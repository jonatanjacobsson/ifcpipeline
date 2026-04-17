"""
Object storage helper (S3-compatible, e.g. MinIO) for the IfcPipeline PoC.

Keeps the public API tiny on purpose:
- `is_enabled()`            → is USE_OBJECT_STORAGE=true?
- `get_client()`            → lazily-built boto3 S3 client
- `bucket_name()`           → configured bucket
- `download_to_tempfile()`  → context manager yielding a local path for a key
- `upload_from_path()`      → push a local file up at `key`
- `object_exists()`         → head-object helper
- `ensure_bucket()`         → create bucket if missing (idempotent)

Keys are S3 object keys (e.g. "uploads/model.ifc"). Path-style addressing is
used so MinIO works out-of-the-box without DNS gymnastics.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from contextlib import contextmanager
from typing import Any, Dict, Iterable, Iterator, Optional, Tuple

logger = logging.getLogger(__name__)

_client = None  # module-level cache


def is_enabled() -> bool:
    return os.environ.get("USE_OBJECT_STORAGE", "false").lower() in ("1", "true", "yes")


def bucket_name() -> str:
    return os.environ.get("S3_BUCKET", "ifcpipeline")


def _endpoint_url() -> Optional[str]:
    return os.environ.get("S3_ENDPOINT_URL") or None


def _region() -> str:
    return os.environ.get("S3_REGION", "us-east-1")


def get_client():
    """Return a cached boto3 S3 client. Imported lazily so the shared package
    stays importable even when boto3 isn't installed (e.g. in workers that
    don't use object storage yet)."""
    global _client
    if _client is not None:
        return _client

    import boto3  # local import
    from botocore.config import Config

    _client = boto3.client(
        "s3",
        endpoint_url=_endpoint_url(),
        region_name=_region(),
        aws_access_key_id=os.environ.get("S3_ACCESS_KEY"),
        aws_secret_access_key=os.environ.get("S3_SECRET_KEY"),
        config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
    )
    return _client


def ensure_bucket(bucket: Optional[str] = None) -> None:
    bucket = bucket or bucket_name()
    client = get_client()
    try:
        client.head_bucket(Bucket=bucket)
    except Exception:
        logger.info("Creating bucket %s", bucket)
        client.create_bucket(Bucket=bucket)


def object_exists(key: str, bucket: Optional[str] = None) -> bool:
    bucket = bucket or bucket_name()
    try:
        get_client().head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


def upload_from_path(local_path: str, key: str, bucket: Optional[str] = None) -> str:
    bucket = bucket or bucket_name()
    logger.info("PUT s3://%s/%s from %s", bucket, key, local_path)
    get_client().upload_file(Filename=local_path, Bucket=bucket, Key=key)
    return f"s3://{bucket}/{key}"


@contextmanager
def download_to_tempfile(key: str, suffix: str = "", bucket: Optional[str] = None) -> Iterator[str]:
    """Download the object at `key` to a NamedTemporaryFile and yield its path.
    The file is removed on exit. Suffix (e.g. ".ifc") helps libraries that
    sniff the file type from the extension."""
    bucket = bucket or bucket_name()
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        logger.info("GET s3://%s/%s → %s", bucket, key, tmp_path)
        get_client().download_file(Bucket=bucket, Key=key, Filename=tmp_path)
        yield tmp_path
    finally:
        try:
            os.remove(tmp_path)
        except FileNotFoundError:
            pass


def build_upload_key(filename: str) -> str:
    """Translate a legacy `/uploads/<filename>` reference into an S3 key."""
    filename = filename.lstrip("/")
    if filename.startswith("uploads/"):
        return filename
    return f"uploads/{filename}"


def build_output_key(subdir: str, filename: str) -> str:
    """Translate a legacy `/output/<subdir>/<filename>` reference into an S3 key.

    Idempotent against callers that already pass in a fully-qualified path:
    ``build_output_key('csv', 'output/csv/foo.csv')`` yields
    ``output/csv/foo.csv`` rather than ``output/csv/output/csv/foo.csv`` (the
    OG filesystem code happily produced the doubled path because nothing ever
    looked at the result).
    """
    subdir = subdir.strip("/")
    name = filename.lstrip("/")
    prefix = f"output/{subdir}/"
    if name.startswith(prefix):
        return name
    if name.startswith("output/"):
        # Caller supplied a different subdir (e.g. output/other/foo.csv). Trust it.
        return name
    return f"{prefix}{name}"


def normalize_input_key(path: str) -> str:
    """Normalize a user-supplied input path into an S3 key.

    Accepts:
    - `/uploads/foo.ifc`       → `uploads/foo.ifc`
    - `uploads/foo.ifc`        → `uploads/foo.ifc`
    - `/output/diff/prev.json` → `output/diff/prev.json`
    - `foo.ifc`                → `uploads/foo.ifc`  (default)
    - `subdir/foo.ifc`         → `subdir/foo.ifc`   (left alone)
    """
    p = path.lstrip("/")
    if "/" in p:
        return p
    return f"uploads/{p}"


