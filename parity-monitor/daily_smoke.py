"""SeaweedFS pilot — daily lifecycle + versioning smoke.

Each iteration (every SMOKE_INTERVAL_S, default 86400) PUTs a 1 MiB key to
the `parity_test/` prefix, overwrites it (creating a non-current version),
verifies versioning, and ensures a 1-day NoncurrentVersionExpiration
lifecycle rule scoped to the prefix.

Lifecycle is validated with a **cross-day deferred (canary) check** rather
than an in-iteration blocking wait. A standard S3 ``NoncurrentDays: 1`` rule
is, by definition, never satisfiable within minutes — the prior code waited
only SMOKE_LIFECYCLE_WAIT_S (1800s / 30 min) for a 1-day rule, so the
lifecycle gate could *never* pass on any S3 backend (SeaweedFS, MinIO or
AWS). That was a test-design bug, not a SeaweedFS defect.

Instead, each run plants a "canary" (the non-current version id created by
overwriting today's key) into a small state file, and on subsequent runs
checks whether previously-planted canaries have been reaped. A canary still
present beyond SMOKE_LIFECYCLE_GRACE_S (default 2 days) is the real
lifecycle-broken signal.

Writes /reports/smoke-YYYY-MM-DD.json plus /reports/smoke-latest.json and
maintains /reports/smoke-lifecycle-state.json. Never raises out of the loop.
Designed to be cheap (only 1 MiB / day) but exercises every SeaweedFS
behavior the audit pipeline depends on.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import boto3
from botocore.config import Config


REPORTS_DIR = os.environ.get("S3_SHADOW_REPORTS_DIR", "/reports")
INTERVAL_S = int(os.environ.get("SMOKE_INTERVAL_S", "86400"))
# Grace window after a version becomes non-current before we treat it as a
# genuine lifecycle failure. Must exceed the rule's NoncurrentDays (1 day)
# plus the backend's sweep cadence; default 2 days.
LIFECYCLE_GRACE_S = int(os.environ.get("SMOKE_LIFECYCLE_GRACE_S", str(2 * 86400)))
TEST_PREFIX = os.environ.get("SMOKE_TEST_PREFIX", "parity_test/")
STATE_PATH = os.path.join(REPORTS_DIR, "smoke-lifecycle-state.json")
# Cap on tracked canaries so a permanently-broken lifecycle can't grow the
# state file without bound.
MAX_PENDING_CANARIES = 10

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("smoke")


def _client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["S3_SHADOW_ENDPOINT_URL"],
        region_name=os.environ.get("S3_SHADOW_REGION", "us-east-1"),
        aws_access_key_id=os.environ["S3_SHADOW_ACCESS_KEY"],
        aws_secret_access_key=os.environ["S3_SHADOW_SECRET_KEY"],
        config=Config(
            s3={"addressing_style": "path"},
            signature_version="s3v4",
            connect_timeout=5,
            read_timeout=30,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )


def _ensure_lifecycle_rule(client, bucket: str) -> Dict[str, Any]:
    """Apply an idempotent lifecycle rule scoped to TEST_PREFIX. SeaweedFS
    accepts the same PutBucketLifecycleConfiguration shape as AWS S3."""
    config = {
        "Rules": [
            {
                "ID": "parity-test-noncurrent-expire",
                "Status": "Enabled",
                "Filter": {"Prefix": TEST_PREFIX},
                "NoncurrentVersionExpiration": {"NoncurrentDays": 1},
            }
        ]
    }
    try:
        client.put_bucket_lifecycle_configuration(
            Bucket=bucket,
            LifecycleConfiguration=config,
        )
        return {"applied": True}
    except Exception as e:
        logger.warning("put_bucket_lifecycle failed: %s", e)
        return {"applied": False, "error": str(e), "error_class": type(e).__name__}


def _put_random(client, bucket: str, key: str, n_bytes: int) -> Dict[str, Any]:
    body = secrets.token_bytes(n_bytes)
    resp = client.put_object(Bucket=bucket, Key=key, Body=body)
    return {
        "version_id": resp.get("VersionId"),
        "etag": (resp.get("ETag") or "").strip('"'),
        "size": n_bytes,
    }


def _head(client, bucket: str, key: str, *, version_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    kwargs = {"Bucket": bucket, "Key": key, "ChecksumMode": "ENABLED"}
    if version_id:
        kwargs["VersionId"] = version_id
    try:
        resp = client.head_object(**kwargs)
    except Exception:
        return None
    return {
        "size": resp.get("ContentLength"),
        "etag": (resp.get("ETag") or "").strip('"'),
        "version_id": resp.get("VersionId"),
    }


def _list_versions(client, bucket: str, prefix: str) -> List[Dict[str, Any]]:
    try:
        resp = client.list_object_versions(Bucket=bucket, Prefix=prefix)
    except Exception as e:
        return [{"error": str(e), "error_class": type(e).__name__}]
    out: List[Dict[str, Any]] = []
    for v in resp.get("Versions", []) or []:
        out.append({
            "key": v.get("Key"),
            "version_id": v.get("VersionId"),
            "is_latest": v.get("IsLatest"),
            "size": v.get("Size"),
        })
    for m in resp.get("DeleteMarkers", []) or []:
        out.append({
            "key": m.get("Key"),
            "version_id": m.get("VersionId"),
            "is_latest": m.get("IsLatest"),
            "delete_marker": True,
        })
    return out


def _load_state() -> Dict[str, Any]:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("pending"), list):
            return data
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning("could not read lifecycle state (%s); starting fresh", e)
    return {"pending": []}


def _save_state(state: Dict[str, Any]) -> None:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True, default=str)
    os.replace(tmp, STATE_PATH)


def _version_present(client, bucket: str, key: str, version_id: str) -> bool:
    versions = _list_versions(client, bucket, key)
    return any(v.get("version_id") == version_id for v in versions)


def _check_pending_canaries(client, bucket: str, pending: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Cross-day lifecycle check. For every canary planted on a previous run,
    see whether its non-current version has been reaped.

    Returns a result dict and the surviving pending list. `reaped`:
      - True  : at least one canary was due and every due canary was reaped
      - False : a canary is overdue (still present beyond the grace window)
      - None  : nothing was due yet (first run, or all within grace) — neutral
    """
    now = time.time()
    reaped: List[Dict[str, Any]] = []
    overdue: List[Dict[str, Any]] = []
    waiting: List[Dict[str, Any]] = []
    survivors: List[Dict[str, Any]] = []

    for c in pending:
        key = c.get("key")
        vid = c.get("version_id")
        planted = float(c.get("planted_at") or 0)
        age_s = round(now - planted, 1)
        if not key or not vid:
            continue
        present = _version_present(client, bucket, key, vid)
        entry = {"key": key, "version_id": vid, "age_s": age_s}
        if not present:
            reaped.append(entry)  # gone => lifecycle did its job
        elif age_s > LIFECYCLE_GRACE_S:
            overdue.append(entry)
            survivors.append(c)  # keep tracking until it eventually reaps
        else:
            waiting.append(entry)
            survivors.append(c)

    if overdue:
        verdict: Optional[bool] = False
    elif reaped:
        verdict = True
    else:
        verdict = None

    result = {
        "reaped": verdict,
        "checked": len(pending),
        "reaped_canaries": reaped,
        "overdue_canaries": overdue,
        "waiting_canaries": waiting,
        "grace_s": LIFECYCLE_GRACE_S,
    }
    # Surface the oldest reaped age as elapsed_s for the weekly-summary table.
    if reaped:
        result["elapsed_s"] = max(e["age_s"] for e in reaped)
    if overdue:
        result["note"] = (
            f"{len(overdue)} non-current version(s) NOT reaped within "
            f"{LIFECYCLE_GRACE_S}s grace — lifecycle may be broken on this build."
        )
    elif verdict is None:
        result["note"] = (
            "No canary due yet (first run or all within grace) — lifecycle "
            "result will be available on a later run."
        )
    return result, survivors


