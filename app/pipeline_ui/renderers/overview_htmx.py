"""Dashboard — "is the pipeline healthy right now?"

Deliberately holds no job table. Jobs owns the live queue and History owns the archive;
if this page listed jobs too, all three would read as the same screen. This one answers
a different question: overall state, where the work is sitting, and what is broken.
"""

from __future__ import annotations

from fasthtml.components import A, Div, P, Span

from pipeline_ui.renderers.htmx_common import (
    ft_html,
    short_id,
    status_dot,
    time_ago,
    truncate,
)
from pipeline_ui.renderers.htmx_layout import render_htmx_shell_page
from routers import rq, system
from services import redis_service


def render_header_status_fragment() -> str:
    """Redis / PG / n8n dots in the header."""
    try:
        health = system.health()
    except Exception:
        return ""
    health = health or {}
    parts = [
        status_dot((health.get("redis") or {}).get("connected"), "Redis"),
        status_dot((health.get("postgres") or {}).get("connected"), "PG"),
        status_dot((health.get("n8n") or {}).get("connected"), "n8n"),
    ]
    return str(Div(*parts, cls="header-status"))


def _overall(queues: list[dict], workers: list[dict], totals: dict) -> tuple[str, str, str]:
    """(state, headline, detail) for the hero banner."""
    listened = {q for w in workers for q in (w.get("queues") or [])}
    blocked = [q for q in queues if (q.get("queued") or 0) and q["name"] not in listened]
    running = totals.get("started") or 0
    queued = totals.get("queued") or 0

    if blocked:
        names = ", ".join(q["name"] for q in blocked[:3])
        more = f" +{len(blocked) - 3} more" if len(blocked) > 3 else ""
        return (
            "blocked",
            f"{len(blocked)} queue{'s' if len(blocked) > 1 else ''} blocked",
            f"work is queued on {names}{more} with no worker listening",
        )
    if not workers:
        return "blocked", "No workers online", "nothing can be processed"
    if running:
        return (
            "running",
            f"{running} job{'s' if running != 1 else ''} running",
            f"{queued} queued across {len(listened)} live queue(s)",
        )
    if queued:
        return "waiting", f"{queued} job(s) queued", "workers are picking them up"
    return "idle", "Idle", f"{len(workers)} worker(s) online, nothing queued"


def _activity_bar(q: dict, has_worker: bool) -> Div:
    """One row of the queue-activity strip: depth as a proportional bar."""
    queued = q.get("queued") or 0
    started = q.get("started") or 0
    failed = q.get("failed") or 0

    if started:
        state = "running"
    elif queued and not has_worker:
        state = "blocked"
    elif queued:
        state = "waiting"
    elif not has_worker:
        state = "offline"
    else:
        state = "idle"

    return Div(
        Span(cls=f"qpulse qpulse--{state}"),
        A(q["name"], href=f"/htmx/jobs/?queue={q['name']}", cls="actbar-name"),
        Div(Div(cls=f"actbar-fill actbar-fill--{state}"), cls="actbar-track"),
        Div(
            Span(str(queued), cls="actbar-n", title="queued"),
            Span("·", cls="actbar-sep"),
            Span(str(started), cls="actbar-n actbar-n--run", title="running"),
            Span("·", cls="actbar-sep"),
            Span(
                str(failed),
                cls="actbar-n actbar-n--err" if failed else "actbar-n",
                title="failed",
            ),
            cls="actbar-nums",
        ),
        cls="actbar",
    )


