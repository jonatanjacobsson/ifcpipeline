"""HTMX HTML routes (`/htmx/*`)."""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from pipeline_ui.renderers import (
    database_htmx,
    detail_htmx,
    history_htmx,
    jobs_htmx,
    logs_htmx,
    n8n_htmx,
    network_share_htmx,
    overview_htmx,
    workers_htmx,
)

router = APIRouter(prefix="/htmx", tags=["htmx"])


# --- Overview ---
@router.get("/", response_class=HTMLResponse)
def htmx_dashboard():
    return HTMLResponse(overview_htmx.render_overview_page())


@router.get("/overview", response_class=HTMLResponse)
def htmx_overview_fragment():
    return HTMLResponse(overview_htmx.render_overview_fragment())


@router.get("/header-status", response_class=HTMLResponse)
def htmx_header_status():
    """Redis / PG / n8n dots in the header."""
    return HTMLResponse(overview_htmx.render_header_status_fragment())


# --- Workers ---
@router.get("/workers/", response_class=HTMLResponse)
def htmx_workers_dashboard():
    return HTMLResponse(workers_htmx.render_workers_page())


@router.get("/workers", response_class=HTMLResponse)
def htmx_workers_no_slash():
    return RedirectResponse(url="/htmx/workers/", status_code=307)


@router.get("/workers/content", response_class=HTMLResponse)
def htmx_workers_fragment():
    return HTMLResponse(workers_htmx.render_workers_fragment())


# --- Jobs (table route must precede the {job_id} routes) ---
@router.get("/jobs/table", response_class=HTMLResponse)
def htmx_jobs_table(
    queue: str = Query("all"),
    state: str = Query("all"),
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=500),
    search: str = Query(""),
    sort_col: str = Query("created_at"),
    sort_dir: str = Query("desc"),
):
    return HTMLResponse(
        jobs_htmx.render_jobs_table_fragment(
            queue, state, page, per_page, search, sort_col=sort_col, sort_dir=sort_dir
        )
    )


@router.get("/jobs/", response_class=HTMLResponse)
def htmx_jobs_page(
    queue: str = Query("all"),
    state: str = Query("live"),
    search: str = Query(""),
):
    return HTMLResponse(jobs_htmx.render_jobs_page(queue=queue, state=state, search=search))


@router.get("/jobs", response_class=HTMLResponse)
def htmx_jobs_no_slash():
    return RedirectResponse(url="/htmx/jobs/", status_code=307)


@router.get("/jobs/{job_id}/log-view", response_class=HTMLResponse)
def htmx_job_log_view(job_id: str, filename: str = Query(...)):
    return HTMLResponse(detail_htmx.render_log_view_fragment(job_id, filename))


@router.get("/jobs/{job_id}/content", response_class=HTMLResponse)
def htmx_job_detail_content(job_id: str):
    return HTMLResponse(detail_htmx.render_job_detail_fragment(job_id))


@router.get("/jobs/{job_id}/logs-panel", response_class=HTMLResponse)
def htmx_job_logs_panel(job_id: str):
    return HTMLResponse(detail_htmx.render_job_logs_panel_fragment(job_id))


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def htmx_job_detail_page(job_id: str):
    return HTMLResponse(detail_htmx.render_job_detail_page(job_id))


# --- History ---
@router.get("/history/table", response_class=HTMLResponse)
def htmx_history_table(
    queue: str = Query("all"),
    status: str = Query("all"),
    search: str = Query(""),
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=500),
    sort_col: str = Query("created_at"),
    sort_dir: str = Query("desc"),
):
    return HTMLResponse(
        history_htmx.render_history_table_fragment(
            queue, status, search, page, per_page, sort_col=sort_col, sort_dir=sort_dir
        )
    )


@router.get("/history/", response_class=HTMLResponse)
def htmx_history_page(
    queue: str = Query("all"),
    status: str = Query("all"),
    search: str = Query(""),
):
    return HTMLResponse(
        history_htmx.render_history_page(queue=queue, status=status, search=search)
    )


@router.get("/history", response_class=HTMLResponse)
def htmx_history_no_slash():
    return RedirectResponse(url="/htmx/history/", status_code=307)


@router.get("/history/{job_id}/content", response_class=HTMLResponse)
def htmx_history_detail_content(job_id: str):
    return HTMLResponse(detail_htmx.render_history_detail_fragment(job_id))


@router.get("/history/{job_id}", response_class=HTMLResponse)
def htmx_history_detail_page(job_id: str):
    return HTMLResponse(detail_htmx.render_history_detail_page(job_id))


# --- Network share ---
@router.get("/network-share/", response_class=HTMLResponse)
def htmx_network_share_page(
    path: str = Query(""),
    open_file: str | None = Query(None, alias="open"),
):
    return HTMLResponse(network_share_htmx.render_network_share_page(path, open_file))


@router.get("/network-share", response_class=HTMLResponse)
def htmx_network_share_no_slash():
    return RedirectResponse(url="/htmx/network-share/", status_code=307)


# --- Logs (live container/queue streaming) ---
@router.get("/logs/", response_class=HTMLResponse)
def htmx_logs_page(
    container: str = Query(""),
    queue: str = Query(""),
    tail: int = Query(200, ge=0, le=5000),
):
    return HTMLResponse(
        logs_htmx.render_logs_page(container=container or None, queue=queue or None, tail=tail)
    )


@router.get("/logs", response_class=HTMLResponse)
def htmx_logs_no_slash():
    return RedirectResponse(url="/htmx/logs/", status_code=307)


# --- n8n ---
@router.get("/n8n/content", response_class=HTMLResponse)
def htmx_n8n_content():
    return HTMLResponse(n8n_htmx.render_n8n_fragment())


@router.get("/n8n/", response_class=HTMLResponse)
def htmx_n8n_page():
    return HTMLResponse(n8n_htmx.render_n8n_page())


@router.get("/n8n", response_class=HTMLResponse)
def htmx_n8n_no_slash():
    return RedirectResponse(url="/htmx/n8n/", status_code=307)


# --- Database ---
@router.get("/database/content", response_class=HTMLResponse)
def htmx_database_content():
    return HTMLResponse(database_htmx.render_database_fragment())


@router.get("/database/", response_class=HTMLResponse)
def htmx_database_page():
    return HTMLResponse(database_htmx.render_database_page())


@router.get("/database", response_class=HTMLResponse)
def htmx_database_no_slash():
    return RedirectResponse(url="/htmx/database/", status_code=307)
