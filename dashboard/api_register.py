"""Shared FastAPI wiring: CORS, lifespan (background sync), JSON API routers."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import n8n, network_share, rq, system
from services import job_history_service

logger = logging.getLogger(__name__)

SYNC_INTERVAL = int(os.getenv("JOB_HISTORY_SYNC_INTERVAL", "90"))


def _background_pg_sync_enabled() -> bool:
    """Only one process should mirror Redis→Postgres; set false on extra replicas."""
    v = os.getenv("ENABLE_BACKGROUND_PG_SYNC", "true").strip().lower()
    return v in ("1", "true", "yes", "on")


async def _sync_loop():
    """Persist finished jobs before RQ's result TTL drops them, so History survives."""
    await asyncio.sleep(10)
    while True:
        try:
            await asyncio.to_thread(job_history_service.sync_redis_to_postgres)
        except Exception as e:
            logger.error(f"Job history sync error: {e}")
        await asyncio.sleep(SYNC_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if _background_pg_sync_enabled():
        sync_task = asyncio.create_task(_sync_loop())
        logger.info("Background PG sync started (interval=%ss)", SYNC_INTERVAL)
    else:
        sync_task = None
        logger.info("Background PG sync disabled (ENABLE_BACKGROUND_PG_SYNC=false)")
    yield
    if sync_task is not None:
        sync_task.cancel()
        try:
            await sync_task
        except asyncio.CancelledError:
            pass
        logger.info("Background PG sync stopped")


def register_cors_middleware(app: FastAPI) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )


def register_json_api_routers(app: FastAPI) -> None:
    """Mount JSON `/api/*` routers (no HTMX / HTML routes)."""
    app.include_router(rq.router)
    app.include_router(system.router)
    app.include_router(n8n.router)
    app.include_router(network_share.router)
