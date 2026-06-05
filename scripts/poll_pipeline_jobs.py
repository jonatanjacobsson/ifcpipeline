#!/usr/bin/env python3
"""
Monitor IFC Pipeline RQ jobs: wait for queue(s) to drain, poll explicit job IDs
via GET /jobs/{id}/status, then write a JSON report (failures + summaries).

Typical use after an n8n clash burst:
  export IFC_PIPELINE_API_BASE=https://ifcpipeline.example.com
  export IFC_PIPELINE_API_KEY=...
  python3 scripts/poll_pipeline_jobs.py --wait-idle ifcclash -o clash-job-report.json

Poll known job ids until terminal:
  python3 scripts/poll_pipeline_jobs.py abc-123 def-456 -o report.json

Env:
  IFC_PIPELINE_API_BASE   API root (default http://127.0.0.1:8000)
  IFC_PIPELINE_API_KEY    X-API-Key for /jobs/... (required off Docker whitelist)
  REDIS_URL               default redis://127.0.0.1:6379/0 (for --wait-idle)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

TERMINAL = frozenset({"finished", "failed", "stopped", "canceled"})


@dataclass
class JobSnapshot:
    job_id: str
    status: str
    error: Optional[str] = None
    result: Optional[Any] = None
    execution_time_seconds: Optional[float] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    http_error: Optional[str] = None


@dataclass
class Report:
    generated_at: str
    api_base: str
    wait_idle_queues: List[str] = field(default_factory=list)
    idle_reached_at: Optional[str] = None
    polled_job_ids: List[str] = field(default_factory=list)
    jobs: Dict[str, JobSnapshot] = field(default_factory=dict)
    failed_registry_samples: Dict[str, List[str]] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)


def _api_base() -> str:
    return (
        os.environ.get("IFC_PIPELINE_API_BASE")
        or os.environ.get("IFC_PIPELINE_URL")
        or "http://127.0.0.1:8000"
    ).rstrip("/")


def _api_key() -> str:
    return (os.environ.get("IFC_PIPELINE_API_KEY") or "").strip()


def fetch_job_status(base: str, api_key: str, job_id: str) -> JobSnapshot:
    url = f"{base}/jobs/{job_id}/status"
    req = urllib.request.Request(url, headers={"X-API-Key": api_key, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
        return JobSnapshot(
            job_id=data.get("job_id", job_id),
            status=str(data.get("status", "")).lower(),
            error=data.get("error"),
            result=data.get("result"),
            execution_time_seconds=data.get("execution_time_seconds"),
            created_at=_iso(data.get("created_at")),
            started_at=_iso(data.get("started_at")),
            ended_at=_iso(data.get("ended_at")),
        )
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:2000]
        return JobSnapshot(
            job_id=job_id,
            status="http_error",
            http_error=f"{e.code} {e.reason}: {body}",
        )
    except Exception as e:
        return JobSnapshot(job_id=job_id, status="http_error", http_error=str(e))


def _iso(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    return str(v)


def wait_queues_idle(
    redis_url: str,
    queue_names: Sequence[str],
    interval: float,
    timeout: float,
) -> None:
    from redis import Redis
    from rq import Queue

    r = Redis.from_url(redis_url, decode_responses=False)
    deadline = time.monotonic() + timeout if timeout > 0 else None
    qs = [Queue(n, connection=r) for n in queue_names]

    while True:
        busy: List[str] = []
        for name, q in zip(queue_names, qs):
            nq = q.count
            ns = q.started_job_registry.count
            if nq or ns:
                busy.append(f"{name}:queued={nq},started={ns}")
        if not busy:
            return
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError(
                "Queues not idle before timeout: " + "; ".join(busy),
            )
        time.sleep(interval)


def failed_registry_job_ids(redis_url: str, queue_name: str, limit: int) -> List[str]:
    from redis import Redis
    from rq import Queue

    q = Queue(queue_name, connection=Redis.from_url(redis_url))
    ids = list(q.failed_job_registry.get_job_ids())
    ids.reverse()
    return ids[:limit]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("job_ids", nargs="*", help="RQ job UUIDs to poll until terminal")
    p.add_argument("-f", "--file", help="File with one job id per line (merged with positional ids)")
    p.add_argument("--interval", type=float, default=3.0, help="Seconds between polls (default 3)")
    p.add_argument(
        "--timeout",
        type=float,
        default=0.0,
        metavar="SEC",
        help="Max seconds polling explicit job ids until terminal (0 = no limit)",
    )
    p.add_argument(
        "--idle-timeout",
        type=float,
        default=0.0,
        metavar="SEC",
        help="Max seconds to wait for --wait-idle queues to drain (0 = wait indefinitely)",
    )
    p.add_argument(
        "--wait-idle",
        metavar="QUEUES",
        help="Comma-separated RQ queue names; wait until each has queued=0 and started=0",
    )
    p.add_argument(
        "--redis-url",
        default=os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"),
        help="Redis URL for --wait-idle / failed registry scan",
    )
    p.add_argument(
        "--failed-scan-limit",
        type=int,
        default=100,
        help="Max failed job ids per queue to fetch after idle (default 100)",
    )
    p.add_argument("-o", "--output", help="Write JSON report to this path")
    args = p.parse_args()

    base = _api_base()
    api_key = _api_key()
    if not api_key:
        print("ERROR: IFC_PIPELINE_API_KEY is not set.", file=sys.stderr)
        return 1

    report = Report(generated_at=datetime.now(timezone.utc).isoformat(), api_base=base)

    queue_names: List[str] = []
    if args.wait_idle:
        queue_names = [q.strip() for q in args.wait_idle.split(",") if q.strip()]
        report.wait_idle_queues = queue_names
        try:
            idle_cap = args.idle_timeout if args.idle_timeout > 0 else 0.0
            wait_queues_idle(
                args.redis_url,
                queue_names,
                max(0.5, args.interval),
                idle_cap,
            )
            report.idle_reached_at = datetime.now(timezone.utc).isoformat()
        except TimeoutError as e:
            report.notes.append(str(e))
            print(f"WARNING: {e}", file=sys.stderr)

        for qn in queue_names:
            try:
                report.failed_registry_samples[qn] = failed_registry_job_ids(
                    args.redis_url, qn, args.failed_scan_limit
                )
            except Exception as e:
                report.notes.append(f"failed_registry {qn}: {e}")

    job_ids: List[str] = []
    if args.file:
        raw = open(args.file, encoding="utf-8").read().splitlines()
        job_ids.extend(line.strip() for line in raw if line.strip() and not line.strip().startswith("#"))
    job_ids.extend(args.job_ids)
    # merge failed samples when we waited idle and user did not pass explicit ids
    if queue_names and not job_ids:
        seen: set[str] = set()
        for qn, ids in report.failed_registry_samples.items():
            for jid in ids:
                if jid not in seen:
                    seen.add(jid)
                    job_ids.append(jid)
        if not job_ids:
            report.notes.append(
                "No job ids to poll after idle (failed registry empty or scan limit 0).",
            )

    report.polled_job_ids = list(dict.fromkeys(job_ids))

    deadline = time.monotonic() + args.timeout if args.timeout > 0 else None
    pending = set(report.polled_job_ids)

    while pending:
        for jid in list(pending):
            snap = fetch_job_status(base, api_key, jid)
            report.jobs[jid] = snap
            st = snap.status.lower()
            if st in TERMINAL or st == "http_error":
                pending.discard(jid)
        if not pending:
            break
        if deadline is not None and time.monotonic() >= deadline:
            report.notes.append(f"Polling timed out with {len(pending)} job(s) still non-terminal.")
            break
        time.sleep(max(0.5, args.interval))

    # summary counts
    failed = sum(1 for j in report.jobs.values() if j.status == "failed")
    http_err = sum(1 for j in report.jobs.values() if j.status == "http_error")
    summary = {
        "polled": len(report.jobs),
        "failed": failed,
        "http_errors": http_err,
        "still_pending": list(pending),
    }
    out_obj = {**asdict(report), "summary": summary}

    text = json.dumps(out_obj, indent=2, default=str) + "\n"
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"Wrote {args.output}")
    else:
        print(text)

    if pending:
        return 1
    if failed or http_err:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
