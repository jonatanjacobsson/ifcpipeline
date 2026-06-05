"""Per-file validation cache for ifcclash-worker.

Companion to ``bvh_cache.py``. The pre-clash ``validate_ifc_file`` step calls
``ifcopenshell.open(file_path)`` + a small geometry-iterator probe on every
clash invocation, costing ~2-3 s per file on real models (e.g. 3.19 s on the
72 MB S2 master). Both outputs (``schema``, ``element_count``,
"validated=True", and the IFC4X3 / AGGREGATE-OF-STRING signal) are 100 %
deterministic given the file's content sha256, so we memoise them.

Three-layer policy:

  1. **Cheap header check** stays in ``validate_ifc_file`` (catches missing /
     truncated files before we even hash).
  2. **Redis hash** at ``ifcclash:validate:<sha256>`` keyed by file content
     sha256, with per-kernel ``kernel_ok`` field tracking which geometry
     libraries have demonstrably initialised an iterator on this file.
  3. **BVH-cache promotion** — if ``bvh_cache.local_cache_path(sha, kernel)``
     exists with size > 0 the file is *provably* valid for that kernel (we
     successfully tessellated elements from it), so we mark it validated
     without re-running the probe even if Redis has no entry yet.

The whole module fails open: any unexpected exception returns ``None`` from
``lookup`` and silently no-ops in ``store`` so a cache problem can never
block validation. Toggle via ``IFCCLASH_VALIDATE_CACHE=on|off`` env.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

import bvh_cache


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def is_enabled() -> bool:
    """``IFCCLASH_VALIDATE_CACHE=on|off`` master switch (default off)."""
    return os.environ.get("IFCCLASH_VALIDATE_CACHE", "off").strip().lower() in (
        "1", "on", "true", "yes",
    )


def _ttl_seconds() -> int:
    try:
        return max(0, int(os.environ.get("IFCCLASH_VALIDATE_CACHE_TTL_S", "604800")))
    except (TypeError, ValueError):
        return 604800  # 7 days


def _redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://redis:6379/0")


def _redis_key(sha: str) -> str:
    prefix = os.environ.get("IFCCLASH_VALIDATE_CACHE_PREFIX", "ifcclash:validate")
    return f"{prefix.rstrip(':')}:{sha}"


# ---------------------------------------------------------------------------
# Redis connection — lazily resolved so the module is import-safe in
# environments without a Redis daemon (CLI spike, unit tests).
# ---------------------------------------------------------------------------

_REDIS_CACHE: Dict[str, object] = {}


def _redis():
    url = _redis_url()
    cached = _REDIS_CACHE.get(url)
    if cached is not None:
        return cached
    try:
        from redis import Redis  # type: ignore
    except Exception as exc:
        logger.debug("validation_cache: redis package unavailable: %s", exc)
        return None
    try:
        client = Redis.from_url(url, socket_timeout=2.0, socket_connect_timeout=2.0)
        # Cheap probe — never raise on the hot path because of a transient
        # network blip; the caller treats None as "skip cache".
        client.ping()
    except Exception as exc:
        logger.warning("validation_cache: redis connect/ping failed for %s: %s", url, exc)
        return None
    _REDIS_CACHE[url] = client
    return client


# ---------------------------------------------------------------------------
# Cached result. Mirrors the public surface of validate_ifc_file's
# metadata dict so the caller can drop us in with minimal restructuring.
# ---------------------------------------------------------------------------

@dataclass
class ValidationHit:
    sha: str
    schema: Optional[str]
    element_count: Optional[object]   # int or "unknown" — matches today's shape
    kernel_ok: Set[str] = field(default_factory=set)
    validated_at: Optional[float] = None
    source: str = "redis"            # "redis" | "bvh-promote"

    def to_metadata(self, file_path: str) -> Dict[str, object]:
        return {
            "file_path": file_path,
            "file_name": os.path.basename(file_path),
            "validated": True,
            "schema": self.schema,
            "element_count": self.element_count,
            "validation_source": self.source,
            "sha256": self.sha,
        }


# ---------------------------------------------------------------------------
# Lookup / store
# ---------------------------------------------------------------------------

def lookup(file_path: str, kernel: str) -> Optional[ValidationHit]:
    """Return a ``ValidationHit`` if the file is known-good for ``kernel``,
    else None. Never raises.

    Order:
      1. Redis hash for sha → fields, check that ``kernel`` appears in
         ``kernel_ok`` (so OCC and CGAL can disagree about a file).
      2. BVH-cache fallback: ``bvh_cache.local_cache_path(sha, kernel)``
         exists and is non-empty → file is provably valid for that kernel;
         promote into Redis on the spot so future lookups skip even the
         filesystem stat.
    """
    if not is_enabled():
        return None
    try:
        sha = bvh_cache.compute_file_sha256(file_path)
    except Exception as exc:
        logger.debug("validation_cache: sha256 failed for %s: %s", file_path, exc)
        return None

    # 1) Redis
    hit = _lookup_redis(sha, kernel)
    if hit is not None:
        return hit

    # 2) BVH-cache promotion
    try:
        bvh_path = bvh_cache.local_cache_path(sha, kernel)
    except Exception:
        bvh_path = None
    if bvh_path is not None and bvh_path.exists() and bvh_path.stat().st_size > 0:
        hit = ValidationHit(
            sha=sha,
            schema=None,             # not known without opening; downstream tolerates
            element_count="unknown",
            kernel_ok={kernel},
            validated_at=time.time(),
            source="bvh-promote",
        )
        # Best-effort promote into Redis so the next hit is a pure HGET.
        _store_redis(hit)
        return hit

    return None


def store(file_path: str, kernel: str, schema: Optional[str],
          element_count: Optional[object]) -> Optional[ValidationHit]:
    """Persist a successful validation. Never raises.

    Returns the stored ``ValidationHit`` (with the resolved sha) for the
    caller to log, or None when the cache is disabled / sha failed.
    """
    if not is_enabled():
        return None
    try:
        sha = bvh_cache.compute_file_sha256(file_path)
    except Exception as exc:
        logger.debug("validation_cache: sha256 failed for %s: %s", file_path, exc)
        return None
    hit = ValidationHit(
        sha=sha,
        schema=schema,
        element_count=element_count,
        kernel_ok={kernel},
        validated_at=time.time(),
        source="fresh",
    )
    _store_redis(hit)
    return hit


# ---------------------------------------------------------------------------
# Internal: Redis read/write helpers
# ---------------------------------------------------------------------------

def _lookup_redis(sha: str, kernel: str) -> Optional[ValidationHit]:
    client = _redis()
    if client is None:
        return None
    key = _redis_key(sha)
    try:
        raw = client.hgetall(key)
    except Exception as exc:
        logger.debug("validation_cache: hgetall %s raised: %s", key, exc)
        return None
    if not raw:
        return None

    # Redis returns bytes for both keys and values by default; decode.
    fields: Dict[str, str] = {}
    for k, v in raw.items():
        kk = k.decode() if isinstance(k, (bytes, bytearray)) else str(k)
        vv = v.decode() if isinstance(v, (bytes, bytearray)) else str(v)
        fields[kk] = vv

    kernels_csv = fields.get("kernel_ok", "")
    kernels = {x.strip() for x in kernels_csv.split(",") if x.strip()}
    if kernel not in kernels:
        return None

    elem_raw = fields.get("element_count")
    try:
        elem_count: object = int(elem_raw) if elem_raw and elem_raw.isdigit() else (elem_raw or "unknown")
    except Exception:
        elem_count = "unknown"

    validated_at = None
    try:
        validated_at = float(fields["validated_at"]) if "validated_at" in fields else None
    except (TypeError, ValueError):
        validated_at = None

    return ValidationHit(
        sha=sha,
        schema=fields.get("schema") or None,
        element_count=elem_count,
        kernel_ok=kernels,
        validated_at=validated_at,
        source="redis",
    )


def _store_redis(hit: ValidationHit) -> None:
    client = _redis()
    if client is None:
        return
    key = _redis_key(hit.sha)
    try:
        # Merge into existing kernel_ok rather than overwriting, so a CGAL
        # validation later doesn't forget the earlier OCC success.
        existing = client.hget(key, "kernel_ok")
        if existing:
            existing_csv = existing.decode() if isinstance(existing, (bytes, bytearray)) else str(existing)
            merged = {x.strip() for x in existing_csv.split(",") if x.strip()} | hit.kernel_ok
        else:
            merged = set(hit.kernel_ok)
        payload = {
            "schema": (hit.schema or ""),
            "element_count": (str(hit.element_count) if hit.element_count is not None else ""),
            "kernel_ok": ",".join(sorted(merged)),
            "validated_at": f"{hit.validated_at:.0f}" if hit.validated_at else "",
        }
        # Tiny pipeline so HSET + EXPIRE are one round-trip.
        pipe = client.pipeline(transaction=False)
        pipe.hset(key, mapping=payload)
        ttl = _ttl_seconds()
        if ttl > 0:
            pipe.expire(key, ttl)
        pipe.execute()
    except Exception as exc:
        logger.debug("validation_cache: store %s raised: %s", key, exc)


# ---------------------------------------------------------------------------
# Admin helpers (used by manual purge / debugging; never on the hot path).
# ---------------------------------------------------------------------------

def purge(sha: str) -> bool:
    client = _redis()
    if client is None:
        return False
    try:
        return bool(client.delete(_redis_key(sha)))
    except Exception:
        return False


def stats() -> Dict[str, object]:
    """Return a small summary of cached entries — for ad-hoc inspection."""
    client = _redis()
    if client is None:
        return {"enabled": is_enabled(), "redis": False}
    prefix = os.environ.get("IFCCLASH_VALIDATE_CACHE_PREFIX", "ifcclash:validate")
    try:
        n = 0
        for _ in client.scan_iter(match=f"{prefix}:*", count=500):
            n += 1
        return {"enabled": is_enabled(), "redis": True, "entries": n, "prefix": prefix}
    except Exception as exc:
        return {"enabled": is_enabled(), "redis": False, "error": str(exc)}
