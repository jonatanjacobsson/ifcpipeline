import glob
import json
import logging
import os
import pickle
import zlib
from datetime import datetime, timezone
from typing import Any, Optional

from redis import Redis
from rq.job import Job
from rq.queue import Queue
from rq_scheduler import Scheduler

from config import settings
from services.rq_compat import safe_args, safe_description, safe_func_name, safe_status, status_value

logger = logging.getLogger(__name__)


def _utc_to_local(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)


def _utc_to_local_iso(dt: Optional[datetime]) -> Optional[str]:
    """Local wall time as ISO-8601 *with* offset so parsers do not treat it as UTC."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone().isoformat()


def _read_job_result(redis: Redis, job_id: str) -> Any:
    try:
        raw = redis.hget(f"rq:job:{job_id}", "result")
        if not raw:
            return None
        try:
            return pickle.loads(raw)
        except Exception:
            pass
        try:
            return pickle.loads(zlib.decompress(raw))
        except Exception:
            pass
    except Exception:
        pass
    return None


def _get_redis() -> Redis:
    return Redis.from_url(settings.REDIS_URL)


def _repair_job_timestamps(redis: Redis, job_ids: list[str]) -> int:
    """Append missing 'Z' suffix to ISO timestamps so RQ can parse them."""
    fixed = 0
    for jid in job_ids:
        key = f"rq:job:{jid}"
        for field in (b"created_at", b"enqueued_at", b"ended_at", b"started_at"):
            val = redis.hget(key, field)
            if val:
                v = val.decode()
                if v and not v.endswith("Z"):
                    redis.hset(key, field, (v + "Z").encode())
                    fixed += 1
    if fixed:
        logger.info(f"Repaired {fixed} job timestamp(s) missing Z suffix")
    return fixed


# A live RQ worker refreshes its key well inside `worker_ttl` (default 420s). Anything
# quieter than this is a leaked registration, not a worker that is merely idle.
STALE_WORKER_AFTER_SECONDS = int(os.getenv("STALE_WORKER_AFTER_SECONDS", "900"))


def _collect_workers(redis: Redis) -> tuple[list[dict], int]:
    """Return (live workers, stale registration count).

    `Worker.all()` reads the `rq:workers` set, which on some deployments holds bare
    worker names instead of full `rq:worker:<name>` keys — that makes it raise
    ``ValueError: Not a valid RQ worker key`` and take every worker down with it.
    Enumerating the hashes directly is immune to a malformed set.
    """
    from rq.worker import Worker

    workers: list[dict] = []
    stale = 0
    now = datetime.now(timezone.utc)

    try:
        keys = [
            k.decode() if isinstance(k, bytes) else k
            for k in redis.scan_iter(match="rq:worker:*", count=500)
        ]
    except Exception as e:
        logger.warning(f"Failed to scan worker keys: {e}")
        return [], 0

    for key in keys:
        try:
            w = Worker.find_by_key(key, connection=redis)
        except Exception as e:
            logger.debug(f"Skipping unreadable worker key {key}: {e}")
            stale += 1
            continue
        if w is None:
            stale += 1
            continue

        hb = w.last_heartbeat
        if hb is not None:
            if hb.tzinfo is None:
                hb = hb.replace(tzinfo=timezone.utc)
            if (now - hb).total_seconds() > STALE_WORKER_AFTER_SECONDS:
                stale += 1
                continue
        else:
            stale += 1
            continue

        try:
            state = w.get_state()
        except Exception:
            state = "unknown"
        try:
            queues = [q.name for q in w.queues]
        except Exception:
            queues = []

        workers.append({
            "name": w.name,
            "state": getattr(state, "value", state),
            "queues": queues,
            # Inside a container RQ records the container's short id here, which is how
            # a worker maps back to its container for the log viewer.
            "hostname": getattr(w, "hostname", "") or "",
            "pid": getattr(w, "pid", None),
            "current_job_id": w.get_current_job_id(),
            "successful_job_count": w.successful_job_count,
            "failed_job_count": w.failed_job_count,
            "total_working_time": w.total_working_time,
            "birth_date": _utc_to_local_iso(w.birth_date),
            "last_heartbeat": _utc_to_local_iso(w.last_heartbeat),
        })

    workers.sort(key=lambda x: str(x.get("name") or ""))
    if stale:
        logger.info("Skipped %s stale worker registration(s) in Redis", stale)
    return workers, stale


def get_overview() -> dict:
    redis = _get_redis()
    queues = Queue.all(connection=redis)

    queue_stats = []
    total_jobs = 0
    total_failed = 0
    total_started = 0
    total_queued = 0

    for q in queues:
        queued = q.count
        started = q.started_job_registry.count
        failed = q.failed_job_registry.count
        finished = q.finished_job_registry.count
        scheduled = q.scheduled_job_registry.count
        deferred = q.deferred_job_registry.count

        total_jobs += queued + started + failed + finished + scheduled + deferred
        total_failed += failed
        total_started += started
        total_queued += queued

        queue_stats.append({
            "name": q.name,
            "queued": queued,
            "started": started,
            "failed": failed,
            "finished": finished,
            "scheduled": scheduled,
            "deferred": deferred,
        })

    worker_list, stale_workers = _collect_workers(redis)

    info = redis.info()
    redis_info = {
        "connected": True,
        "used_memory_human": info.get("used_memory_human", "?"),
        "used_memory": info.get("used_memory", 0),
        "maxmemory": info.get("maxmemory", 0),
        "connected_clients": info.get("connected_clients", 0),
        "uptime_in_seconds": info.get("uptime_in_seconds", 0),
        "redis_version": info.get("redis_version", "?"),
    }

    return {
        "redis": redis_info,
        "queues": queue_stats,
        "workers": worker_list,
        "totals": {
            "jobs": total_jobs,
            "failed": total_failed,
            "started": total_started,
            "queued": total_queued,
            "workers": len(worker_list),
            "stale_workers": stale_workers,
            "queues": len(queue_stats),
        },
    }


def get_jobs(
    queue_name: str = "all",
    state: str = "all",
    page: int = 1,
    per_page: int = 100,
    sort_col: str = "created_at",
    sort_dir: str = "desc",
) -> dict:
    redis = _get_redis()
    queues = Queue.all(connection=redis)
    scheduler = Scheduler(connection=redis)

    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page

    all_jobs = []

    for q in queues:
        if queue_name != "all" and q.name != queue_name:
            continue

        # "live" = everything still in flight or needing action; excludes `finished`,
        # which otherwise buries the queue under thousands of completed jobs. Finished
        # work belongs to History.
        live = state == "live"

        job_ids = []
        if live or state in ("all", "queued"):
            job_ids.extend([(jid, "queued") for jid in q.get_job_ids()])
        if live or state in ("all", "started"):
            job_ids.extend([(jid, "started") for jid in q.started_job_registry.get_job_ids()])
        if live or state in ("all", "failed"):
            job_ids.extend([(jid, "failed") for jid in q.failed_job_registry.get_job_ids()])
        if state in ("all", "finished"):
            job_ids.extend([(jid, "finished") for jid in q.finished_job_registry.get_job_ids()])
        if live or state in ("all", "scheduled"):
            job_ids.extend([(jid, "scheduled") for jid in q.scheduled_job_registry.get_job_ids()])
        if live or state in ("all", "deferred"):
            job_ids.extend([(jid, "deferred") for jid in q.deferred_job_registry.get_job_ids()])

        raw_ids = [jid for jid, _ in job_ids]
        status_map = {jid: st for jid, st in job_ids}

        try:
            fetched = Job.fetch_many(raw_ids, connection=redis)
        except Exception:
            _repair_job_timestamps(redis, raw_ids)
            try:
                fetched = Job.fetch_many(raw_ids, connection=redis)
            except Exception as e:
                logger.warning(f"Failed to fetch jobs for queue {q.name} after repair: {e}")
                continue

        for job in fetched:
            if job is None:
                continue
            all_jobs.append({
                "id": job.id,
                "name": safe_description(job),
                "queue": q.name,
                "status": status_value(status_map.get(job.id) or job.get_status()),
                "created_at": _utc_to_local_iso(job.created_at),
                "enqueued_at": _utc_to_local_iso(job.enqueued_at),
                "ended_at": _utc_to_local_iso(job.ended_at),
                "meta": job.meta or {},
            })

    rev = (sort_dir or "desc").lower() == "desc"

    def _sort_key(j: dict) -> str:
        sc = (sort_col or "created_at").lower()
        if sc == "id":
            return str(j.get("id") or "")
        if sc == "status":
            return str(j.get("status") or "")
        if sc == "queue":
            return str(j.get("queue") or "")
        if sc == "name":
            return str(j.get("name") or "").lower()
        if sc.startswith("meta:"):
            mk = sc[5:]
            return str((j.get("meta") or {}).get(mk) or "").lower()
        return str(j.get("created_at") or "")

    all_jobs.sort(key=_sort_key, reverse=rev)
    paginated = all_jobs[start_idx:end_idx]

    available_queues = sorted(set(q.name for q in queues))

    return {
        "jobs": paginated,
        "total": len(all_jobs),
        "page": page,
        "per_page": per_page,
        "queues": available_queues,
    }


def get_job_detail(job_id: str) -> dict:
    redis = _get_redis()
    job = Job.fetch(job_id, connection=redis)

    result = _read_job_result(redis, job_id)
    if result is None:
        result = job.result

    log_files = []
    logs_dir = settings.WORKER_LOGS_DIR
    pattern = os.path.join(logs_dir, f"{job_id}-*")
    for path in sorted(glob.glob(pattern)):
        fname = os.path.basename(path)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        log_files.append({"filename": fname, "size_bytes": size})

    args = safe_args(job)

    return {
        "id": job.id,
        "name": safe_description(job),
        "queue": job.origin or "",
        "func_name": safe_func_name(job),
        "args": args,
        "created_at": _utc_to_local_iso(job.created_at),
        "enqueued_at": _utc_to_local_iso(job.enqueued_at),
        "ended_at": _utc_to_local_iso(job.ended_at),
        "status": safe_status(job),
        "result": result,
        "exc_info": job.exc_info,
        "meta": job.meta or {},
        "log_files": log_files,
    }


def delete_job(job_id: str):
    redis = _get_redis()
    job = Job.fetch(job_id, connection=redis)
    job.delete()


def requeue_job(job_id: str):
    redis = _get_redis()
    job = Job.fetch(job_id, connection=redis)
    job.requeue()


def get_log_content(job_id: str, filename: str) -> str:
    safe_name = os.path.basename(filename)
    file_path = os.path.join(settings.WORKER_LOGS_DIR, safe_name)
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"Log file not found: {safe_name}")
    with open(file_path, "r", errors="replace") as f:
        return f.read()
