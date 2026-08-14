"""Server-rendered dashboard overview (FastHTML components + HTMX target)."""

from __future__ import annotations

from html import escape

from fasthtml.components import (
    A,
    Div,
    NotStr,
    P,
    Span,
    Table,
    Tbody,
    Td,
    Th,
    Thead,
    Tr,
)

from pipeline_ui.renderers.htmx_common import badge, service_card, short_id, status_dot, time_ago, truncate
from pipeline_ui.renderers.htmx_layout import render_htmx_shell_page
from routers import rq, system
from services import redis_service

# Matches [dashboard/static/index.html](dashboard/static/index.html) Recent Jobs panel icon
_RECENT_JOBS_SVG = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>'
)


def render_header_status_fragment() -> str:
    """SPA `updateHeaderStatus`: Redis, PG, n8n dots in header."""
    try:
        health = system.health()
    except Exception:
        return ""
    parts = [
        status_dot(health.get("redis", {}).get("connected"), "Redis"),
        status_dot(health.get("postgres", {}).get("connected"), "PG"),
        status_dot(health.get("n8n", {}).get("connected"), "n8n"),
    ]
    return str(Div(*parts, cls="header-status"))


def render_overview_fragment() -> str:
    """HTML fragment for HTMX swap (stats + recent jobs + service health)."""
    try:
        overview = rq.overview()
        health = system.health()
        jobs_payload = redis_service.get_jobs("all", "all", 1, 15)
    except Exception as e:
        return str(Div(P(f"Could not load overview: {escape(str(e))}", cls="error")))

    t = overview["totals"]
    r = overview["redis"]
    jobs = jobs_payload.get("jobs") or []

    failed_val_cls = "stat-card-value error" if t.get("failed", 0) > 0 else "stat-card-value"

    stat_grid = Div(
        Div(
            Div("Total Jobs", cls="stat-card-label"),
            Div(str(t.get("jobs", 0)), cls="stat-card-value"),
            Div(f'{t.get("queued", 0)} queued, {t.get("started", 0)} running', cls="stat-card-sub"),
            cls="stat-card",
        ),
        Div(
            Div("Failed", cls="stat-card-label"),
            Div(str(t.get("failed", 0)), cls=failed_val_cls),
            Div("jobs with errors", cls="stat-card-sub"),
            cls="stat-card",
        ),
        Div(
            Div("Workers", cls="stat-card-label"),
            Div(str(t.get("workers", 0)), cls="stat-card-value accent"),
            Div(f'{t.get("queues", 0)} queues active', cls="stat-card-sub"),
            cls="stat-card",
        ),
        Div(
            Div("Redis Memory", cls="stat-card-label"),
            Div(str(r.get("used_memory_human", "?")), cls="stat-card-value"),
            Div(f'v{r.get("redis_version", "?")}', cls="stat-card-sub"),
            cls="stat-card",
        ),
        cls="stat-grid",
    )

    tbody_rows = []
    for j in jobs:
        jid = j.get("id") or ""
        name = str(j.get("name") or "")
        tbody_rows.append(
            Tr(
                Td(
                    A(
                        short_id(jid),
                        href=f"/htmx/jobs/{escape(jid)}",
                        cls="link-job",
                    )
                ),
                Td(badge(j.get("status") or "")),
                Td(Span(escape(str(j.get("queue") or "")), cls="meta-tag")),
                Td(escape(truncate(name, 55)), title=escape(name)),
                Td(time_ago(j.get("created_at"))),
            )
        )

    jobs_panel = Div(
        Div(
            Span(
                NotStr(_RECENT_JOBS_SVG),
                " Recent Jobs",
                cls="panel-title",
            ),
            A("View All", href="/htmx/jobs/", cls="btn btn-ghost btn-sm"),
            cls="panel-header",
        ),
        Div(
            Div(
                Table(
                    Thead(
                        Tr(
                            Th("ID"),
                            Th("Status"),
                            Th("Queue"),
                            Th("Job"),
                            Th("Created"),
                        )
                    ),
                    Tbody(*tbody_rows) if tbody_rows else Tbody(Tr(Td("No jobs", colspan=5))),
                    cls="data-table",
                ),
                cls="table-scroll table-sticky-first htmx-data-table",
            ),
            cls="panel-body no-pad",
        ),
        cls="panel",
    )

    ns = health.get("network_share") or {}
    n8n_h = health.get("n8n") or {}

    service_grid = Div(
        service_card(
            status_dot(health.get("redis", {}).get("connected"), "Redis"),
            [("Status", "Connected" if health.get("redis", {}).get("connected") else "Offline")],
        ),
        service_card(
            status_dot(health.get("postgres", {}).get("connected"), "PostgreSQL"),
            [("Status", "Connected" if health.get("postgres", {}).get("connected") else "Offline")],
        ),
        service_card(
            status_dot(n8n_h.get("connected"), "n8n"),
            [("Status", "Online" if n8n_h.get("connected") else "Offline")],
        ),
        service_card(
            status_dot(ns.get("mounted"), "Network Share"),
            [("Mount", "Mounted" if ns.get("mounted") else "Not Mounted")],
        ),
        cls="service-grid",
    )

    service_panel = Div(
        Div(Span("Service health", cls="panel-title"), cls="panel-header"),
        Div(service_grid, cls="panel-body no-pad"),
        cls="panel",
    )

    return str(Div(stat_grid, jobs_panel, service_panel, cls="htmx-page-stack"))


def render_overview_page() -> str:
    """Full HTML document: HTMX shell + auto-refreshing overview."""
    return render_htmx_shell_page(
        document_title="IFC Pipeline — HTMX overview",
        header_title="Dashboard",
        hx_fragment_path="/htmx/overview",
        json_href="/api/rq/overview",
        json_label="JSON: /api/rq/overview",
        active_nav="overview",
    )
