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

import logging
import os
import tempfile
from contextlib import contextmanager
from typing import Iterator, Optional

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
    """Translate a legacy `/output/<subdir>/<filename>` reference into an S3 key."""
    subdir = subdir.strip("/")
    return f"output/{subdir}/{filename.lstrip('/')}"
