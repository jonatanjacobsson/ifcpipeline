"""HTMX job history table (parity with SPA: stats cards, columns)."""

from __future__ import annotations

from urllib.parse import urlencode

from fasthtml.components import A, Div, Form, Input, Option, P, Select, Span, Table, Tbody, Td, Th, Thead, Tr

from pipeline_ui.renderers.htmx_common import badge, ft_html, short_id, time_ago, truncate
from pipeline_ui.renderers.htmx_layout import render_htmx_shell_page
from services import job_history_service


def _opt(label: str, value: str, current: str) -> Option:
    kw: dict = {"value": value}
    if value == current:
        kw["selected"] = ""
    return Option(label, **kw)


def _next_sort_dir(col: str, cur_col: str, cur_dir: str) -> str:
    if col != cur_col:
        return "desc"
    return "asc" if (cur_dir or "desc").lower() == "desc" else "desc"


def render_history_table_fragment(
    queue: str = "all",
    status: str = "all",
    search: str = "",
    page: int = 1,
    per_page: int = 100,
    sort_col: str = "created_at",
    sort_dir: str = "desc",
) -> str:
    try:
        data = job_history_service.get_jobs_from_history(
            queue,
            status,
            search,
            page,
            per_page,
            sort_col=sort_col,
            sort_dir=sort_dir,
        )
    except Exception as e:
        return ft_html(Div(P(f"Could not load history: {str(e)}", cls="error")))

    if data.get("error"):
        return ft_html(Div(P(f"History error: {str(data['error'])}", cls="error")))

    jobs = data.get("jobs") or []
    total = data.get("total") or 0
    total_all = data.get("total_all")
    if total_all is None:
        total_all = total
    pages = max(1, (total + per_page - 1) // per_page) if total else 1
    queues = data.get("queues") or []
    sc = data.get("status_counts") or {}

    stats = Div(
        Div(
            Div("Total Recorded", cls="stat-card-label"),
            Div(str(total_all), cls="stat-card-value"),
            Div(
                "all time in Postgres"
                + (f" ({total} matching filter)" if total != total_all else ""),
                cls="stat-card-sub",
            ),
            cls="stat-card",
        ),
        Div(
            Div("Finished", cls="stat-card-label"),
            Div(str(sc.get("finished") or 0), cls="stat-card-value", style="color:var(--success)"),
            cls="stat-card",
        ),
        Div(
            Div("Failed", cls="stat-card-label"),
            Div(
                str(sc.get("failed") or 0),
                cls="stat-card-value error" if (sc.get("failed") or 0) > 0 else "stat-card-value",
            ),
            cls="stat-card",
        ),
        Div(
            Div("Running", cls="stat-card-label"),
            Div(str(sc.get("started") or 0), cls="stat-card-value"),
            cls="stat-card",
        ),
        cls="stat-grid",
        style="margin-bottom:16px",
    )

    q_opts = [_opt("All queues", "all", queue)]
    for q in queues:
        qs = str(q)
        q_opts.append(_opt(qs, qs, queue))

    status_opts = [
        _opt("All states", "all", status),
        _opt("queued", "queued", status),
        _opt("started", "started", status),
        _opt("finished", "finished", status),
        _opt("failed", "failed", status),
        _opt("scheduled", "scheduled", status),
        _opt("deferred", "deferred", status),
    ]

    def q_params(**kwargs) -> str:
        p = {
            "queue": queue,
            "status": status,
            "search": search,
            "page": page,
            "per_page": per_page,
            "sort_col": sort_col,
            "sort_dir": sort_dir,
        }
        p.update(kwargs)
        return urlencode(p)

    def _sort_th(label: str, col: str) -> Th:
        nd = _next_sort_dir(col, sort_col, sort_dir)
        mark = ""
        if col == sort_col:
            mark = " ▲" if (sort_dir or "").lower() == "asc" else " ▼"
        return Th(
            A(
                f"{label}{mark}",
                cls="table-sort-link",
                hx_get=f"/htmx/history/table?{q_params(sort_col=col, sort_dir=nd)}",
                hx_target="#history-htmx-root",
                hx_indicator="#history-htmx-indicator",
                hx_swap="outerHTML",
            )
        )

    thead_row = Tr(
        _sort_th("ID", "job_id"),
        _sort_th("Status", "status"),
        _sort_th("Queue", "queue"),
        _sort_th("Function", "func_name"),
        _sort_th("Job", "name"),
        _sort_th("Created", "created_at"),
        _sort_th("Ended", "ended_at"),
    )

    rows = []
    for j in jobs:
        jid = j.get("job_id") or j.get("id") or ""
        name = str(j.get("name") or "")
        fn = j.get("func_name") or ""
        func_short = fn.split(".")[-1] if fn else ""

        rows.append(
            Tr(
                Td(
                    A(
                        short_id(jid),
                        href=f"/htmx/history/{jid}",
                        cls="link-job",
                        title=jid,
                    )
                ),
                Td(badge(j.get("status") or "")),
                Td(str(j.get("queue") or "")),
                Td(func_short, style="font-family:var(--font-mono);font-size:11px"),
                Td(truncate(name, 60), title=name),
                Td(time_ago(j.get("created_at"))),
                Td(time_ago(j.get("ended_at")) if j.get("ended_at") else "-"),
            )
        )

    pag_parts = []
    if page > 1:
        pag_parts.append(
            A(
                "« Prev",
                cls="btn btn-ghost btn-sm",
                hx_get=f"/htmx/history/table?{q_params(page=page - 1)}",
                hx_target="#history-htmx-root",
                hx_indicator="#history-htmx-indicator",
                hx_swap="outerHTML",
            )
        )
    pag_parts.append(Span(f"Page {page} / {pages} · {total} jobs total", cls="meta-tag"))
    if page < pages and len(jobs) >= per_page:
        pag_parts.append(
            A(
                "Next »",
                cls="btn btn-ghost btn-sm",
                hx_get=f"/htmx/history/table?{q_params(page=page + 1)}",
                hx_target="#history-htmx-root",
                hx_indicator="#history-htmx-indicator",
                hx_swap="outerHTML",
            )
        )
    pag = Div(*pag_parts, cls="pagination")

    ncol = len(thead_row.children)
    table = Table(
        Thead(thead_row),
        Tbody(*rows) if rows else Tbody(Tr(Td("No jobs in history", colspan=str(ncol)))),
        cls="data-table",
    )

    form = Form(
        Span(**{"aria-hidden": "true"}, id="history-htmx-indicator", cls="htmx-inline-indicator htmx-indicator"),
        Span("Queue", cls="filter-label"),
        Select(
            *q_opts,
            name="queue",
            cls="filter-select",
            hx_get="/htmx/history/table",
            hx_trigger="change",
            hx_target="#history-htmx-root",
            hx_indicator="#history-htmx-indicator",
            hx_swap="outerHTML",
            hx_include="closest form",
        ),
        Span("State", cls="filter-label"),
        Select(
            *status_opts,
            name="status",
            cls="filter-select",
            hx_get="/htmx/history/table",
            hx_trigger="change",
            hx_target="#history-htmx-root",
            hx_indicator="#history-htmx-indicator",
            hx_swap="outerHTML",
            hx_include="closest form",
        ),
        Input(
            name="search",
            type="text",
            value=search,
            placeholder="Search by ID, name, model…",
            cls="filter-input htmx-table-search",
            hx_get="/htmx/history/table",
            hx_trigger="input changed delay:400ms",
            hx_target="#history-htmx-root",
            hx_indicator="#history-htmx-indicator",
            hx_swap="outerHTML",
            hx_include="closest form",
            hx_vals='{"page": 1}',
        ),
        Input(name="page", type="hidden", value=str(page)),
        Input(name="per_page", type="hidden", value=str(per_page)),
        Input(name="sort_col", type="hidden", value=sort_col),
        Input(name="sort_dir", type="hidden", value=sort_dir),
        cls="filters filter-bar",
        style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:0",
    )

    inner = Div(
        stats,
        form,
        Div(
            Div(
                Div(
                    Span("Job history", cls="panel-title"),
                    Span(
                        "Recorded jobs from Postgres (sortable columns).",
                        cls="panel-header-subtitle",
                    ),
                    style="display:flex;flex-wrap:wrap;align-items:baseline;gap:4px",
                ),
                cls="panel-header",
            ),
            Div(
                Div(table, cls="table-scroll table-sticky-first htmx-data-table", style="max-height:600px"),
                pag,
                cls="panel-body no-pad",
            ),
            cls="panel",
        ),
        cls="htmx-page-stack",
        id="history-htmx-root",
        **{"data-page-loader": "full"},
    )

    return ft_html(inner)


def render_history_page() -> str:
    return render_htmx_shell_page(
        document_title="IFC Pipeline — History",
        header_title="History",
        hx_fragment_path="/htmx/history/table",
        json_href="/api/rq/history",
        json_label="JSON: /api/rq/history",
        active_nav="history",
        hx_trigger="load",
    )