def _emit(report: Dict[str, Any]) -> None:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    day = datetime.now(timezone.utc).date().isoformat()
    path = os.path.join(REPORTS_DIR, f"smoke-{day}.json")
    latest = os.path.join(REPORTS_DIR, "smoke-latest.json")
    # If today already has a report, append a numeric suffix so a manual
    # re-run doesn't clobber it.
    if os.path.exists(path):
        i = 1
        while os.path.exists(os.path.join(REPORTS_DIR, f"smoke-{day}.{i}.json")):
            i += 1
        path = os.path.join(REPORTS_DIR, f"smoke-{day}.{i}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)
    tmp = latest + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)
    os.replace(tmp, latest)


def _one_iteration(client, bucket: str, state: Dict[str, Any]) -> Dict[str, Any]:
    ts = datetime.now(timezone.utc)
    key = f"{TEST_PREFIX}daily-{ts.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}.bin"
    report: Dict[str, Any] = {
        "ts": ts.isoformat(),
        "bucket": bucket,
        "key": key,
        "steps": {},
        "level": "ok",
    }

    # Step 1: lifecycle config (idempotent)
    report["steps"]["lifecycle"] = _ensure_lifecycle_rule(client, bucket)

    # Step 2: PUT v1
    v1 = _put_random(client, bucket, key, 1 << 20)
    h1 = _head(client, bucket, key)
    report["steps"]["put_v1"] = {
        "put": v1,
        "head": h1,
        "size_match": (h1 or {}).get("size") == v1["size"],
    }
    if not report["steps"]["put_v1"]["size_match"]:
        report["level"] = "warn"

    # Step 3: PUT v2 (different bytes, same key → new VersionId)
    v2 = _put_random(client, bucket, key, 1 << 20)
    h2 = _head(client, bucket, key)
    report["steps"]["put_v2"] = {
        "put": v2,
        "head": h2,
        "different_version": bool(v1.get("version_id")) and v2.get("version_id") != v1.get("version_id"),
    }
    if not report["steps"]["put_v2"]["different_version"]:
        report["level"] = "warn"

    # Step 4: list versions
    versions = _list_versions(client, bucket, key)
    report["steps"]["list_versions"] = {
        "versions": versions,
        "count": len(versions),
        "expected_count": 2,
        "match": len(versions) == 2,
    }
    if len(versions) != 2:
        report["level"] = "warn"

    # Step 5: lifecycle reap — cross-day deferred (canary) check.
    # Verify canaries planted on previous runs have been reaped by the 1-day
    # NoncurrentVersionExpiration rule. A standard S3 lifecycle rule cannot
    # reap within one iteration, so we never block here.
    pending = state.get("pending", [])
    reap, survivors = _check_pending_canaries(client, bucket, pending)
    report["steps"]["lifecycle_reap"] = reap
    if reap.get("reaped") is False:
        # The genuine lifecycle-broken signal.
        report["level"] = "warn"

    # Step 6: plant today's canary. After the v2 overwrite above, v1 is now a
    # non-current version eligible for the 1-day rule; track it for a future
    # run to confirm it gets reaped.
    if v1.get("version_id"):
        survivors.append({
            "key": key,
            "version_id": v1["version_id"],
            "planted_at": time.time(),
            "planted_ts": ts.isoformat(),
        })
        report["steps"]["plant_canary"] = {"key": key, "version_id": v1["version_id"]}
    else:
        report["steps"]["plant_canary"] = {
            "skipped": True,
            "reason": "v1 PUT did not return a VersionId (bucket may not be versioned)",
        }
        report["level"] = "warn"

    # Keep only the most recent canaries so a broken lifecycle can't grow the
    # state unbounded.
    state["pending"] = survivors[-MAX_PENDING_CANARIES:]

    # Step 7: cleanup — delete the current version so only non-current
    # versions remain for the lifecycle rule to sweep.
    try:
        client.delete_object(Bucket=bucket, Key=key)
        report["steps"]["cleanup"] = {"deleted": True}
    except Exception as e:
        report["steps"]["cleanup"] = {
            "deleted": False, "error": str(e), "error_class": type(e).__name__,
        }
        report["level"] = "warn"

    return report


def main() -> int:
    if not os.environ.get("S3_SHADOW_ENDPOINT_URL"):
        logger.error(
            "S3_SHADOW_ENDPOINT_URL not set — refusing to start daily smoke."
        )
        return 2
    bucket = os.environ.get("S3_SHADOW_BUCKET", "ifcpipeline")
    client = _client()
    logger.info(
        "daily-smoke starting: interval=%ss lifecycle_grace=%ss prefix=%s endpoint=%s",
        INTERVAL_S, LIFECYCLE_GRACE_S, TEST_PREFIX,
        os.environ.get("S3_SHADOW_ENDPOINT_URL"),
    )
    while True:
        try:
            state = _load_state()
            report = _one_iteration(client, bucket, state)
            _save_state(state)
            _emit(report)
            logger.info(
                "daily-smoke level=%s steps=%s",
                report["level"],
                {k: (v.get("level") or ("ok" if not isinstance(v, dict) or "error" not in v else "err"))
                 for k, v in report["steps"].items()},
            )
        except Exception as e:
            err = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "level": "error",
                "error": str(e),
                "traceback": traceback.format_exc(),
            }
            _emit(err)
            logger.error("daily-smoke iteration failed: %s", e)
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    sys.exit(main())
