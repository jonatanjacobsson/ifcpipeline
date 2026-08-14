"""IFC Pipeline HTMX dashboard — server-rendered UI over the shared `/api/*` layer."""

import logging
import os
import sys

# Repo layout: `app/` next to `dashboard/`; imports use `services`, `routers`, `config` from dashboard.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_DASH = os.path.join(_ROOT, "dashboard")
if _DASH not in sys.path:
    sys.path.insert(0, _DASH)
_APP = os.path.abspath(os.path.dirname(__file__))
if _APP not in sys.path:
    sys.path.insert(0, _APP)

import uvicorn
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from api_register import lifespan, register_cors_middleware, register_json_api_routers
from pipeline_ui.routes import router as htmx_router

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

app = FastAPI(title="IFC Pipeline Dashboard", version="1.0.0", lifespan=lifespan)

register_cors_middleware(app)
register_json_api_routers(app)
app.include_router(htmx_router)

static_dir = os.path.join(_DASH, "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/", include_in_schema=False)
def index():
    return RedirectResponse(url="/htmx/", status_code=307)


@app.get("/htmx", include_in_schema=False)
def htmx_no_trailing_slash():
    return RedirectResponse(url="/htmx/", status_code=307)


if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
