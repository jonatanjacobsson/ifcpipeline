"""Server-rendered workers table (matches SPA loadWorkers)."""

from __future__ import annotations


from fasthtml.components import A, Div, P, Span, Table, Tbody, Td, Th, Thead, Tr

from pipeline_ui.renderers.htmx_common import short_id, time_ago, worker_state_badge
from pipeline_ui.renderers.htmx_layout import render_htmx_shell_page
from routers import rq


def render_workers_fragment() -> str:
    """HTML fragment: Active Workers table."""
    try:
        overview = rq.overview()
    except Exception as e:
        return str(Div(P(f"Could not load workers: {str(e)}", cls="error")))

    workers = overview.get("workers") or []
    t = overview.get("totals") or {}

    stale = int(t.get("stale_workers") or 0)

    stat_cards = [
        Div(
            Div("Workers online", cls="stat-card-label"),
            Div(str(len(workers)), cls="stat-card-value accent"),
            cls="stat-card",
        ),
        Div(
            Div("Queued jobs", cls="stat-card-label"),
            Div(str(t.get("queued") or 0), cls="stat-card-value"),
            cls="stat-card",
        ),
        Div(
            Div("Running jobs", cls="stat-card-label"),
            Div(str(t.get("started") or 0), cls="stat-card-value"),
            cls="stat-card",
        ),
    ]
    if stale:
        # Never hide these silently: a growing count means workers are dying without
        # deregistering, and the leaked hashes stay in Redis forever.
        stat_cards.append(
            Div(
                Div("Stale registrations", cls="stat-card-label"),
                Div(str(stale), cls="stat-card-value error"),
                Div("no heartbeat — not shown below", cls="stat-card-sub"),
                cls="stat-card",
            )
        )

    stat_row = Div(*stat_cards, cls="stat-grid")

    if not workers:
        empty = Tr(
            Td(
                "No workers online",
                colspan=7,
                style="text-align:center;color:var(--text-tertiary);padding:40px",
            )
        )
        tbody = Tbody(empty)
    else:
        rows = []
        for w in workers:
            jid = w.get("current_job_id")
            rows.append(
                Tr(
                    Td(str(w.get("name") or ""), style="font-family:var(--font-mono);font-size:12px"),
                    Td(worker_state_badge(w.get("state"))),
                    Td(
                        Div(
                            *[
                                Span(str(q), cls="meta-tag")
                                for q in (w.get("queues") or [])
                            ],
                            cls="meta-tags",
                        )
                    ),
                    Td(
                        A(
                            short_id(jid),
                            href=f"/htmx/jobs/{jid}",
                            cls="link-job",
                        )
                        if jid
                        else Span("-")
                    ),
                    Td(str(w.get("successful_job_count", 0)), style="color:var(--success)"),
                    Td(
                        str(w.get("failed_job_count", 0)),
                        style=(
                            "color:var(--error)"
                            if (w.get("failed_job_count") or 0) > 0
                            else "color:var(--text-tertiary)"
                        ),
                    ),
                    Td(time_ago(w.get("birth_date"))),
                )
            )
        tbody = Tbody(*rows)

    panel = Div(
        Div(Span("Active Workers", cls="panel-title"), cls="panel-header"),
        Div(
            Div(
                Table(
                    Thead(
                        Tr(
                            Th("Name"),
                            Th("State"),
                            Th("Queues"),
                            Th("Current Job"),
                            Th("Successful"),
                            Th("Failed"),
                            Th("Uptime"),
                        )
                    ),
                    tbody,
                    cls="data-table",
                ),
                cls="table-scroll table-sticky-first htmx-data-table",
                style="max-height:600px",
            ),
            cls="panel-body no-pad",
        ),
        cls="panel",
    )
    return str(Div(stat_row, panel, cls="htmx-page-stack"))


def render_workers_page() -> str:
    return render_htmx_shell_page(
        document_title="IFC Pipeline — Workers",
        header_title="Workers",
        hx_fragment_path="/htmx/workers/content",
        json_href="/api/rq/overview",
        json_label="JSON: /api/rq/overview",
        active_nav="workers",
    )
