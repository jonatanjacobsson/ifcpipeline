import json as _json
import logging
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Optional

import psycopg2
from psycopg2.extras import RealDictCursor, Json
from redis import Redis
from rq.job import Job
from rq.queue import Queue

from config import settings
from services.rq_compat import safe_args, safe_description, safe_func_name, status_value

logger = logging.getLogger(__name__)

# Max rows held in memory per tick; flush to Postgres and clear to cap RSS during large queue scans.
JOB_HISTORY_SYNC_CHUNK = int(os.getenv("JOB_HISTORY_SYNC_CHUNK", "300"))

REGISTRIES = (
    "started_job_registry",
    "failed_job_registry",
    "finished_job_registry",
    "scheduled_job_registry",
    "deferred_job_registry",
)


@contextmanager
def _get_conn():
    conn = psycopg2.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        dbname=settings.POSTGRES_DB,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
    )
    try:
        yield conn
    finally:
        conn.close()


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


def _utc_to_local(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)


def _local_naive_to_iso(dt: Optional[datetime]) -> Optional[str]:
    """TIMESTAMP without tz is server-local wall time; include offset for time_ago/JS."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        tz = datetime.now().astimezone().tzinfo
        dt = dt.replace(tzinfo=tz)
    return dt.isoformat()


def _ensure_table(conn):
    """Create the job_history table if it doesn't exist (idempotent)."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS job_history (
                id          SERIAL PRIMARY KEY,
                job_id      TEXT UNIQUE NOT NULL,
                queue       TEXT NOT NULL,
                status      TEXT NOT NULL,
                func_name   TEXT,
                description TEXT,
                args        JSONB,
                meta        JSONB,
                exc_info    TEXT,
                created_at  TIMESTAMP,
                enqueued_at TIMESTAMP,
                ended_at    TIMESTAMP,
                synced_at   TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
    conn.commit()


def _flush_job_history_rows(cur, rows: list) -> None:
    """Upsert one chunk of job_history rows (caller commits)."""
    for r in rows:
        cur.execute(
            """
            INSERT INTO job_history
                (job_id, queue, status, func_name, description,
                 args, meta, exc_info, created_at, enqueued_at, ended_at, synced_at)
            VALUES
                (%(job_id)s, %(queue)s, %(status)s, %(func_name)s, %(description)s,
                 %(args)s, %(meta)s, %(exc_info)s, %(created_at)s, %(enqueued_at)s,
                 %(ended_at)s, NOW())
            ON CONFLICT (job_id) DO UPDATE SET
                status      = EXCLUDED.status,
                func_name   = COALESCE(EXCLUDED.func_name, job_history.func_name),
                description = COALESCE(EXCLUDED.description, job_history.description),
                args        = COALESCE(EXCLUDED.args, job_history.args),
                meta        = COALESCE(EXCLUDED.meta, job_history.meta),
                exc_info    = COALESCE(EXCLUDED.exc_info, job_history.exc_info),
                ended_at    = COALESCE(EXCLUDED.ended_at, job_history.ended_at),
                synced_at   = NOW()
        """,
            {
                **r,
                "args": Json(r["args"]) if r["args"] else None,
                "meta": Json(r["meta"]) if r["meta"] else None,
            },
        )


def sync_redis_to_postgres():
    """Scan all RQ queues/registries and upsert every job into Postgres."""
    try:
        redis = _get_redis()
        queues = Queue.all(connection=redis)
    except Exception as e:
        logger.error(f"Job history sync: failed to connect to Redis: {e}")
        return

    batch: list[dict] = []
    total = 0
    try:
        with _get_conn() as conn:
            _ensure_table(conn)
            with conn.cursor() as cur:
                for q in queues:
                    job_ids_with_status: list[tuple[str, str]] = []
                    job_ids_with_status.extend((jid, "queued") for jid in q.get_job_ids())
                    for reg_name in REGISTRIES:
                        reg = getattr(q, reg_name, None)
                        if reg:
                            status_label = reg_name.replace("_job_registry", "")
                            job_ids_with_status.extend(
                                (jid, status_label) for jid in reg.get_job_ids()
                            )

                    raw_ids = [jid for jid, _ in job_ids_with_status]
                    status_map = {jid: st for jid, st in job_ids_with_status}

                    if not raw_ids:
                        continue

                    try:
                        fetched = Job.fetch_many(raw_ids, connection=redis)
                    except Exception:
                        _repair_job_timestamps(redis, raw_ids)
                        try:
                            fetched = Job.fetch_many(raw_ids, connection=redis)
                        except Exception as e:
                            logger.warning(
                                f"Job history sync: failed to fetch jobs for queue {q.name}: {e}"
                            )
                            continue

                    for job in fetched:
                        if job is None:
                            continue
                        args = safe_args(job)
                        batch.append(
                            {
                                "job_id": job.id,
                                "queue": q.name,
                                "status": status_value(status_map.get(job.id) or job.get_status()),
                                "func_name": safe_func_name(job),
                                "description": safe_description(job),
                                "args": args,
                                "meta": job.meta or {},
                                "exc_info": job.exc_info,
                                "created_at": _utc_to_local(job.created_at),
                                "enqueued_at": _utc_to_local(job.enqueued_at),
                                "ended_at": _utc_to_local(job.ended_at),
                            }
                        )
                        if len(batch) >= JOB_HISTORY_SYNC_CHUNK:
                            _flush_job_history_rows(cur, batch)
                            conn.commit()
                            total += len(batch)
                            batch.clear()

                if batch:
                    _flush_job_history_rows(cur, batch)
                    conn.commit()
                    total += len(batch)
        if total:
            logger.info(f"Job history sync: upserted {total} jobs")
    except Exception as e:
        # Include the type: some RQ exceptions (DeserializationError) stringify to "".
        logger.error(
            "Job history sync: Postgres write failed after %s rows: %s: %s",
            total, type(e).__name__, e, exc_info=True,
        )