def normalize_output_key(path: str, default_subdir: str) -> str:
    """Normalize a user-supplied output path into an S3 key.

    Bare filenames are placed under `output/<default_subdir>/<name>`.
    Paths already containing a separator are trusted and only have the leading
    slash stripped (e.g. `/output/converted/x.glb` → `output/converted/x.glb`).
    """
    p = path.lstrip("/")
    if "/" in p:
        return p
    return f"output/{default_subdir.strip('/')}/{p}"


def presigned_get_url(key: str, expires_in: int = 1800, bucket: Optional[str] = None) -> str:
    """Return a pre-signed URL for a GET on the given key. The client must be
    able to reach the S3 endpoint (the URL points at whatever S3_ENDPOINT_URL
    is — for MinIO that's the public host the caller can see)."""
    bucket = bucket or bucket_name()
    return get_client().generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires_in,
    )


def public_endpoint_url() -> Optional[str]:
    """Optional override pointing at the *public* S3 endpoint. When set, this
    is what presigned URLs are rewritten against so callers outside the docker
    network can hit MinIO (e.g. `http://localhost:9000`). Falls back to
    `S3_ENDPOINT_URL`."""
    return os.environ.get("S3_PUBLIC_ENDPOINT_URL") or _endpoint_url()


def presigned_get_url_public(key: str, expires_in: int = 1800, bucket: Optional[str] = None) -> str:
    """Like `presigned_get_url` but rewrites the host portion to the public
    endpoint so end-users can follow the link."""
    url = presigned_get_url(key, expires_in=expires_in, bucket=bucket)
    public = public_endpoint_url()
    internal = _endpoint_url()
    if public and internal and public != internal and url.startswith(internal):
        return public.rstrip("/") + url[len(internal.rstrip("/")):]
    return url


# --------------------------------------------------------------------------- #
# Audit helpers: sha256 + one-shot "upload + record lineage"                  #
# --------------------------------------------------------------------------- #


_HASH_CHUNK = 1 << 20  # 1 MiB


def sha256_of_path(path: str) -> Tuple[str, int]:
    """Stream a file from disk and return `(hex_digest, size_bytes)`."""
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_HASH_CHUNK), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


class HashingReader:
    """File-like wrapper that computes sha256 and byte count on the fly.

    Safe to hand to `boto3.client.upload_fileobj` — supports `.read(size)`
    semantics. After the upload completes, `.hexdigest` and `.size` hold the
    final values.
    """

    def __init__(self, fileobj):
        self._fileobj = fileobj
        self._hash = hashlib.sha256()
        self.size = 0

    def read(self, size: int = -1) -> bytes:
        data = self._fileobj.read(size) if size and size > 0 else self._fileobj.read()
        if data:
            self._hash.update(data)
            self.size += len(data)
        return data

    @property
    def hexdigest(self) -> str:
        return self._hash.hexdigest()


def upload_fileobj_and_hash(fileobj, key: str, bucket: Optional[str] = None,
                            content_type: Optional[str] = None) -> Tuple[str, int]:
    """Upload a streaming body to `key` while computing sha256.

    Returns `(sha256_hex, size_bytes)`. Used by the gateway's /upload endpoint
    to avoid re-reading the request body.
    """
    bucket = bucket or bucket_name()
    reader = HashingReader(fileobj)
    extra_args: Dict[str, Any] = {}
    if content_type:
        extra_args["ContentType"] = content_type
    logger.info("PUT s3://%s/%s (streaming+hash)", bucket, key)
    get_client().upload_fileobj(reader, bucket, key, ExtraArgs=extra_args or None)
    return reader.hexdigest, reader.size


def upload_and_audit(
    local_path: str,
    *,
    key: str,
    operation: str,
    worker: str,
    job_id: Optional[str],
    parents: Iterable[Tuple[str, str]] = (),
    metadata: Optional[Dict[str, Any]] = None,
    content_type: Optional[str] = None,
    bucket: Optional[str] = None,
) -> Dict[str, Any]:
    """Upload a local file to S3 **and** record a derivative lineage row.

    `parents` is an iterable of `(role, parent_key)` pairs. Missing parents
    (not previously audited) are tolerated by `audit_db.record_derivative` —
    the version row is still written.

    Returns a dict with the S3 URI, sha256, size, and the audit version id
    (which may be None if the DB is unreachable).
    """
    bucket = bucket or bucket_name()
    s3_uri = upload_from_path(local_path, key, bucket=bucket)
    sha256, size = sha256_of_path(local_path)

    audit_id: Optional[int] = None
    try:
        from . import audit_db  # local import so pure-S3 callers don't need psycopg2 loaded
        audit_id = audit_db.record_derivative(
            bucket=bucket,
            object_key=key,
            sha256=sha256,
            size_bytes=size,
            operation=operation,
            worker=worker,
            job_id=job_id,
            parents=list(parents),
            content_type=content_type,
            metadata=metadata or {},
        )
    except Exception as e:  # audit must never break the pipeline
        logger.warning("audit: upload_and_audit could not record lineage: %s", e)

    return {
        "s3_uri": s3_uri,
        "bucket": bucket,
        "object_key": key,
        "sha256": sha256,
        "size_bytes": size,
        "audit_id": audit_id,
    }