def render_overview_fragment() -> str:
    try:
        overview = rq.overview()
        health = system.health() or {}
        failed = redis_service.get_jobs("all", "failed", 1, 6)
    except Exception as e:
        return ft_html(Div(P(f"Could not load dashboard: {e}", cls="error")))

    totals = overview.get("totals") or {}
    queues = overview.get("queues") or []
    workers = overview.get("workers") or []
    redis_info = overview.get("redis") or {}

    state, headline, detail = _overall(queues, workers, totals)
    listened = {q for w in workers for q in (w.get("queues") or [])}

    hero = Div(
        Div(
            Span(cls=f"hero-pulse hero-pulse--{state}"),
            Div(
                Div(headline, cls="hero-headline"),
                Div(detail, cls="hero-detail"),
                cls="hero-text",
            ),
            cls="hero-lead",
        ),
        Div(
            Div(
                Div(str(totals.get("workers") or 0), cls="hero-stat-value"),
                Div("workers", cls="hero-stat-label"),
                cls="hero-stat",
            ),
            Div(
                Div(str(len(queues)), cls="hero-stat-value"),
                Div("queues", cls="hero-stat-label"),
                cls="hero-stat",
            ),
            Div(
                Div(str(totals.get("jobs") or 0), cls="hero-stat-value"),
                Div("jobs tracked", cls="hero-stat-label"),
                cls="hero-stat",
            ),
            Div(
                Div(str(redis_info.get("used_memory_human", "?")), cls="hero-stat-value"),
                Div("redis memory", cls="hero-stat-label"),
                cls="hero-stat",
            ),
            cls="hero-stats",
        ),
        cls=f"hero hero--{state}",
    )

    # Busiest first: the strip should lead with whatever is moving.
    ordered = sorted(
        queues,
        key=lambda q: (-(q.get("started") or 0), -(q.get("queued") or 0), q["name"]),
    )
    activity = Div(
        Div(
            Span("Queue activity", cls="panel-title"),
            A("Workers →", href="/htmx/workers/", cls="btn btn-ghost btn-sm"),
            cls="panel-header",
        ),
        Div(
            *[_activity_bar(q, q["name"] in listened) for q in ordered],
            cls="panel-body actbar-list",
        ),
        cls="panel",
    )

    blocks = [hero, activity]

    failed_jobs = failed.get("jobs") or []
    if failed_jobs:
        rows = []
        for j in failed_jobs:
            jid = j.get("id") or ""
            rows.append(
                Div(
                    A(short_id(jid), href=f"/htmx/jobs/{jid}", cls="fail-id", title=jid),
                    Span(str(j.get("queue") or ""), cls="fail-queue"),
                    Span(truncate(str(j.get("name") or ""), 64), cls="fail-name"),
                    Span(time_ago(j.get("created_at")), cls="fail-when"),
                    cls="fail-row",
                )
            )
        blocks.append(
            Div(
                Div(
                    Span("Needs attention", cls="panel-title"),
                    Span(
                        f"{failed.get('total') or 0} failed job(s) in the queues",
                        cls="panel-hint",
                    ),
                    A(
                        "All failures →",
                        href="/htmx/jobs/?state=failed",
                        cls="btn btn-ghost btn-sm",
                    ),
                    cls="panel-header",
                ),
                Div(*rows, cls="panel-body fail-list"),
                cls="panel panel--attention",
            )
        )

    svc = []
    for key, label in (
        ("redis", "Redis"),
        ("postgres", "PostgreSQL"),
        ("n8n", "n8n"),
        ("network_share", "Network Share"),
    ):
        h = health.get(key) or {}
        ok = h.get("connected") if key != "network_share" else h.get("mounted")
        svc.append(
            Div(
                status_dot(ok, label),
                Span("ok" if ok else "down", cls=f"svc-state svc-state--{'ok' if ok else 'down'}"),
                cls="svc-chip",
            )
        )
    blocks.append(
        Div(
            Div(Span("Services", cls="panel-title"), cls="panel-header"),
            Div(Div(*svc, cls="svc-row"), cls="panel-body"),
            cls="panel",
        )
    )

    return ft_html(Div(*blocks, cls="htmx-page-stack"))


def render_overview_page() -> str:
    return render_htmx_shell_page(
        document_title="IFC Pipeline — Dashboard",
        header_title="Dashboard",
        hx_fragment_path="/htmx/overview",
        json_href="/api/rq/overview",
        json_label="JSON",
        active_nav="overview",
        hx_trigger="load, every 5s",
    )
