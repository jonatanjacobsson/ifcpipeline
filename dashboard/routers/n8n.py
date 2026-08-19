from fastapi import APIRouter, Query

from services import n8n_service, cache

router = APIRouter(prefix="/api/n8n", tags=["n8n"])


@router.get("/status")
def status():
    return n8n_service.check_health()


@router.get("/workflows")
def workflows():
    return cache.get_or_set("n8n_workflows", 120, n8n_service.get_workflows)


@router.get("/executions")
def executions(limit: int = Query(20, ge=1, le=200), status: str | None = None):
    key = f"n8n_executions_{limit}_{status}"
    return cache.get_or_set(key, 60, n8n_service.get_executions, limit, status)
