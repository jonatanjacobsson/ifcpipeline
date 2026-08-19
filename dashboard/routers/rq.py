import re

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from services import redis_service, job_history_service, cache

router = APIRouter(prefix="/api/rq", tags=["Redis Queue"])


@router.get("/overview")
def overview():
    try:
        return cache.get_or_set("rq_overview", 5, redis_service.get_overview)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/jobs")
def list_jobs(
    queue: str = Query("all"),
    state: str = Query("all"),
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=500),
):
    try:
        return redis_service.get_jobs(queue, state, page, per_page)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/jobs/{job_id}")
def job_detail(job_id: str):
    if not re.match(r"^[\w\-]+$", job_id):
        raise HTTPException(status_code=400, detail="Invalid job_id")
    try:
        return redis_service.get_job_detail(job_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str):
    if not re.match(r"^[\w\-]+$", job_id):
        raise HTTPException(status_code=400, detail="Invalid job_id")
    try:
        redis_service.delete_job(job_id)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/jobs/{job_id}/requeue")
def requeue_job(job_id: str):
    if not re.match(r"^[\w\-]+$", job_id):
        raise HTTPException(status_code=400, detail="Invalid job_id")
    try:
        redis_service.requeue_job(job_id)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history")
def job_history(
    queue: str = Query("all"),
    status: str = Query("all"),
    search: str = Query(""),
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=500),
):
    try:
        return job_history_service.get_jobs_from_history(queue, status, search, page, per_page)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history/{job_id}")
def job_history_detail(job_id: str):
    if not re.match(r"^[\w\-]+$", job_id):
        raise HTTPException(status_code=400, detail="Invalid job_id")
    try:
        result = job_history_service.get_job_from_history(job_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Job not found in history")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/jobs/{job_id}/logs/{filename}")
def get_log_content(job_id: str, filename: str):
    if not re.match(r"^[\w\-]+$", job_id):
        raise HTTPException(status_code=400, detail="Invalid job_id")
    try:
        content = redis_service.get_log_content(job_id, filename)
        return PlainTextResponse(content)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
