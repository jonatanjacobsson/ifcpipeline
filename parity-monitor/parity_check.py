"""SeaweedFS pilot — parity monitor.

Every PARITY_INTERVAL_S seconds (default 600), diff the primary (MinIO) and
shadow (SeaweedFS) backends and write a JSON report under /reports.

Report shape:

  {
    "ts": "2026-05-17T09:11:00Z",
    "level": "ok" | "warn" | "error",
    "primary": {"count": N, "size_bytes": ...},
    "shadow":  {"count": N, "size_bytes": ...},
    "drift":   {"only_in_primary": [...], "only_in_shadow": [...]},
    "samples": [{"key": "...", "size_match": bool, "sha256_match": bool, ...}],
    "audit":   {"checked": N, "mismatched": [...]},
    "shadow_counters": {...},  // copy of /reports/shadow-counter.json
  }

The script never raises out of the loop; any iteration that fails writes a
"level": "error" report instead. Designed to be debuggable via:

  docker compose -f docker-compose.yml -f docker-compose.seaweedfs.yml \
    logs -f parity-monitor
"""

from __future__ import annotations

import json
import logging
import os
import random
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import boto3
from botocore.config import Config

try:
    import psycopg
except Exception:  # pragma: no cover - psycopg should be installed
    psycopg = None  # type: ignore


REPORTS_DIR = os.environ.get("S3_SHADOW_REPORTS_DIR", "/reports")
INTERVAL_S = int(os.environ.get("PARITY_INTERVAL_S", "600"))
SAMPLE_N = int(os.environ.get("PARITY_SAMPLE_N", "20"))
MAX_LIST = int(os.environ.get("PARITY_MAX_LIST", "50000"))

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("parity")


def _s3_client(endpoint: str, access: str, secret: str, region: str):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        config=Config(
            s3={"addressing_style": "path"},
            signature_version="s3v4",
            connect_timeout=5,
            read_timeout=30,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )


def _primary_client():
    return _s3_client(
        endpoint=os.environ["S3_ENDPOINT_URL"],
        access=os.environ["S3_ACCESS_KEY"],
        secret=os.environ["S3_SECRET_KEY"],
        region=os.environ.get("S3_REGION", "us-east-1"),
    )


def _shadow_client():
    return _s3_client(
        endpoint=os.environ["S3_SHADOW_ENDPOINT_URL"],
        access=os.environ["S3_SHADOW_ACCESS_KEY"],
        secret=os.environ["S3_SHADOW_SECRET_KEY"],
        region=os.environ.get("S3_SHADOW_REGION", "us-east-1"),
    )


def _list_all_objects(client, bucket: str, max_keys: int = MAX_LIST) -> Dict[str, Dict[str, Any]]:
    """Return {key: {"size": int, "etag": str, "last_modified": iso}}.

    Capped at `max_keys` to avoid pathological runs early in the pilot. The
    cap is reported in the parity JSON so operators know if they're looking
    at a partial diff.
    """
    out: Dict[str, Dict[str, Any]] = {}
    token: Optional[str] = None
    paginator = client.get_paginator("list_objects_v2")
    kwargs: Dict[str, Any] = {"Bucket": bucket}
    for page in paginator.paginate(**kwargs):
        for obj in page.get("Contents", []) or []:
            out[obj["Key"]] = {
                "size": obj.get("Size"),
                "etag": (obj.get("ETag") or "").strip('"'),
                "last_modified": obj["LastModified"].astimezone(timezone.utc).isoformat()
                if obj.get("LastModified") else None,
            }
            if len(out) >= max_keys:
                return out
    return out


def _head_with_checksum(client, bucket: str, key: str) -> Optional[Dict[str, Any]]:
    try:
        resp = client.head_object(Bucket=bucket, Key=key, ChecksumMode="ENABLED")
    except Exception:
        return None
    return {
        "size": resp.get("ContentLength"),
        "etag": (resp.get("ETag") or "").strip('"'),
        "checksum_sha256_b64": resp.get("ChecksumSHA256"),
        "version_id": resp.get("VersionId"),
    }


