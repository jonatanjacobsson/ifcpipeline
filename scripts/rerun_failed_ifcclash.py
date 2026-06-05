#!/usr/bin/env python3
"""
Re-submit failed ifcclash RQ jobs via POST /ifcclash with per-file mode "a".

Typical use after fixing defaults (exclude without selector was misconfigured):

  cd ifcpipeline && set -a && source .env && set +a && \\
    python3 scripts/rerun_failed_ifcclash.py

Env:
  IFC_PIPELINE_API_BASE   default http://127.0.0.1:8000
  IFC_PIPELINE_API_KEY    required
  REDIS_URL               default redis://127.0.0.1:6379/0
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import pickle
import sys
import time
import urllib.error
import urllib.request
import zlib
from typing import Any, Dict, List, Optional, Tuple

import redis

# RQ job pickles reference `shared.classes` (IfcClashRequest / enums). When this
# script is copied to e.g. `/tmp/` inside the api-gateway container, Python would
# otherwise put only `/tmp` on sys.path and unpickling fails.
for _root in (os.environ.get("IFCPIPELINE_APP"), "/app"):
    if _root and os.path.isdir(os.path.join(_root, "shared")):
        sys.path.insert(0, _root)
        break


def _api_base() -> str:
    return (
        os.environ.get("IFC_PIPELINE_API_BASE")
        or os.environ.get("IFC_PIPELINE_URL")
        or "http://127.0.0.1:8000"
    ).rstrip("/")


def _api_key() -> str:
    return (os.environ.get("IFC_PIPELINE_API_KEY") or "").strip()


def _decode_job_body(raw: bytes) -> Optional[Dict[str, Any]]:
    try:
        tup = pickle.loads(zlib.decompress(raw))
    except Exception:
        try:
            tup = pickle.loads(raw)
        except Exception:
            return None
    if not isinstance(tup, tuple) or len(tup) < 3:
        return None
    inner = tup[2]
    if isinstance(inner, (list, tuple)) and inner and isinstance(inner[0], dict):
        return inner[0]
    return None


def _json_safe(obj: Any) -> Any:
    """Recursively turn Enums and other non-JSON types into JSON-friendly values."""
    if hasattr(obj, "value") and not isinstance(obj, (str, int, float, bool)):
        try:
            return obj.value  # Enum
        except Exception:
            pass
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def _normalize_for_rerun(body: Dict[str, Any]) -> Dict[str, Any]:
    out = _json_safe(copy.deepcopy(body))
    for cs in out.get("clash_sets") or []:
        for side in ("a", "b"):
            for f in cs.get(side) or []:
                if not isinstance(f, dict):
                    continue
                f["mode"] = "a"
                if not f.get("selector"):
                    f.pop("selector", None)
    base, ext = os.path.splitext(out.get("output_filename") or "clash.json")
    out["output_filename"] = f"{base}_rerun{ext or '.json'}"
    return out


def _post_ifcclash(base: str, api_key: str, body: Dict[str, Any]) -> Tuple[int, str]:
    url = f"{base}/ifcclash"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "X-API-Key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")[:4000]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--redis-url",
        default=os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"),
    )
    p.add_argument("--limit", type=int, default=0, help="Max jobs to resubmit (0 = all)")
    p.add_argument("--sleep", type=float, default=0.15, help="Seconds between POSTs")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    api_key = _api_key()
    if not api_key:
        print("IFC_PIPELINE_API_KEY is required.", file=sys.stderr)
        return 1

    r = redis.Redis.from_url(args.redis_url, decode_responses=False)
    ids = r.zrevrange("rq:failed:ifcclash", 0, -1)
    if args.limit and args.limit > 0:
        ids = ids[: args.limit]

    base = _api_base()
    ok, bad = 0, 0
    for raw_id in ids:
        jid = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
        raw = r.hget(f"rq:job:{jid}", b"data")
        if not raw:
            print(f"skip {jid}: no job data")
            bad += 1
            continue
        body = _decode_job_body(raw)
        if not body:
            print(f"skip {jid}: unpickle failed")
            bad += 1
            continue
        payload = _normalize_for_rerun(body)
        if args.dry_run:
            print(f"dry-run {jid} -> {payload.get('output_filename')}")
            ok += 1
            continue
        code, text = _post_ifcclash(base, api_key, payload)
        if code == 200:
            ok += 1
            try:
                job_id = json.loads(text).get("job_id")
            except Exception:
                job_id = None
            print(f"ok old={jid} new_job={job_id} out={payload['output_filename']}")
        else:
            bad += 1
            print(f"fail old={jid} http={code} {text[:500]}")
        if args.sleep > 0:
            time.sleep(args.sleep)

    print(f"done submitted={ok} failed_steps={bad} total_ids={len(ids)}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
