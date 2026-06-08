"""Space-model cache for the topologicpy-worker roomstamp job.

Roomstamp re-runs against a stable architecture file repeatedly. Every run
otherwise rebuilds the spatial side from scratch:

  * ``ifcopenshell.open`` the spatial IFC          (``load_seconds``)
  * ``ifcopenshell.geom.create_shape`` per IfcSpace (``space_collect_seconds``)

Both produce the same ``verts``/``faces``/``bbox``/metadata for an unchanged
spatial file. This module caches that collected-space payload so warm runs skip
the load + create_shape pass. The Topologic cells themselves are **not** cached
(they are C++ objects and not serializable); ``_prebuild_space_cells`` cheaply
rebuilds them from the cached ``verts``/``faces``.

Two storage layers (mirrors ``ifcclash-worker/bvh_cache.py``):

  1. **Local disk** — ``IFCTOPOLOGY_SPACE_CACHE_DIR`` (default
     ``/tmp/topology-space-cache``). Survives between RQ jobs in a container.
  2. **MinIO** at ``cache/topology-spaces/<key>.json.gz`` — survives container
     restarts and is shared across every worker (local + remote).

The cache is keyed by ``(content sha of each spatial file, space_query,
include_zones, FORMAT_VERSION)`` so it auto-invalidates whenever the spatial
file or the query changes.

The whole module **fails open**: any unexpected error returns ``None`` (miss)
or no-ops (save), so a cache problem can never block a roomstamp. Toggle with
``IFCTOPOLOGY_SPACE_CACHE=on|off`` (default off).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Bump when the serialized payload schema or the geometry settings that affect
# verts/faces change, so stale entries are ignored instead of mis-deserialized.
FORMAT_VERSION = 1

# Separate version for the built-cell (BREP) payload; bump if the cell build
# logic or BREP schema changes.
CELL_FORMAT_VERSION = 1


# ---------------------------------------------------------------------------
# Configuration knobs
# ---------------------------------------------------------------------------

def is_enabled() -> bool:
    """``IFCTOPOLOGY_SPACE_CACHE=on|off`` master switch (default off)."""
    return os.environ.get("IFCTOPOLOGY_SPACE_CACHE", "off").strip().lower() in (
        "1", "on", "true", "yes",
    )


def _cache_dir() -> Path:
    raw = os.environ.get("IFCTOPOLOGY_SPACE_CACHE_DIR", "/tmp/topology-space-cache")
    p = Path(raw)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _max_bytes() -> int:
    try:
        mb = int(os.environ.get("IFCTOPOLOGY_SPACE_CACHE_MAX_MB", "2048"))
    except (TypeError, ValueError):
        mb = 2048
    return max(0, mb) * 1024 * 1024


def _minio_prefix() -> str:
    # Keyed under cache/ so the cleanup-service / lifecycle policies never treat
    # it as user-owned output.
    return os.environ.get(
        "IFCTOPOLOGY_SPACE_CACHE_MINIO_PREFIX", "cache/topology-spaces"
    )


# ---------------------------------------------------------------------------
# Content sha, memoised by (abs_path, mtime_ns, size).
# ---------------------------------------------------------------------------

_SHA_CACHE_LOCK = threading.Lock()
_SHA_CACHE: Dict[Tuple[str, int, int], str] = {}


def file_sha256(file_path: str) -> str:
    abs_path = os.path.abspath(file_path)
    st = os.stat(abs_path)
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


def build_key(
    spatial_shas: List[str],
    space_query: str,
    include_zones: bool,
) -> str:
    """Derive the spaces (verts/faces) cache key from the cache-relevant inputs."""
    parts = [
        f"fmt={FORMAT_VERSION}",
        "shas=" + ",".join(sorted(spatial_shas)),
        f"query={space_query}",
        f"zones={int(bool(include_zones))}",
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def cells_enabled() -> bool:
    """Whether the built-cell (BREP) cache is active.

    Separate from is_enabled() and OFF by default: BREP-roundtripped mesh cells
    (Topology.ByBREPString) can segfault topologic containment (OCCT) on some
    datasets, taking down the whole work-horse. Only enable for experimentation
    on known-safe data. Requires the space cache to also be enabled.
    """
    if not is_enabled():
        return False
    return os.environ.get("IFCTOPOLOGY_CELL_CACHE", "off").strip().lower() in (
        "1", "true", "on", "yes",
    )


def build_cell_key(
    spatial_shas: List[str],
    space_query: str,
    include_zones: bool,
    cell_mode: str,
    tolerance: float,
) -> str:
    """Derive the built-cell (BREP) cache key.

    Cells additionally depend on ``cell_mode`` and ``tolerance``, so those enter
    the key (whereas the collected verts/faces do not).
    """
    parts = [
        f"cellfmt={CELL_FORMAT_VERSION}",
        "shas=" + ",".join(sorted(spatial_shas)),
        f"query={space_query}",
        f"zones={int(bool(include_zones))}",
        f"cell_mode={cell_mode}",
        f"tol={float(tolerance):.6f}",
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Path / key resolution.
# ---------------------------------------------------------------------------

def _spaces_filename(key: str) -> str:
    return f"{key}.json.gz"


def _cells_filename(key: str) -> str:
    return f"{key}.cells.json.gz"


def local_path(filename: str) -> Path:
    return _cache_dir() / filename


def minio_key(filename: str) -> str:
    return f"{_minio_prefix().rstrip('/')}/{filename}"


# ---------------------------------------------------------------------------
# MinIO pull / push (lazy import; fail-open).
# ---------------------------------------------------------------------------

def _s3():
    try:
        from shared import object_storage as s3  # type: ignore
    except Exception as exc:
        logger.debug("space_cache: shared.object_storage unavailable: %s", exc)
        return None
    try:
        if not s3.is_enabled():
            return None
    except Exception:
        return None
    return s3


def _try_pull_from_minio(filename: str, dest: Path) -> bool:
    s3 = _s3()
    if s3 is None:
        return False
    mkey = minio_key(filename)
    try:
        if not s3.object_exists(mkey):
            return False
    except Exception as exc:
        logger.debug("space_cache: minio head failed for %s: %s", mkey, exc)
        return False
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=dest.name + ".", dir=str(dest.parent))
    os.close(tmp_fd)
    try:
        s3.download_to_path(mkey, tmp_path)
        os.replace(tmp_path, dest)
    except Exception as exc:
        logger.warning("space_cache: minio download failed for %s: %s", mkey, exc)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return False
    logger.info("space_cache: pulled %s (%d bytes) from MinIO", mkey, dest.stat().st_size)
    return True


def _push_to_minio(filename: str, path: Path) -> None:
    s3 = _s3()
    if s3 is None:
        return
    mkey = minio_key(filename)
    try:
        s3.upload_from_path(str(path), mkey, content_type="application/gzip")
        logger.info("space_cache: pushed %s (%d bytes) to MinIO", mkey, path.stat().st_size)
    except Exception as exc:
        logger.warning("space_cache: minio upload failed for %s: %s", mkey, exc)


# ---------------------------------------------------------------------------
# Public load / save. Operate on plain JSON-serializable dicts only; the caller
# converts to/from SpaceCandidate so this module stays free of tasks imports.
# ---------------------------------------------------------------------------

def _read_blob(filename: str) -> Optional[dict]:
    """Local-or-MinIO read of a gzipped-JSON blob. Returns the dict or None."""
    path = local_path(filename)
    if not (path.exists() and path.stat().st_size > 0):
        if not _try_pull_from_minio(filename, path):
            return None
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        payload = json.load(fh)
    return payload if isinstance(payload, dict) else None


def _write_blob(filename: str, payload: dict) -> None:
    """Atomic local write + MinIO push of a gzipped-JSON blob."""
    path = local_path(filename)
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    os.close(tmp_fd)
    try:
        with gzip.open(tmp_path, "wt", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    _push_to_minio(filename, path)
    maybe_evict()


def load(key: str) -> Optional[List[dict]]:
    """Return the cached list of space dicts, or ``None`` on any miss/error."""
    if not is_enabled():
        return None
    try:
        payload = _read_blob(_spaces_filename(key))
        if payload is None or payload.get("format") != FORMAT_VERSION:
            return None
        spaces = payload.get("spaces")
        return spaces if isinstance(spaces, list) else None
    except Exception as exc:
        logger.warning("space_cache: load failed for %s: %s", key, exc)
        return None


def save(key: str, spaces: List[dict]) -> None:
    """Write the space dicts to the local + MinIO cache. Never raises."""
    if not is_enabled():
        return
    try:
        _write_blob(_spaces_filename(key), {"format": FORMAT_VERSION, "spaces": spaces})
    except Exception as exc:
        logger.warning("space_cache: save failed for %s: %s", key, exc)


def load_cells(key: str) -> Optional[List[dict]]:
    """Return the cached list of built-cell dicts ({global_id, kind, brep}), or None."""
    if not is_enabled():
        return None
    try:
        payload = _read_blob(_cells_filename(key))
        if payload is None or payload.get("format") != CELL_FORMAT_VERSION:
            return None
        cells = payload.get("cells")
        return cells if isinstance(cells, list) else None
    except Exception as exc:
        logger.warning("space_cache: cell load failed for %s: %s", key, exc)
        return None


def save_cells(key: str, cells: List[dict]) -> None:
    """Write the built-cell BREP dicts to the local + MinIO cache. Never raises."""
    if not is_enabled():
        return
    try:
        _write_blob(_cells_filename(key), {"format": CELL_FORMAT_VERSION, "cells": cells})
    except Exception as exc:
        logger.warning("space_cache: cell save failed for %s: %s", key, exc)


# ---------------------------------------------------------------------------
# LRU eviction over the local cache dir (cheap; stats only).
# ---------------------------------------------------------------------------

_LAST_EVICT_TS: float = 0.0
_EVICT_LOCK = threading.Lock()


def maybe_evict(min_interval_s: float = 60.0) -> None:
    if not is_enabled():
        return
    now = time.time()
    global _LAST_EVICT_TS
    with _EVICT_LOCK:
        if now - _LAST_EVICT_TS < min_interval_s:
            return
        _LAST_EVICT_TS = now
    cap = _max_bytes()
    if cap <= 0:
        return
    base = _cache_dir()
    entries = []
    total = 0
    for path in base.glob("*.json.gz"):
        try:
            st = path.stat()
        except OSError:
            continue
        entries.append((st.st_mtime, st.st_size, path))
        total += st.st_size
    if total <= cap:
        return
    target = int(cap * 0.9)
    entries.sort()  # oldest first
    freed = 0
    for _mtime, size, path in entries:
        if total - freed <= target:
            break
        try:
            path.unlink()
            freed += size
        except OSError:
            pass