def _sample_shared_keys(
    shared: List[str],
    *,
    n: int,
) -> List[str]:
    """Sorted-evenly-spaced + a random spice so we cover the bucket but a
    drifting key doesn't have to wait for the next iteration to be observed."""
    if not shared:
        return []
    shared_sorted = sorted(shared)
    if len(shared_sorted) <= n:
        return shared_sorted
    step = max(1, len(shared_sorted) // (n - 1 if n > 1 else 1))
    spaced = [shared_sorted[min(i * step, len(shared_sorted) - 1)] for i in range(n - 5)]
    # Sprinkle a few random picks (deterministic seed per interval is fine).
    rng = random.Random(int(time.time() // INTERVAL_S))
    rng.shuffle(shared_sorted)
    spaced.extend(shared_sorted[:5])
    return list(dict.fromkeys(spaced))[:n]


def _load_shadow_counters() -> Dict[str, Any]:
    path = os.path.join(REPORTS_DIR, "shadow-counter.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"note": "counter file not yet created (no shadow PUTs observed)"}
    except Exception as e:
        return {"error": f"could not read counters: {e}"}


def _connect_pg():
    if psycopg is None:
        return None
    try:
        return psycopg.connect(
            host=os.environ.get("POSTGRES_HOST", "postgres"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            dbname=os.environ.get("POSTGRES_DB", "ifcpipeline"),
            user=os.environ.get("POSTGRES_USER", "ifcpipeline"),
            password=os.environ.get("POSTGRES_PASSWORD", ""),
            connect_timeout=5,
        )
    except Exception as e:
        logger.warning("postgres connect failed: %s", e)
        return None


def _audit_cross_check(
    sample_keys: List[str],
    primary_heads: Dict[str, Dict[str, Any]],
    shadow_heads: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """For each sampled key, verify the newest object_versions row pins
    `version_id` = primary HEAD and `metadata.shadow_version_id` =
    shadow HEAD."""
    conn = _connect_pg()
    if conn is None:
        return {"checked": 0, "mismatched": [], "legacy_null": 0, "note": "postgres unavailable"}
    mismatched: List[Dict[str, Any]] = []
    legacy_null = 0
    checked = 0
    try:
        with conn.cursor() as cur:
            for key in sample_keys:
                cur.execute(
                    """
                    SELECT version_id,
                           metadata->>'shadow_version_id' AS shadow_vid
                      FROM object_versions
                     WHERE object_key = %s
                     ORDER BY created_at DESC, id DESC
                     LIMIT 1
                    """,
                    (key,),
                )
                row = cur.fetchone()
                if not row:
                    # Key may have been written via the legacy filesystem path
                    # (no audit row) — not a parity defect.
                    continue
                checked += 1
                db_primary_vid, db_shadow_vid = row
                primary_vid = (primary_heads.get(key) or {}).get("version_id")
                shadow_vid = (shadow_heads.get(key) or {}).get("version_id")
                # In normal mode the latest audit row's version_id should
                # match MinIO's current HEAD. If MinIO returns nothing
                # (rare race), we skip silently.
                problems = []
                if primary_vid and db_primary_vid and primary_vid != db_primary_vid:
                    problems.append(
                        f"primary_db_version_id={db_primary_vid!r} != head={primary_vid!r}"
                    )
                if shadow_vid and db_shadow_vid and shadow_vid != db_shadow_vid:
                    problems.append(
                        f"shadow_db_version_id={db_shadow_vid!r} != head={shadow_vid!r}"
                    )
                if db_shadow_vid is None and shadow_vid is not None:
                    # Benign legacy case: the object exists on the shadow
                    # backend (e.g. via the initial backfill) but its audit
                    # row predates dual-write, so no shadow_version_id was
                    # recorded. This is expected for historical data and is
                    # NOT a parity defect — track it separately rather than
                    # counting it as a hard mismatch (see SEAWEEDFS_PILOT.md
                    # §3.4). Real shadow defects are caught above, when BOTH
                    # the DB and the HEAD carry a version_id and they differ.
                    legacy_null += 1
                if problems:
                    mismatched.append({"key": key, "issues": problems})
    except Exception as e:
        logger.error("audit cross-check failed: %s", e)
        return {"checked": checked, "mismatched": mismatched, "legacy_null": legacy_null, "error": str(e)}
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return {"checked": checked, "mismatched": mismatched, "legacy_null": legacy_null}


def _prefix_counts(keys: List[str]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for k in keys:
        prefix = k.split("/", 1)[0] if "/" in k else "(root)"
        out[prefix] = out.get(prefix, 0) + 1
    return out


def _emit_report(report: Dict[str, Any]) -> None:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    ts = report["ts"].replace(":", "-").replace("+00:00", "Z")
    out_path = os.path.join(REPORTS_DIR, f"parity-{ts}.json")
    latest_path = os.path.join(REPORTS_DIR, "parity-latest.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)
    tmp = latest_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)
    os.replace(tmp, latest_path)


def _summary_line(report: Dict[str, Any]) -> str:
    drift = report["drift"]
    samples = report["samples"]
    sha_match = sum(1 for s in samples if s.get("size_match") and s.get("checksum_match") is not False)
    return (
        f"parity level={report['level']} "
        f"primary={report['primary']['count']} shadow={report['shadow']['count']} "
        f"only_primary={len(drift['only_in_primary'])} only_shadow={len(drift['only_in_shadow'])} "
        f"sampled={len(samples)} matched={sha_match} "
        f"audit_checked={report['audit'].get('checked')} "
        f"audit_mismatched={len(report['audit'].get('mismatched', []))} "
        f"audit_legacy_null={report['audit'].get('legacy_null', 0)}"
    )


def _one_tick(
    primary, shadow, *,
    primary_bucket: str, shadow_bucket: str,
    prev_drift: Tuple[Set[str], Set[str]],
) -> Tuple[Dict[str, Any], Tuple[Set[str], Set[str]]]:
    now = datetime.now(timezone.utc).isoformat()
    primary_list = _list_all_objects(primary, primary_bucket)
    shadow_list = _list_all_objects(shadow, shadow_bucket)

    primary_keys = set(primary_list.keys())
    shadow_keys = set(shadow_list.keys())
    only_in_primary = primary_keys - shadow_keys
    only_in_shadow = shadow_keys - primary_keys
    shared = sorted(primary_keys & shadow_keys)

    sample_keys = _sample_shared_keys(shared, n=SAMPLE_N)
    primary_heads: Dict[str, Dict[str, Any]] = {}
    shadow_heads: Dict[str, Dict[str, Any]] = {}
    sample_results: List[Dict[str, Any]] = []
    for key in sample_keys:
        ph = _head_with_checksum(primary, primary_bucket, key) or {}
        sh = _head_with_checksum(shadow, shadow_bucket, key) or {}
        primary_heads[key] = ph
        shadow_heads[key] = sh
        size_match = ph.get("size") == sh.get("size")
        checksum_match: Optional[bool] = None
        if ph.get("checksum_sha256_b64") and sh.get("checksum_sha256_b64"):
            checksum_match = ph["checksum_sha256_b64"] == sh["checksum_sha256_b64"]
        sample_results.append({
            "key": key,
            "primary_size": ph.get("size"),
            "shadow_size": sh.get("size"),
            "size_match": size_match,
            "primary_etag": ph.get("etag"),
            "shadow_etag": sh.get("etag"),
            "primary_version_id": ph.get("version_id"),
            "shadow_version_id": sh.get("version_id"),
            "checksum_match": checksum_match,
        })

    audit = _audit_cross_check(sample_keys, primary_heads, shadow_heads)

    # Escalation rules.
    level = "ok"
    prev_only_primary, prev_only_shadow = prev_drift
    sustained_primary = only_in_primary & prev_only_primary
    sustained_shadow = only_in_shadow & prev_only_shadow
    if sustained_primary or sustained_shadow:
        level = "warn"
    bad_samples = [s for s in sample_results if not s.get("size_match")]
    bad_checksums = [s for s in sample_results if s.get("checksum_match") is False]
    if bad_samples or bad_checksums or audit.get("mismatched"):
        level = "warn"

    primary_size = sum(int(v.get("size") or 0) for v in primary_list.values())
    shadow_size = sum(int(v.get("size") or 0) for v in shadow_list.values())
    capped = (len(primary_list) >= MAX_LIST) or (len(shadow_list) >= MAX_LIST)

    report = {
        "ts": now,
        "level": level,
        "interval_s": INTERVAL_S,
        "primary": {
            "endpoint": os.environ.get("S3_ENDPOINT_URL"),
            "bucket": primary_bucket,
            "count": len(primary_list),
            "size_bytes": primary_size,
            "prefix_counts": _prefix_counts(list(primary_list.keys())),
        },
        "shadow": {
            "endpoint": os.environ.get("S3_SHADOW_ENDPOINT_URL"),
            "bucket": shadow_bucket,
            "count": len(shadow_list),
            "size_bytes": shadow_size,
            "prefix_counts": _prefix_counts(list(shadow_list.keys())),
        },
        "drift": {
            "only_in_primary": sorted(only_in_primary)[:200],
            "only_in_shadow": sorted(only_in_shadow)[:200],
            "only_in_primary_count": len(only_in_primary),
            "only_in_shadow_count": len(only_in_shadow),
            "sustained_only_in_primary": sorted(sustained_primary)[:200],
            "sustained_only_in_shadow": sorted(sustained_shadow)[:200],
            "list_capped_at": MAX_LIST if capped else None,
        },
        "samples": sample_results,
        "audit": audit,
        "shadow_counters": _load_shadow_counters(),
    }
    _emit_report(report)
    logger.info(_summary_line(report))
    return report, (only_in_primary, only_in_shadow)


def main() -> int:
    primary_bucket = os.environ.get("S3_BUCKET", "ifcpipeline")
    shadow_bucket = os.environ.get("S3_SHADOW_BUCKET", primary_bucket)
    logger.info(
        "parity-monitor starting: interval=%ss sample=%s primary=%s/%s shadow=%s/%s",
        INTERVAL_S, SAMPLE_N,
        os.environ.get("S3_ENDPOINT_URL"), primary_bucket,
        os.environ.get("S3_SHADOW_ENDPOINT_URL"), shadow_bucket,
    )
    if not os.environ.get("S3_SHADOW_ENDPOINT_URL"):
        logger.error(
            "S3_SHADOW_ENDPOINT_URL not set — refusing to start parity monitor. "
            "Add the SeaweedFS pilot env vars to .env and recreate this service."
        )
        return 2

    primary = _primary_client()
    shadow = _shadow_client()
    prev_drift: Tuple[Set[str], Set[str]] = (set(), set())

    while True:
        started = time.perf_counter()
        try:
            _, prev_drift = _one_tick(
                primary, shadow,
                primary_bucket=primary_bucket,
                shadow_bucket=shadow_bucket,
                prev_drift=prev_drift,
            )
        except Exception as e:
            now = datetime.now(timezone.utc).isoformat()
            err = {
                "ts": now,
                "level": "error",
                "error": str(e),
                "traceback": traceback.format_exc(),
            }
            _emit_report(err)
            logger.error("parity tick failed: %s", e)
        elapsed = time.perf_counter() - started
        sleep_s = max(5.0, INTERVAL_S - elapsed)
        time.sleep(sleep_s)


if __name__ == "__main__":
    sys.exit(main())
