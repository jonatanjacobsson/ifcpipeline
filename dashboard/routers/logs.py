"""Live container logs (SSE), scoped to this compose project's own containers."""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from services import docker_logs, redis_service

router = APIRouter(prefix="/api/logs", tags=["Logs"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    # Stop nginx/traefik from buffering the stream into uselessness.
    "X-Accel-Buffering": "no",
}


@router.get("/status")
def status():
    return {
        "available": docker_logs.available(),
        "socket": docker_logs.available() and "mounted" or "not mounted",
        "project": docker_logs.own_project(),
    }


@router.get("/containers")
def containers():
    """Containers whose logs may be streamed."""
    return docker_logs.list_containers()


def _targets(container: str | None, queue: str | None) -> list[dict]:
    if container:
        c = docker_logs.resolve(container)
        if c is None:
            # Either it does not exist or it is outside this compose project. Do not
            # distinguish the two — that would confirm whether a container id exists.
            raise HTTPException(status_code=404, detail="Unknown container")
        return [c]

    if queue:
        try:
            workers = (redis_service.get_overview() or {}).get("workers") or []
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Redis unavailable: {e}")
        found = docker_logs.containers_for_queue(queue, workers)
        if not found:
            raise HTTPException(
                status_code=404,
                detail=f"No running worker container is serving queue '{queue}'",
            )
        return found

    raise HTTPException(status_code=400, detail="Pass either container= or queue=")


@router.get("/stream")
async def stream(
    container: str | None = Query(None),
    queue: str | None = Query(None),
    tail: int = Query(200, ge=0, le=5000),
    follow: bool = Query(True),
):
    if not docker_logs.available():
        raise HTTPException(
            status_code=503,
            detail="Docker socket is not mounted; live logs are unavailable",
        )
    targets = _targets(container, queue)
    return StreamingResponse(
        docker_logs.stream(targets, tail=tail, follow=follow),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