def get_jobs_from_history(
    queue: str = "all",
    status: str = "all",
    search: str = "",
    page: int = 1,
    per_page: int = 100,
    sort_col: str = "created_at",
    sort_dir: str = "desc",
) -> dict:
    """Read job history from Postgres with filters and pagination."""
    try:
        with _get_conn() as conn:
            _ensure_table(conn)
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                conditions = []
                params: dict = {}

                if queue != "all":
                    conditions.append("queue = %(queue)s")
                    params["queue"] = queue

                if status != "all":
                    conditions.append("status = %(status)s")
                    params["status"] = status

                if search:
                    conditions.append(
                        "(job_id ILIKE %(search)s OR description ILIKE %(search)s "
                        "OR func_name ILIKE %(search)s OR meta::text ILIKE %(search)s)"
                    )
                    params["search"] = f"%{search}%"

                where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

                cur.execute("SELECT COUNT(*) as total FROM job_history")
                total_all = cur.fetchone()["total"]

                cur.execute(f"SELECT COUNT(*) as total FROM job_history {where}", params)
                total = cur.fetchone()["total"]

                offset = (page - 1) * per_page
                _allowed_order = {
                    "created_at": "created_at",
                    "ended_at": "ended_at",
                    "status": "status",
                    "queue": "queue",
                    "func_name": "func_name",
                    "job_id": "job_id",
                    "name": "description",
                }
                order_col = _allowed_order.get((sort_col or "created_at").lower(), "created_at")
                order_dir = "DESC" if (sort_dir or "desc").lower() == "desc" else "ASC"
                cur.execute(f"""
                    SELECT job_id, queue, status, func_name, description,
                           args, meta, exc_info, created_at, enqueued_at, ended_at
                    FROM job_history
                    {where}
                    ORDER BY {order_col} {order_dir} NULLS LAST
                    LIMIT %(limit)s OFFSET %(offset)s
                """, {**params, "limit": per_page, "offset": offset})

                jobs = []
                for row in cur.fetchall():
                    r = dict(row)
                    for key in ("created_at", "enqueued_at", "ended_at"):
                        if r.get(key):
                            r[key] = _local_naive_to_iso(r[key])
                    jobs.append({
                        "id": r["job_id"],
                        "name": r["description"] or "",
                        "queue": r["queue"],
                        "status": r["status"],
                        "func_name": r["func_name"],
                        "created_at": r["created_at"],
                        "enqueued_at": r["enqueued_at"],
                        "ended_at": r["ended_at"],
                        "meta": r["meta"] or {},
                        "args": r["args"],
                        "exc_info": r["exc_info"],
                    })

                cur.execute("SELECT DISTINCT queue FROM job_history ORDER BY queue")
                available_queues = [row["queue"] for row in cur.fetchall()]

                cur.execute("""
                    SELECT status, COUNT(*) as cnt
                    FROM job_history GROUP BY status
                """)
                status_counts = {row["status"]: row["cnt"] for row in cur.fetchall()}

                return {
                    "jobs": jobs,
                    "total": total,
                    "total_all": total_all,
                    "page": page,
                    "per_page": per_page,
                    "queues": available_queues,
                    "status_counts": status_counts,
                }
    except Exception as e:
        logger.error(f"Failed to read job history: {e}")
        return {
            "jobs": [],
            "total": 0,
            "page": page,
            "per_page": per_page,
            "queues": [],
            "status_counts": {},
            "error": str(e),
        }


def get_job_from_history(job_id: str) -> dict | None:
    """Fetch a single job from Postgres history. Returns None if not found."""
    try:
        with _get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT job_id, queue, status, func_name, description,
                           args, meta, exc_info, created_at, enqueued_at, ended_at
                    FROM job_history WHERE job_id = %s
                """, (job_id,))
                row = cur.fetchone()
                if not row:
                    return None
                r = dict(row)
                for key in ("created_at", "enqueued_at", "ended_at"):
                    if r.get(key):
                        r[key] = _local_naive_to_iso(r[key])
                return {
                    "id": r["job_id"],
                    "name": r["description"] or "",
                    "queue": r["queue"],
                    "status": r["status"],
                    "func_name": r["func_name"],
                    "created_at": r["created_at"],
                    "enqueued_at": r["enqueued_at"],
                    "ended_at": r["ended_at"],
                    "meta": r["meta"] or {},
                    "args": r["args"],
                    "exc_info": r["exc_info"],
                    "result": None,
                    "log_files": [],
                    "source": "history",
                }
    except Exception as e:
        logger.error(f"Failed to fetch job {job_id} from history: {e}")
        return None
