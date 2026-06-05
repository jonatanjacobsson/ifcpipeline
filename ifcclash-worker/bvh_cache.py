"""Per-element BVH/tessellation cache for ifcclash-worker.

Wraps ifcopenshell 0.8.5's ``HdfSerializer`` + ``Iterator.set_cache(...)`` so
back-to-back clash calls against the same IFC file can skip the OCC/CGAL
tessellation pass that dominates ``CustomClasher.add_collision_objects``
(see ``Tree finished`` timing in tasks.py).

Two storage layers:

  1. **Local cache directory** — ``IFCCLASH_BVH_CACHE_DIR`` (default
     ``/tmp/ifcclash-bvh-cache``). Survives between RQ jobs in the same
     container. Acts as the in-process LRU (the disk *is* the LRU; an
     mtime-based eviction caps total size at ``IFCCLASH_BVH_CACHE_MAX_MB``).
  2. **MinIO** at ``cache/clash-tess/<kernel>/<file_sha>.h5`` — survives
     container restarts. Pulled on local miss, pushed when the local cache
     file grows after a tessellation.

The cache is **per (file_content_sha, kernel)**. Threads, mode, and selector
do NOT enter the key: ``HdfSerializer`` indexes per IFC GlobalId, so a single
cache file is reused across any subset selector against the same file.

The whole module **fails open**: any unexpected exception returns
``CacheLookup(local_path=None, ...)`` or no-ops in ``sync_to_minio`` so a cache
problem can never block a clash. Toggle via ``IFCCLASH_BVH_CACHE=on|off``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration knobs (read at import time; safe to override via env in spawn
# children because we re-read on the hot path too).
# ---------------------------------------------------------------------------

def is_enabled() -> bool:
    """``IFCCLASH_BVH_CACHE=on|off`` master switch.

    Defaults to ``off`` until ``deploy`` confirms the roundtrip in production.
    Anything other than the affirmatives below is treated as off.
    """
    return os.environ.get("IFCCLASH_BVH_CACHE", "off").strip().lower() in (
        "1", "on", "true", "yes",
    )


def _cache_dir() -> Path:
    raw = os.environ.get("IFCCLASH_BVH_CACHE_DIR", "/tmp/ifcclash-bvh-cache")
    p = Path(raw)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _max_bytes() -> int:
    try:
        mb = int(os.environ.get("IFCCLASH_BVH_CACHE_MAX_MB", "4096"))
    except (TypeError, ValueError):
        mb = 4096
    return max(0, mb) * 1024 * 1024


def _minio_prefix() -> str:
    # Keyed under cache/ so the existing cleanup-service / lifecycle policies
    # never mistake it for output the user owns.
    return os.environ.get("IFCCLASH_BVH_CACHE_MINIO_PREFIX", "cache/clash-tess")


# ---------------------------------------------------------------------------
# SHA-256 of file contents, memoised by (abs_path, mtime_ns, size). This keeps
# the per-call hashing budget at "stat the file" for repeat back-to-back
# invocations against the same input.
# ---------------------------------------------------------------------------

_SHA_CACHE_LOCK = threading.Lock()
_SHA_CACHE: Dict[Tuple[str, int, int], str] = {}


def compute_file_sha256(file_path: str) -> str:
    """Return the hex sha256 of ``file_path``, memoised by (path, mtime, size).

    For multi-GB IFCs this is the only cache layer that actually reads bytes
    off disk on a miss — once per file lifetime per worker process.
    """
    abs_path = os.path.abspath(file_path)
    try:
        st = os.stat(abs_path)
    except OSError as exc:
        raise FileNotFoundError(f"bvh_cache: cannot stat {abs_path}: {exc}")

    key = (abs_path, st.st_mtime_ns, st.st_size)
    with _SHA_CACHE_LOCK:
        cached = _SHA_CACHE.get(key)
    if cached is not None:
        return cached

    h = hashlib.sha256()
    with open(abs_path, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    digest = h.hexdigest()
    with _SHA_CACHE_LOCK:
        _SHA_CACHE[key] = digest
    return digest


# ---------------------------------------------------------------------------
# Cache file path resolution + MinIO key formatting.
# ---------------------------------------------------------------------------

def cache_filename(file_sha: str, kernel: str) -> str:
    safe_kernel = kernel.replace("/", "_") or "default"
    return f"{file_sha}.h5"


def local_cache_path(file_sha: str, kernel: str) -> Path:
    base = _cache_dir()
    safe_kernel = kernel.replace("/", "_") or "default"
    sub = base / safe_kernel
    sub.mkdir(parents=True, exist_ok=True)
    return sub / cache_filename(file_sha, kernel)


def minio_key(file_sha: str, kernel: str) -> str:
    safe_kernel = kernel.replace("/", "_") or "default"
    return f"{_minio_prefix().rstrip('/')}/{safe_kernel}/{cache_filename(file_sha, kernel)}"


# ---------------------------------------------------------------------------
# Cache lookup result. Carries everything the caller needs to (a) attach to
# HdfSerializer and (b) decide whether to upload after.
# ---------------------------------------------------------------------------

@dataclass
class CacheLookup:
    enabled: bool
    file_sha: Optional[str]
    kernel: str
    local_path: Optional[Path]
    pre_size: int            # bytes on disk before the iterator runs
    source: str              # "fresh" | "lru" | "minio"
    sha_ms: float
    download_ms: float

    def is_warm(self) -> bool:
        return self.local_path is not None and self.pre_size > 0


def prewarm(file_path: str, kernel: str) -> CacheLookup:
    """Resolve / fetch the cache file for ``file_path`` and return a
    ``CacheLookup`` ready to be handed to ``HdfSerializer``.

    Never raises: any failure returns ``enabled=True, local_path=None`` so the
    caller silently falls back to the no-cache iterator path.
    """
    if not is_enabled():
        return CacheLookup(False, None, kernel, None, 0, "disabled", 0.0, 0.0)

    t0 = time.time()
    try:
        sha = compute_file_sha256(file_path)
    except Exception as exc:
        logger.warning("bvh_cache: sha256 failed for %s: %s", file_path, exc)
        return CacheLookup(True, None, kernel, None, 0, "sha-failed", 0.0, 0.0)
    sha_ms = (time.time() - t0) * 1000.0

    try:
        path = local_cache_path(sha, kernel)
    except Exception as exc:
        logger.warning("bvh_cache: local path resolution failed: %s", exc)
        return CacheLookup(True, sha, kernel, None, 0, "path-failed", sha_ms, 0.0)

    pre_size = path.stat().st_size if path.exists() else 0
    source = "lru" if pre_size > 0 else "fresh"
    dl_ms = 0.0

    if pre_size == 0:
        dl_ms = _try_pull_from_minio(sha, kernel, path) or 0.0
        if path.exists() and path.stat().st_size > 0:
            pre_size = path.stat().st_size
            source = "minio"

    return CacheLookup(
        enabled=True,
        file_sha=sha,
        kernel=kernel,
        local_path=path,
        pre_size=pre_size,
        source=source,
        sha_ms=sha_ms,
        download_ms=dl_ms,
    )


# ---------------------------------------------------------------------------
# MinIO pull / push. Imported lazily so the module is safe to import in
# environments without shared.object_storage on PYTHONPATH (e.g. CLI spike).
# ---------------------------------------------------------------------------

def _s3():
    try:
        from shared import object_storage as s3  # type: ignore
    except Exception as exc:
        logger.debug("bvh_cache: shared.object_storage unavailable: %s", exc)
        return None
    try:
        if not s3.is_enabled():
            return None
    except Exception:
        return None
    return s3


def _try_pull_from_minio(file_sha: str, kernel: str, dest: Path) -> Optional[float]:
    s3 = _s3()
    if s3 is None:
        return None
    key = minio_key(file_sha, kernel)
    try:
        if not s3.object_exists(key):
            return None
    except Exception as exc:
        logger.debug("bvh_cache: minio head failed for %s: %s", key, exc)
        return None

    # Download to a sibling temp file then rename — never leave a half-written
    # cache file behind, even if the worker crashes mid-download.
    t0 = time.time()
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=dest.name + ".", dir=str(dest.parent))
    os.close(tmp_fd)
    try:
        s3.download_to_path(key, tmp_path)
        os.replace(tmp_path, dest)
    except Exception as exc:
        logger.warning("bvh_cache: minio download failed for %s: %s", key, exc)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return None
    dl_ms = (time.time() - t0) * 1000.0
    logger.info(
        "bvh_cache: pulled %s (%d bytes) from MinIO in %.0f ms",
        key, dest.stat().st_size, dl_ms,
    )
    return dl_ms


def sync_to_minio(lookup: CacheLookup) -> dict:
    """If the local cache file grew since ``prewarm``, push the new version up.

    Returns a small status dict for instrumentation. Never raises.
    """
    if not lookup.enabled or lookup.local_path is None or lookup.file_sha is None:
        return {"uploaded": False, "reason": "disabled-or-no-cache"}
    if not lookup.local_path.exists():
        return {"uploaded": False, "reason": "local-vanished"}

    post_size = lookup.local_path.stat().st_size
    if post_size <= lookup.pre_size:
        return {"uploaded": False, "reason": "no-growth", "size": post_size}

    s3 = _s3()
    if s3 is None:
        return {"uploaded": False, "reason": "no-s3", "size": post_size}

    key = minio_key(lookup.file_sha, lookup.kernel)
    t0 = time.time()
    try:
        s3.upload_from_path(str(lookup.local_path), key, content_type="application/x-hdf5")
    except Exception as exc:
        logger.warning("bvh_cache: minio upload failed for %s: %s", key, exc)
        return {"uploaded": False, "reason": f"upload-failed:{exc}"}
    up_ms = (time.time() - t0) * 1000.0
    logger.info(
        "bvh_cache: pushed %s (%d → %d bytes) to MinIO in %.0f ms",
        key, lookup.pre_size, post_size, up_ms,
    )
    return {
        "uploaded": True,
        "pre_size": lookup.pre_size,
        "post_size": post_size,
        "delta_bytes": post_size - lookup.pre_size,
        "upload_ms": up_ms,
    }


# ---------------------------------------------------------------------------
# LRU eviction over the on-disk cache. Called opportunistically; cheap because
# it only scans the cache dir tree, not the cache contents.
# ---------------------------------------------------------------------------

_LAST_EVICT_TS: float = 0.0
_EVICT_LOCK = threading.Lock()


def maybe_evict(max_bytes: Optional[int] = None, min_interval_s: float = 60.0) -> None:
    if not is_enabled():
        return
    now = time.time()
    global _LAST_EVICT_TS
    with _EVICT_LOCK:
        if now - _LAST_EVICT_TS < min_interval_s:
            return
        _LAST_EVICT_TS = now

    cap = max_bytes if max_bytes is not None else _max_bytes()
    if cap <= 0:
        return
    base = _cache_dir()
    entries = []
    total = 0
    for path in base.rglob("*.h5"):
        try:
            st = path.stat()
        except OSError:
            continue
        entries.append((st.st_mtime, st.st_size, path))
        total += st.st_size
    if total <= cap:
        return

    # Evict oldest until under cap, leaving a 10 % headroom so we don't churn.
    target = int(cap * 0.9)
    entries.sort()  # oldest first
    freed = 0
    for mtime, size, path in entries:
        if total - freed <= target:
            break
        try:
            path.unlink()
            freed += size
            logger.info("bvh_cache: evicted %s (%d bytes, age=%.0fs)", path, size, now - mtime)
        except OSError as exc:
            logger.debug("bvh_cache: evict failed for %s: %s", path, exc)
    logger.info("bvh_cache: post-evict total ≈ %d bytes (cap=%d, freed=%d)", total - freed, cap, freed)
