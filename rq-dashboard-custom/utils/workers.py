import logging

from fastapi import HTTPException
from pydantic import BaseModel
from redis import Redis
from rq import Worker
from rq.exceptions import NoSuchJobError

logger = logging.getLogger(__name__)


class WorkerData(BaseModel):
    name: str
    current_job: str | None
    current_job_id: str | None
    successful_job_count: int
    failed_job_count: int
    queues: list[str]


def _worker_key(name: str) -> str:
    return name if name.startswith("rq:worker:") else f"rq:worker:{name}"


def get_workers(redis_url: str) -> list[WorkerData]:
    """List RQ workers; prune stale rq:workers entries (e.g. dead Revit tray keys)."""
    try:
        redis = Redis.from_url(redis_url)
        result = []

        for raw in redis.smembers("rq:workers"):
            name = raw.decode() if isinstance(raw, bytes) else raw
            key = _worker_key(name)
            if not redis.exists(key):
                redis.srem("rq:workers", raw)
                logger.warning("Removed stale worker registry key (no hash): %s", name)
                continue
            try:
                worker = Worker.find_by_key(key, connection=redis)
            except (ValueError, KeyError) as err:
                redis.srem("rq:workers", raw)
                logger.warning("Removed invalid worker registry key %s: %s", name, err)
                continue

            try:
                current_job = worker.get_current_job()
            except NoSuchJobError:
                # Worker hash references a deleted job (empty or stale current_job_id).
                current_job = None
            if current_job is not None:
                result.append(
                    WorkerData(
                        name=worker.name,
                        current_job=current_job.description,
                        current_job_id=current_job.id,
                        successful_job_count=worker.successful_job_count,
                        failed_job_count=worker.failed_job_count,
                        queues=worker.queue_names(),
                    )
                )
            else:
                result.append(
                    WorkerData(
                        name=worker.name,
                        current_job="Idle",
                        current_job_id=None,
                        successful_job_count=worker.successful_job_count,
                        failed_job_count=worker.failed_job_count,
                        queues=worker.queue_names(),
                    )
                )

        return result
    except Exception as error:
        logger.exception("Error reading workers for redis connection: %s", error)
        raise HTTPException(
            status_code=500,
            detail="Error reading workers for redis connection",
        ) from error


def convert_worker_data_to_json_dict(worker_data: list[WorkerData]) -> list[dict]:
    try:
        workers_dict = {}
        for worker in worker_data:
            worker_dict = {
                "name": worker.name,
                "current_job": worker.current_job,
                "current_job_id": worker.current_job_id,
                "successful_job_count": worker.successful_job_count,
                "failed_job_count": worker.failed_job_count,
                "queues": worker.queues,
            }
            workers_dict[worker.name] = worker_dict

        return [workers_dict]
    except Exception as error:
        logger.exception(
            "Error converting worker data list to JSON dictionary: %s", error
        )
        raise HTTPException(
            status_code=500,
            detail="Error converting worker data list to JSON dictionary",
        ) from error


def convert_workers_dict_to_list(input_data: list[dict]) -> list[dict]:
    worker_details = []
    try:
        for workers_dict in input_data:
            for worker_name, worker_data in workers_dict.items():
                worker_details.append(
                    {
                        "worker_name": worker_name,
                        "current_job": worker_data["current_job"],
                        "current_job_id": worker_data["current_job_id"],
                        "successful_job_count": worker_data["successful_job_count"],
                        "failed_job_count": worker_data["failed_job_count"],
                        "queue_name": worker_data["queues"],
                    }
                )
        return worker_details
    except Exception as error:
        logger.exception("Error converting workers dict to list: %s", error)
        raise HTTPException(
            status_code=500, detail="Error converting workers dict to list"
        ) from error
