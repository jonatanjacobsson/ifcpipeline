"""
Object storage helper (S3-compatible, e.g. MinIO) for the IfcPipeline PoC.

Keeps the public API tiny on purpose:
- `is_enabled()`            → is USE_OBJECT_STORAGE=true?
- `get_client()`            → lazily-built boto3 S3 client
- `bucket_name()`           → configured bucket
- `download_to_tempfile()`  → context manager yielding a local path for a key
- `upload_from_path()`      → push a local file up at `key`
- `object_exists()`         → head-object helper
- `head_metadata()`         → HEAD that also returns sha256 / version_id
- `head_version_id()`       → resolve current VersionId for a key
- `ensure_bucket()`         → create bucket if missing (idempotent)

Checksum handling is controlled by S3_CHECKSUM_MODE:
- "native" (default): ask MinIO to compute SHA256 server-side via
  `ChecksumAlgorithm=SHA256`, then read it back from the head response.
  We force single-part uploads so the returned checksum is the whole-object
  SHA256 rather than a multipart composite.
- "app": fall back to Python-side hashing (HashingReader / sha256_of_path).
  Kept for backends that don't support S3 additional checksums.

Keys are S3 object keys (e.g. "uploads/model.ifc"). Path-style addressing is
used so MinIO works out-of-the-box without DNS gymnastics.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import re
import socket
import tempfile
import threading
import time
import unicodedata
from contextlib import contextmanager
from typing import Any, Dict, Iterable, Iterator, Optional, Tuple

logger = logging.getLogger(__name__)

_client = None  # module-level cache
_shadow_client = None  # module-level cache for the SeaweedFS pilot shadow
_shadow_counter_lock = threading.Lock()


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


# --------------------------------------------------------------------------- #
# Shadow / dual-write (SeaweedFS pilot)                                       #
# --------------------------------------------------------------------------- #
#
# When S3_SHADOW_ENDPOINT_URL is non-empty every successful PUT through the
# three centralised upload paths below also writes to the shadow backend on a
# best-effort basis. Shadow failures are caught, logged, and counted; they
# NEVER propagate. The shadow result (sha256/size/version_id) is returned to
# the caller under a "shadow" key so the audit-DB code can stash
# `shadow_version_id` in `object_versions.metadata`.
#
# This is wired up for the parallel-soak pilot — see SEAWEEDFS_PILOT.md. The
# kill switch is `S3_SHADOW_ENDPOINT_URL=` empty + recreate the production
# services; the dual-write path then short-circuits at `_shadow_enabled()`.


def _shadow_enabled() -> bool:
    return bool((os.environ.get("S3_SHADOW_ENDPOINT_URL") or "").strip())


def _shadow_endpoint_url() -> Optional[str]:
    val = (os.environ.get("S3_SHADOW_ENDPOINT_URL") or "").strip()
    return val or None


def _shadow_bucket_name() -> str:
    return os.environ.get("S3_SHADOW_BUCKET") or bucket_name()


def _shadow_region() -> str:
    return os.environ.get("S3_SHADOW_REGION", "us-east-1")


def _shadow_reports_dir() -> str:
    return os.environ.get("S3_SHADOW_REPORTS_DIR", "/reports")


def get_shadow_client():
    """Return a cached boto3 client signed against the shadow endpoint.

    Mirrors `get_client()` / `_get_presign_client()` so the rest of the code
    can keep using the primary client without worrying about which backend
    it's talking to.
    """
    global _shadow_client
    if _shadow_client is not None:
        return _shadow_client
    if not _shadow_enabled():
        return None

    import boto3
    from botocore.config import Config

    _shadow_client = boto3.client(
        "s3",
        endpoint_url=_shadow_endpoint_url(),
        region_name=_shadow_region(),
        aws_access_key_id=os.environ.get("S3_SHADOW_ACCESS_KEY"),
        aws_secret_access_key=os.environ.get("S3_SHADOW_SECRET_KEY"),
        config=Config(
            s3={"addressing_style": "path"},
            signature_version="s3v4",
            connect_timeout=5,
            read_timeout=30,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )
    return _shadow_client


def _record_shadow_metric(
    *,
    outcome: str,
    operation: str,
    key: str,
    bucket: str,
    elapsed_ms: float,
    size_bytes: Optional[int] = None,
    error_class: Optional[str] = None,
) -> None:
    """Append a one-line structured log entry and bump the counters file.

    `outcome` is one of `success` / `failure`. The counters file at
    `<reports_dir>/shadow-counter.json` is the source of truth for the parity
    monitor's "cumulative shadow success/failure" panel; the log line is
    convenient for Dozzle scraping.
    """
    logger.info(
        "shadow.metric outcome=%s op=%s bucket=%s key=%s elapsed_ms=%.1f size=%s err=%s",
        outcome, operation, bucket, key, elapsed_ms,
        size_bytes if size_bytes is not None else "-",
        error_class or "-",
    )
    reports_dir = _shadow_reports_dir()
    path = os.path.join(reports_dir, "shadow-counter.json")
    tmp = path + ".tmp." + str(os.getpid())
    with _shadow_counter_lock:
        try:
            if not os.path.isdir(reports_dir):
                # Reports dir may not be mounted (e.g. unit tests). Skip silently.
                return
            try:
                with open(path, "r", encoding="utf-8") as f:
                    counters = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                counters = {
                    "started_at": time.time(),
                    "success": 0,
                    "failure": 0,
                    "sum_elapsed_ms": 0.0,
                    "by_op": {},
                    "last_failure": None,
                    "last_failure_at": None,
                    "host": socket.gethostname(),
                }
            counters[outcome] = int(counters.get(outcome, 0)) + 1
            counters["sum_elapsed_ms"] = float(counters.get("sum_elapsed_ms", 0.0)) + elapsed_ms
            by_op = counters.setdefault("by_op", {})
            op_bucket = by_op.setdefault(operation, {"success": 0, "failure": 0})
            op_bucket[outcome] = int(op_bucket.get(outcome, 0)) + 1
            counters["updated_at"] = time.time()
            if outcome == "failure":
                counters["last_failure"] = {
                    "key": key,
                    "operation": operation,
                    "error_class": error_class,
                }
                counters["last_failure_at"] = time.time()
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(counters, f)
            os.replace(tmp, path)
        except Exception as e:
            logger.debug("shadow metric counter write failed: %s", e)
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass


def _shadow_head(key: str, bucket: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Best-effort HEAD against the shadow backend. Returns the minimal
    metadata dict the dual-write callers care about, or None on any error."""
    client = get_shadow_client()
    if client is None:
        return None
    bucket = bucket or _shadow_bucket_name()
    try:
        resp = client.head_object(Bucket=bucket, Key=key, ChecksumMode="ENABLED")
    except Exception:
        return None
    return {
        "size_bytes": resp.get("ContentLength"),
        "sha256": _b64_sha256_to_hex(resp.get("ChecksumSHA256")),
        "version_id": resp.get("VersionId"),
        "etag": (resp.get("ETag") or "").strip('"'),
    }


