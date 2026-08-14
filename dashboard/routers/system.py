from fastapi import APIRouter

from services import db_service, redis_service, n8n_service, network_share_service, cache

router = APIRouter(prefix="/api/system", tags=["System"])


def _collect_health():
    redis_ok = False
    try:
        overview = redis_service.get_overview()
        redis_ok = overview.get("redis", {}).get("connected", False)
    except Exception:
        pass

    return {
        "redis": {"connected": redis_ok},
        "postgres": db_service.check_health(),
        "n8n": n8n_service.check_health(),
        "network_share": network_share_service.check_mount(),
    }


@router.get("/health")
def health():
    return cache.get_or_set("system_health", 15, _collect_health)


@router.get("/db/stats")
def db_stats():
    return cache.get_or_set("db_stats", 60, db_service.get_stats)


@router.get("/db/tester-results")
def tester_results(limit: int = 50, offset: int = 0):
    return db_service.get_tester_results(limit, offset)


@router.get("/db/clash-results")
def clash_results(limit: int = 50, offset: int = 0):
    return db_service.get_clash_results(limit, offset)