def _shadow_put_from_path(
    local_path: str,
    key: str,
    *,
    content_type: Optional[str] = None,
    bucket: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Mirror a local file to the shadow backend. Never raises."""
    if not _shadow_enabled():
        return None
    bucket = bucket or _shadow_bucket_name()
    client = get_shadow_client()
    if client is None:
        return None
    extra: Dict[str, Any] = {}
    if content_type:
        extra["ContentType"] = content_type
    started = time.perf_counter()
    try:
        client.upload_file(
            Filename=local_path,
            Bucket=bucket,
            Key=key,
            ExtraArgs=extra or None,
            Config=_transfer_config(),
        )
        head = _shadow_head(key, bucket=bucket) or {}
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        _record_shadow_metric(
            outcome="success",
            operation="put_from_path",
            key=key,
            bucket=bucket,
            elapsed_ms=elapsed_ms,
            size_bytes=head.get("size_bytes"),
        )
        return {
            "s3_uri": f"s3://{bucket}/{key}",
            "bucket": bucket,
            "object_key": key,
            "sha256": head.get("sha256"),
            "size_bytes": head.get("size_bytes"),
            "version_id": head.get("version_id"),
        }
    except Exception as e:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        logger.warning(
            "shadow upload_from_path failed for s3://%s/%s: %s", bucket, key, e
        )
        _record_shadow_metric(
            outcome="failure",
            operation="put_from_path",
            key=key,
            bucket=bucket,
            elapsed_ms=elapsed_ms,
            error_class=type(e).__name__,
        )
        return None


def _shadow_put_fileobj(
    fileobj,
    key: str,
    *,
    content_type: Optional[str] = None,
    bucket: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Mirror a seekable fileobj (post-seek(0)) to the shadow backend.
    Never raises. The caller is responsible for putting the fileobj in the
    right position before calling.
    """
    if not _shadow_enabled():
        return None
    bucket = bucket or _shadow_bucket_name()
    client = get_shadow_client()
    if client is None:
        return None
    extra: Dict[str, Any] = {}
    if content_type:
        extra["ContentType"] = content_type
    started = time.perf_counter()
    try:
        client.upload_fileobj(
            fileobj, bucket, key,
            ExtraArgs=extra or None,
            Config=_transfer_config(),
        )
        head = _shadow_head(key, bucket=bucket) or {}
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        _record_shadow_metric(
            outcome="success",
            operation="put_fileobj",
            key=key,
            bucket=bucket,
            elapsed_ms=elapsed_ms,
            size_bytes=head.get("size_bytes"),
        )
        return {
            "s3_uri": f"s3://{bucket}/{key}",
            "bucket": bucket,
            "object_key": key,
            "sha256": head.get("sha256"),
            "size_bytes": head.get("size_bytes"),
            "version_id": head.get("version_id"),
        }
    except Exception as e:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        logger.warning(
            "shadow upload_fileobj failed for s3://%s/%s: %s", bucket, key, e
        )
        _record_shadow_metric(
            outcome="failure",
            operation="put_fileobj",
            key=key,
            bucket=bucket,
            elapsed_ms=elapsed_ms,
            error_class=type(e).__name__,
        )
        return None


def _merge_shadow_into_metadata(
    metadata: Optional[Dict[str, Any]],
    shadow: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Merge shadow_version_id / shadow_sha256 / shadow_size_bytes into an
    audit-metadata dict so it lands in object_versions.metadata."""
    if not shadow:
        return metadata
    md = dict(metadata or {})
    md["shadow_version_id"] = shadow.get("version_id")
    md["shadow_sha256"] = shadow.get("sha256")
    md["shadow_size_bytes"] = shadow.get("size_bytes")
    md["shadow_bucket"] = shadow.get("bucket")
    md["shadow_object_key"] = shadow.get("object_key")
    return md


def object_exists(key: str, bucket: Optional[str] = None, version_id: Optional[str] = None) -> bool:
    bucket = bucket or bucket_name()
    try:
        kwargs: Dict[str, Any] = {"Bucket": bucket, "Key": key}
        if version_id:
            kwargs["VersionId"] = version_id
        get_client().head_object(**kwargs)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Checksum + versioning helpers                                              #
# --------------------------------------------------------------------------- #


def _checksum_mode() -> str:
    return os.environ.get("S3_CHECKSUM_MODE", "native").lower()


def _b64_sha256_to_hex(b64: Optional[str]) -> Optional[str]:
    """Convert MinIO's base64-encoded SHA256 checksum back to a 64-char hex
    string. Returns None if the checksum is missing or is a multipart
    composite (contains '-'). We force single-part uploads in `_transfer_config`
    so composites shouldn't occur in practice, but we still guard for them."""
    if not b64 or "-" in b64:
        return None
    try:
        raw = base64.b64decode(b64, validate=True)
        if len(raw) != 32:
            return None
        return binascii.hexlify(raw).decode("ascii")
    except Exception:
        return None


def _hex_sha256_to_b64(hex_digest: str) -> str:
    return base64.b64encode(binascii.unhexlify(hex_digest)).decode("ascii")


def _transfer_config():
    """Force single-part uploads so the server-side ChecksumSHA256 is the
    whole-object hash (not a per-part composite). 5 GiB covers every IFC
    we realistically handle; anything larger falls back to multipart and the
    native checksum becomes a composite — we transparently re-hash in that
    case (see `head_metadata`)."""
    from boto3.s3.transfer import TransferConfig
    return TransferConfig(multipart_threshold=5 * 1024 ** 3)


def head_metadata(
    key: str,
    *,
    version_id: Optional[str] = None,
    bucket: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """HEAD the object and return a dict with `size_bytes`, `sha256` (hex or
    None), `version_id`, `content_type`. Returns None if the key doesn't
    exist. Requests `ChecksumMode=ENABLED` so native SHA256 is surfaced."""
    bucket = bucket or bucket_name()
    kwargs: Dict[str, Any] = {"Bucket": bucket, "Key": key, "ChecksumMode": "ENABLED"}
    if version_id:
        kwargs["VersionId"] = version_id
    try:
        resp = get_client().head_object(**kwargs)
    except Exception as e:
        logger.debug("head_metadata: s3://%s/%s missing: %s", bucket, key, e)
        return None
    return {
        "size_bytes": resp.get("ContentLength"),
        "sha256": _b64_sha256_to_hex(resp.get("ChecksumSHA256")),
        "checksum_sha256_b64": resp.get("ChecksumSHA256"),
        "version_id": resp.get("VersionId"),
        "content_type": resp.get("ContentType"),
        "etag": (resp.get("ETag") or "").strip('"'),
    }


def head_version_id(key: str, bucket: Optional[str] = None) -> Optional[str]:
    """Return the current VersionId of `key`, or None if the bucket is
    unversioned / the key doesn't exist."""
    info = head_metadata(key, bucket=bucket)
    return info.get("version_id") if info else None


def _sha256_of_local(path: str) -> Tuple[str, int]:
    """Compute sha256+size by streaming the local file from disk. Used only
    when the native checksum came back missing/composite."""
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_HASH_CHUNK), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def upload_from_path(
    local_path: str,
    key: str,
    bucket: Optional[str] = None,
    *,
    content_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Upload a local file to `key` and return a dict with `s3_uri`,
    `sha256`, `size_bytes`, `version_id`.

    In native checksum mode the sha256 is computed server-side by MinIO;
    the old Tuple-returning signature callers relied on is now `result["sha256"]`
    plus `result["size_bytes"]`. For backwards compatibility the result dict
    also contains `s3_uri` as its first string-ish field so ``str(result)`` is
    still a debuggable representation.
    """
    bucket = bucket or bucket_name()
    extra: Dict[str, Any] = {}
    if content_type:
        extra["ContentType"] = content_type
    mode = _checksum_mode()
    if mode == "native":
        extra["ChecksumAlgorithm"] = "SHA256"
    logger.info("PUT s3://%s/%s from %s (mode=%s)", bucket, key, local_path, mode)
    get_client().upload_file(
        Filename=local_path,
        Bucket=bucket,
        Key=key,
        ExtraArgs=extra or None,
        Config=_transfer_config(),
    )
    sha256: Optional[str] = None
    size_bytes: Optional[int] = None
    version_id: Optional[str] = None
    if mode == "native":
        info = head_metadata(key, bucket=bucket)
        if info:
            sha256 = info["sha256"]
            size_bytes = info["size_bytes"]
            version_id = info["version_id"]
        if not sha256:
            logger.warning(
                "upload_from_path: native sha256 unavailable for s3://%s/%s; "
                "computing app-side as fallback",
                bucket, key,
            )
            sha256, size_bytes = _sha256_of_local(local_path)
    else:
        sha256, size_bytes = _sha256_of_local(local_path)
        # Still grab VersionId if the bucket is versioned.
        info = head_metadata(key, bucket=bucket)
        if info:
            version_id = info["version_id"]
    shadow = _shadow_put_from_path(local_path, key, content_type=content_type)
    return {
        "s3_uri": f"s3://{bucket}/{key}",
        "bucket": bucket,
        "object_key": key,
        "sha256": sha256,
        "size_bytes": size_bytes,
        "version_id": version_id,
        "shadow": shadow,
    }


@contextmanager
def download_to_tempfile(
    key: str,
    suffix: str = "",
    bucket: Optional[str] = None,
    version_id: Optional[str] = None,
) -> Iterator[str]:
    """Download the object at `key` to a NamedTemporaryFile and yield its path.
    The file is removed on exit. Suffix (e.g. ".ifc") helps libraries that
    sniff the file type from the extension. When `version_id` is set, the
    pinned version is fetched instead of the current one."""
    bucket = bucket or bucket_name()
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        extra: Optional[Dict[str, Any]] = {"VersionId": version_id} if version_id else None
        logger.info(
            "GET s3://%s/%s%s → %s",
            bucket, key, f"?versionId={version_id}" if version_id else "", tmp_path,
        )
        get_client().download_file(
            Bucket=bucket, Key=key, Filename=tmp_path, ExtraArgs=extra,
        )
        yield tmp_path
    finally:
        try:
            os.remove(tmp_path)
        except FileNotFoundError:
            pass


def pin_for(request: Any, raw_filename: Optional[str] = None) -> Optional[str]:
    """Resolve the pinned MinIO VersionId for an input referenced by a
    request model (e.g. one inheriting `shared.classes.VersionPinOptional`),
    or any object with `input_version_id` / `input_version_ids` /
    `input_audit_id` attributes).

    Precedence:
      1. `request.input_version_ids[raw_filename]` when `raw_filename` is set
         and the map contains that exact key (multi-input jobs).
      2. `request.input_version_id` (single primary pin).
      3. `request.input_audit_id` → Postgres `object_versions.id` lookup;
         the row's `object_key` must match ``normalize_input_key(raw_filename)``
         when `raw_filename` is set (otherwise the audit pin is ignored with a
         warning). When `raw_filename` is None, the row's `version_id` is used
         if present.
      4. None (no pin; use HEAD).

    `raw_filename` is the filename string as it appears in the original
    request (e.g. `request.filename`, `request.old_file`) — not the S3 key
    derived from it. This is what the gateway keys the pin map by.
    """
    if request is None:
        return None
    if raw_filename is not None:
        pv = request.get("input_version_ids") if isinstance(request, dict) else getattr(
            request, "input_version_ids", None
        )
        if pv and raw_filename in pv:
            return pv[raw_filename]
    vid = (
        request.get("input_version_id")
        if isinstance(request, dict)
        else getattr(request, "input_version_id", None)
    )
    if isinstance(vid, str):
        vid = vid.strip()
    if vid:
        return str(vid)

    audit_raw = (
        request.get("input_audit_id")
        if isinstance(request, dict)
        else getattr(request, "input_audit_id", None)
    )
    try:
        audit_id = int(audit_raw) if audit_raw is not None else 0
    except (TypeError, ValueError):
        audit_id = 0
    if audit_id > 0:
        from shared import audit_db

        row = audit_db.fetch_version_pin_by_audit_id(audit_id)
        if row and row.get("version_id"):
            if raw_filename is None:
                return row["version_id"]
            wanted = normalize_input_key(raw_filename)
            if row.get("object_key") == wanted:
                return row["version_id"]
            logger.warning(
                "input_audit_id=%s points at object_key=%s, expected %s for pin_for(raw_filename=%s); ignoring audit pin",
                audit_id,
                row.get("object_key"),
                wanted,
                raw_filename,
            )
    return None


def download_to_path(
    key: str,
    local_path: str,
    *,
    version_id: Optional[str] = None,
    bucket: Optional[str] = None,
) -> None:
    """Helper matching `get_client().download_file` but with version pinning
    and consistent logging. Workers call this to honor a pinned version."""
    bucket = bucket or bucket_name()
    extra: Optional[Dict[str, Any]] = {"VersionId": version_id} if version_id else None
    logger.info(
        "GET s3://%s/%s%s → %s",
        bucket, key, f"?versionId={version_id}" if version_id else "", local_path,
    )
    get_client().download_file(
        Bucket=bucket, Key=key, Filename=local_path, ExtraArgs=extra,
    )


def _strip_s3_scheme(path: str) -> str:
    """If `path` is an `s3://<bucket>/<key>` URI, return just `<key>`.
    Otherwise return `path` unchanged.

    Every path-normalisation helper in this module delegates here so that
    any user-supplied path — `/uploads/foo.ifc`, `uploads/foo.ifc`,
    `foo.ifc`, or the canonical `s3://<bucket>/uploads/foo.ifc` URI that
    gateway endpoints (`/upload/*`, `/download-from-url`) and worker
    outputs now return — collapses to the same S3 key.
    """
    if path.startswith("s3://"):
        _, _, rest = path.partition("s3://")
        _, _, key = rest.partition("/")
        return key
    return path


# Max stem length so ``uploads/<basename>`` stays well under S3's 1024-byte
# object key limit even with long extensions.
_SAFE_UPLOAD_STEM_MAX = 180


def resolve_upload_filename(original: str) -> tuple[str, str]:
    """Split a client-supplied name into ``(original_basename, storage_basename)``.

    ``original_basename`` is the human-readable SharePoint / ACC name (may
    contain spaces and Unicode). ``storage_basename`` is the ASCII-safe key
    fragment produced by :func:`safe_upload_basename` and used under
    ``uploads/`` in MinIO.
    """
    original_basename = os.path.basename(str(original or "").strip())
    if not original_basename:
        raise ValueError("Filename cannot be empty")
    storage_basename = safe_upload_basename(original_basename)
    return original_basename, storage_basename


def build_upload_key_from_original(original: str) -> tuple[str, str, str]:
    """Return ``(original_basename, storage_basename, upload_key)``."""
    original_basename, storage_basename = resolve_upload_filename(original)
    return original_basename, storage_basename, build_upload_key(storage_basename)


def safe_upload_basename(original_basename: str) -> str:
    """Return an ASCII-safe filename for uploads, preserving the original name.

    NFC-normalizes the name stem, NFKD-folds to ASCII (so letters like å/ä/ö
    become ASCII where decomposition allows), replaces other non-alphanumeric
    runs (except ``._-``) with a single underscore. The file extension is taken
    from the client basename and lower-cased (e.g. ``.BCFZIP`` → ``.bcfzip``).

    The result uses only ``[A-Za-z0-9._-]`` and underscores, safe for shells,
    presigned URLs, and workflow tools that mishandle Unicode or spaces in
    object keys. Uploading the same filename always produces the same key so
    repeated uploads overwrite the previous version in place.
    """
    base = os.path.basename(original_basename)
    stem, ext = os.path.splitext(base)
    ext_lower = ext.lower() if ext else ""
    stem = unicodedata.normalize("NFC", stem)
    stem_ascii = (
        unicodedata.normalize("NFKD", stem)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    stem_safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stem_ascii)
    stem_safe = re.sub(r"_+", "_", stem_safe).strip("._-")
    if not stem_safe:
        stem_safe = "file"
    if len(stem_safe) > _SAFE_UPLOAD_STEM_MAX:
        stem_safe = stem_safe[:_SAFE_UPLOAD_STEM_MAX].rstrip("._-") or "file"
    return f"{stem_safe}{ext_lower}"


def build_upload_key(filename: str) -> str:
    """Translate a legacy `/uploads/<filename>` reference (or an `s3://`
    URI pointing into the bucket) into an S3 key."""
    filename = _strip_s3_scheme(filename).lstrip("/")
    if filename.startswith("uploads/"):
        return filename
    return f"uploads/{filename}"


def build_output_key(subdir: str, filename: str) -> str:
    """Translate a legacy `/output/<subdir>/<filename>` reference into an S3 key.

    Idempotent against callers that already pass in a fully-qualified path:
    ``build_output_key('csv', 'output/csv/foo.csv')`` yields
    ``output/csv/foo.csv`` rather than ``output/csv/output/csv/foo.csv`` (the
    OG filesystem code happily produced the doubled path because nothing ever
    looked at the result). `s3://bucket/<key>` URIs are accepted and
    collapsed to their `<key>` form.
    """
    subdir = subdir.strip("/")
    name = _strip_s3_scheme(filename).lstrip("/")
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
    - `s3://bucket/uploads/foo.ifc` → `uploads/foo.ifc`
    - `/uploads/foo.ifc`            → `uploads/foo.ifc`
    - `uploads/foo.ifc`             → `uploads/foo.ifc`
    - `/output/diff/prev.json`      → `output/diff/prev.json`
    - `foo.ifc`                     → `uploads/foo.ifc`  (default)
    - `subdir/foo.ifc`              → `subdir/foo.ifc`   (left alone)
    """
    p = _strip_s3_scheme(path).lstrip("/")
    if "/" in p:
        return p
    return f"uploads/{p}"


def normalize_output_key(path: str, default_subdir: str) -> str:
    """Normalize a user-supplied output path into an S3 key.

    Bare filenames are placed under `output/<default_subdir>/<name>`.
    Paths already containing a separator are trusted and only have the leading
    slash stripped (e.g. `/output/converted/x.glb` → `output/converted/x.glb`).
    `s3://bucket/<key>` URIs are stripped to their `<key>` form first so
    end-to-end S3 round-trips work (e.g. writing to the same key an upstream
    node just produced).
    """
    p = _strip_s3_scheme(path).lstrip("/")
    if "/" in p:
        return p
    return f"output/{default_subdir.strip('/')}/{p}"


def public_endpoint_url() -> Optional[str]:
    """The S3 endpoint reachable by end-users (outside Docker).

    When S3_PUBLIC_ENDPOINT_URL is set (e.g. https://minio-api.byggstyrning.se)
    presigned URLs are signed against it directly so the HMAC covers the
    correct host. Falls back to S3_ENDPOINT_URL."""
    return os.environ.get("S3_PUBLIC_ENDPOINT_URL") or _endpoint_url()


_presign_client = None  # module-level cache, separate from the internal client


def _get_presign_client():
    """Return a boto3 client configured against the *public* endpoint so that
    presigned URLs are valid for the host callers actually connect to.

    The AWS SigV4 signature covers the Host header, so a URL signed against
    http://minio:9000 and then string-rewritten to https://minio-api.example.com
    will always fail with SignatureDoesNotMatch. Signing against the public
    endpoint from the start avoids this entirely."""
    global _presign_client
    if _presign_client is not None:
        return _presign_client

    import boto3
    from botocore.config import Config

    pub = public_endpoint_url()
    _presign_client = boto3.client(
        "s3",
        endpoint_url=pub,
        region_name=_region(),
        aws_access_key_id=os.environ.get("S3_ACCESS_KEY"),
        aws_secret_access_key=os.environ.get("S3_SECRET_KEY"),
        config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
    )
    return _presign_client


def presigned_get_url(key: str, expires_in: int = 1800, bucket: Optional[str] = None) -> str:
    """Return a pre-signed GET URL signed against the internal S3 endpoint.
    Only use this when the caller is inside the Docker network."""
    bucket = bucket or bucket_name()
    return get_client().generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires_in,
    )


def presigned_get_url_public(
    key: str,
    expires_in: int = 1800,
    bucket: Optional[str] = None,
    *,
    response_content_disposition: Optional[str] = None,
) -> str:
    """Return a pre-signed GET URL signed against the *public* S3 endpoint.

    The signature is generated with the public endpoint as the host so it is
    valid when followed by a browser or n8n running outside the Docker network.

    When ``response_content_disposition`` is set, it is forwarded to S3/MinIO so
    the GET response includes ``Content-Disposition`` (e.g. real ``.ifc`` name
    for browser downloads and fetch() consumers).
    """
    bucket = bucket or bucket_name()
    params: Dict[str, Any] = {"Bucket": bucket, "Key": key}
    if response_content_disposition:
        params["ResponseContentDisposition"] = response_content_disposition
    return _get_presign_client().generate_presigned_url(
        ClientMethod="get_object",
        Params=params,
        ExpiresIn=expires_in,
    )


# --------------------------------------------------------------------------- #
# Audit helpers: sha256 + one-shot "upload + record lineage"                  #
# --------------------------------------------------------------------------- #


_HASH_CHUNK = 1 << 20  # 1 MiB


def sha256_of_path(path: str) -> Tuple[str, int]:
    """Stream a file from disk and return `(hex_digest, size_bytes)`.
    Kept as a fallback for S3_CHECKSUM_MODE=app and direct callers."""
    return _sha256_of_local(path)


class HashingReader:
    """File-like wrapper that computes sha256 and byte count on the fly.

    Only used when `S3_CHECKSUM_MODE=app`. In the default `native` mode the
    gateway hands the raw request body straight to boto3 and the sha256 is
    recovered from MinIO's `ChecksumSHA256` response header — no per-byte
    Python work on the request path.
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


def upload_fileobj_and_hash(
    fileobj,
    key: str,
    bucket: Optional[str] = None,
    content_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Upload a streaming body to `key` and return a dict with `sha256`,
    `size_bytes`, `version_id`.

    In `native` mode MinIO computes the SHA256 on the server; we read it back
    via `head_object(ChecksumMode=ENABLED)`. In `app` mode we wrap the reader
    with `HashingReader` and compute it Python-side.

    When `_shadow_enabled()`, the request body is spooled to a
    `SpooledTemporaryFile` so the same bytes can be replayed against the
    shadow backend after the primary write completes. The 64 MiB threshold
    keeps small uploads entirely in RAM; larger ones overflow to a temp file.
    The shadow result is returned under the `shadow` key.
    """
    bucket = bucket or bucket_name()
    extra_args: Dict[str, Any] = {}
    if content_type:
        extra_args["ContentType"] = content_type
    mode = _checksum_mode()
    sha256: Optional[str] = None
    size_bytes: Optional[int] = None
    version_id: Optional[str] = None
    shadow_payload: Optional[Dict[str, Any]] = None

    if not _shadow_enabled():
        # Fast path: unchanged behavior from before the pilot. Single PUT
        # straight from the request body.
        if mode == "native":
            extra_args["ChecksumAlgorithm"] = "SHA256"
            logger.info("PUT s3://%s/%s (streaming, native sha256)", bucket, key)
            get_client().upload_fileobj(
                fileobj, bucket, key,
                ExtraArgs=extra_args or None,
                Config=_transfer_config(),
            )
            info = head_metadata(key, bucket=bucket)
            if info:
                sha256 = info["sha256"]
                size_bytes = info["size_bytes"]
                version_id = info["version_id"]
            if not sha256:
                logger.error(
                    "upload_fileobj_and_hash: native sha256 missing for s3://%s/%s "
                    "and the fileobj was already consumed — returning None. "
                    "Switch S3_CHECKSUM_MODE=app if your backend doesn't support "
                    "additional checksums.",
                    bucket, key,
                )
        else:
            reader = HashingReader(fileobj)
            logger.info("PUT s3://%s/%s (streaming, app sha256)", bucket, key)
            get_client().upload_fileobj(
                reader, bucket, key,
                ExtraArgs=extra_args or None,
                Config=_transfer_config(),
            )
            sha256 = reader.hexdigest
            size_bytes = reader.size
            info = head_metadata(key, bucket=bucket)
            if info:
                version_id = info["version_id"]
        return {
            "sha256": sha256,
            "size_bytes": size_bytes,
            "version_id": version_id,
            "shadow": None,
        }

    # Shadow-enabled path: drain the body to a temp file on disk so we can
    # do two completely independent PUTs (primary then shadow) without
    # boto3 closing or seeking into the source twice. Slight extra cost
    # (one disk write on the gateway tempfs); removable by clearing
    # S3_SHADOW_ENDPOINT_URL. We compute sha256 + size during the drain so
    # nothing has to read the body a third time.
    sha_hash = hashlib.sha256()
    size = 0
    tmp = tempfile.NamedTemporaryFile(delete=False, prefix="dualwrite-", suffix=".bin")
    tmp_path = tmp.name
    try:
        try:
            while True:
                chunk = fileobj.read(1 << 20)
                if not chunk:
                    break
                tmp.write(chunk)
                sha_hash.update(chunk)
                size += len(chunk)
        finally:
            tmp.close()
        size_bytes = size
        sha256 = sha_hash.hexdigest()

        if mode == "native":
            extra_args["ChecksumAlgorithm"] = "SHA256"
        logger.info(
            "PUT s3://%s/%s (streaming, dualwrite, app sha256=%s size=%d)",
            bucket, key, sha256[:12], size,
        )
        get_client().upload_file(
            Filename=tmp_path,
            Bucket=bucket,
            Key=key,
            ExtraArgs=extra_args or None,
            Config=_transfer_config(),
        )
        info = head_metadata(key, bucket=bucket)
        if info:
            version_id = info["version_id"]
            if mode == "native" and info.get("sha256"):
                sha256 = info["sha256"]
                size_bytes = info["size_bytes"] or size_bytes

        # Best-effort shadow PUT against the same tmp_path. Never raises.
        shadow_payload = _shadow_put_from_path(
            tmp_path, key, content_type=content_type
        )
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    return {
        "sha256": sha256,
        "size_bytes": size_bytes,
        "version_id": version_id,
        "shadow": shadow_payload,
    }


def upload_and_audit(
    local_path: str,
    *,
    key: str,
    operation: str,
    worker: str,
    job_id: Optional[str],
    parents: Iterable[Tuple[str, str]] = (),
    parent_version_ids: Optional[Dict[str, str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    content_type: Optional[str] = None,
    bucket: Optional[str] = None,
    guid_role: Optional[str] = "derived",
) -> Dict[str, Any]:
    """Upload a local file to S3 **and** record a derivative lineage row.

    `parents` is an iterable of `(role, parent_key)` pairs. Missing parents
    (not previously audited) are tolerated by `audit_db.record_derivative` —
    the version row is still written.

    `parent_version_ids` optionally maps `parent_key -> version_id` so the
    lineage row can pin to the exact bytes the worker consumed. When provided
    the parent_version_id is also embedded in the child's metadata.

    `guid_role` controls the asynchronous GUID-index enqueue:
      - str (default "derived"): stamp rows with this role in object_guids.
        ifcpatch passes "patched", ifclite passes "split", etc.
      - None: skip GUID extraction entirely (used by ifctester / ifcclash,
        which write their own specialised rows into tester_results /
        clash_pairs instead).

    Returns a dict with the S3 URI, sha256, size, version_id, and the audit
    version id (which may be None if the DB is unreachable).
    """
    bucket = bucket or bucket_name()
    put = upload_from_path(local_path, key, bucket=bucket, content_type=content_type)
    sha256 = put["sha256"]
    size = put["size_bytes"]
    version_id = put["version_id"]
    shadow = put.get("shadow")

    # Embed parent version pins into the child's metadata so ancestor walks
    # can reproduce the exact inputs without a separate table.
    md = dict(metadata or {})
    if parent_version_ids:
        md.setdefault("parent_version_ids", {}).update(parent_version_ids)
    # Stash the shadow backend's VersionId in metadata for end-of-pilot
    # parity verification. JSONB column → no schema change required.
    md = _merge_shadow_into_metadata(md, shadow) or md

    audit_id: Optional[int] = None
    try:
        from . import audit_db  # local import so pure-S3 callers don't need psycopg2 loaded
        audit_id = audit_db.record_derivative(
            bucket=bucket,
            object_key=key,
            sha256=sha256,
            size_bytes=size,
            version_id=version_id,
            operation=operation,
            worker=worker,
            job_id=job_id,
            parents=list(parents),
            parent_version_ids=parent_version_ids or {},
            content_type=content_type,
            metadata=md,
        )
    except Exception as e:  # audit must never break the pipeline
        logger.warning("audit: upload_and_audit could not record lineage: %s", e)

    # Enqueue GUID indexing (async) if configured and the caller opted in.
    if guid_role is not None:
        try:
            _maybe_enqueue_guid_index(
                audit_id=audit_id,
                object_key=key,
                version_id=version_id,
                role_hint=guid_role,
            )
        except Exception as e:
            logger.warning("guid-index: could not enqueue for %s: %s", key, e)

    return {
        "s3_uri": put["s3_uri"],
        "bucket": bucket,
        "object_key": key,
        "sha256": sha256,
        "size_bytes": size,
        "version_id": version_id,
        "audit_id": audit_id,
        "shadow": shadow,
    }


# --------------------------------------------------------------------------- #
# GUID indexer hook (defined here so every upload path picks it up)          #
# --------------------------------------------------------------------------- #


def guid_index_mode() -> str:
    """`sync`, `async`, or `off`. Defaults to `off` so upload paths stay
    silent until the `guid-index-worker` service is running. The compose
    stack flips this to `async` for the services that should feed the
    index (api-gateway + all workers)."""
    return os.environ.get("GUID_INDEX_MODE", "off").lower()


def _maybe_enqueue_guid_index(
    *,
    audit_id: Optional[int],
    object_key: str,
    version_id: Optional[str],
    role_hint: str,
) -> None:
    """Fire-and-forget GUID indexing.

    - `off` : do nothing.
    - `async`: enqueue on the `guid_index_queue` (requires redis+rq).
    - `sync` : run in-process (used by smoke tests and small installs).

    Audit rows without an id (DB unavailable) are skipped entirely — there is
    nothing to anchor the GUID rows to.
    """
    mode = guid_index_mode()
    if mode == "off" or not audit_id:
        return
    if mode == "sync":
        try:
            from . import guid_extract, audit_db  # local imports
            pairs = list(_extract_for_sync(object_key, version_id, role_hint))
            if pairs:
                audit_db.record_guids(audit_id, pairs)
        except Exception as e:
            logger.warning("guid-index (sync) failed for %s: %s", object_key, e)
        return

    # async
    try:
        from redis import Redis
        from rq import Queue
        redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
        redis = Redis.from_url(redis_url)
        q = Queue("guid_index", connection=redis)
        q.enqueue(
            "tasks.index_object",
            audit_id,
            object_key,
            version_id,
            role_hint,
            job_timeout=60 * 30,
        )
    except Exception as e:
        logger.warning("guid-index (async) enqueue failed for %s: %s", object_key, e)


def _extract_for_sync(object_key: str, version_id: Optional[str], role: str):
    """Inline GUID extraction for GUID_INDEX_MODE=sync. Mirrors the worker's
    extension-based dispatch but runs in-process."""
    from . import guid_extract
    ext = os.path.splitext(object_key)[1].lower()
    with download_to_tempfile(object_key, suffix=ext or ".bin", version_id=version_id) as local:
        if role.startswith("diff_") or (ext == ".json" and "diff" in object_key.lower()):
            yield from guid_extract.extract_from_diff_report(local)
            return
        if ext in (".ifc", ".ifczip"):
            base = guid_extract.extract_from_ifc_path(local)
        elif ext == ".json":
            base = guid_extract.extract_from_ifc_json_path(local)
        elif ext in (".csv", ".xlsx"):
            base = guid_extract.extract_from_csv_path(local)
        else:
            return
        for guid, entity, inner_role in base:
            yield (guid, entity, inner_role or role)
