"""HTMX job queue table (parity with SPA: filters, dynamic meta columns, search, actions)."""

from __future__ import annotations

import re
from urllib.parse import urlencode

from fasthtml.components import A, Button, Div, Form, Input, Option, P, Select, Span, Table, Tbody, Td, Th, Thead, Tr

from pipeline_ui.renderers.htmx_common import badge, ft_html, short_id, time_ago, truncate
from pipeline_ui.renderers.htmx_layout import render_htmx_shell_page
from services import redis_service


def _opt(label: str, value: str, current: str) -> Option:
    kw = {"value": value}
    if value == current:
        kw["selected"] = ""
    return Option(label, **kw)


def _job_matches_search(j: dict, q: str) -> bool:
    if not q or not str(q).strip():
        return True
    needle = str(q).lower()
    meta = j.get("meta") or {}
    parts = [
        str(j.get("id") or ""),
        str(j.get("name") or ""),
        str(j.get("queue") or ""),
        str(j.get("status") or ""),
        *[str(v) for v in meta.values()],
    ]
    return needle in " ".join(parts).lower()


def _meta_td(val) -> Td:
    text = "" if val is None else str(val)
    if text and re.match(r"^https?://\S+$", text, re.I):
        return Td(
            A(truncate(text, 30), href=text, target="_blank", rel="noopener", title=text),
        )
    return Td(truncate(text, 30), title=text)


def _next_sort_dir(col: str, cur_col: str, cur_dir: str) -> str:
    if col != cur_col:
        return "desc"
    return "asc" if (cur_dir or "desc").lower() == "desc" else "desc"


def render_jobs_table_fragment(
    queue: str = "all",
    state: str = "live",
    page: int = 1,
    per_page: int = 100,
    search: str = "",
    sort_col: str = "created_at",
    sort_dir: str = "desc",
) -> str:
    try:
        data = redis_service.get_jobs(
            queue, state, page, per_page, sort_col=sort_col, sort_dir=sort_dir
        )
    except Exception as e:
        return ft_html(Div(P(f"Could not load jobs: {str(e)}", cls="error")))

    raw_jobs = data.get("jobs") or []
    jobs = [j for j in raw_jobs if _job_matches_search(j, search)]
    total = data.get("total") or 0
    pages = max(1, (total + per_page - 1) // per_page) if total else 1
    queues = data.get("queues") or []

    meta_keys: set[str] = set()
    for j in jobs:
        for k in (j.get("meta") or {}):
            meta_keys.add(k)
    meta_keys_sorted = sorted(meta_keys)

    q_opts = [_opt("All queues", "all", queue)]
    for q in queues:
        qs = str(q)
        q_opts.append(_opt(qs, qs, queue))

    state_opts = [
        _opt("In flight", "live", state),
        _opt("All states", "all", state),
        _opt("queued", "queued", state),
        _opt("started", "started", state),
        _opt("finished", "finished", state),
        _opt("failed", "failed", state),
        _opt("scheduled", "scheduled", state),
        _opt("deferred", "deferred", state),
    ]

    def q_params(**kwargs) -> str:
        p = {
            "queue": queue,
            "state": state,
            "page": page,
            "per_page": per_page,
            "search": search,
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
                hx_get=f"/htmx/jobs/table?{q_params(sort_col=col, sort_dir=nd)}",
                hx_target="#jobs-htmx-root",
                hx_indicator="#jobs-htmx-indicator",
                hx_swap="outerHTML",
            )
        )

    thead_cells = [
        _sort_th("ID", "id"),
        _sort_th("Status", "status"),
        _sort_th("Queue", "queue"),
        _sort_th("Job", "name"),
    ]
    for mk in meta_keys_sorted:
        mcol = f"meta:{mk}"
        nd = _next_sort_dir(mcol, sort_col, sort_dir)
        mark = ""
        if mcol == sort_col:
            mark = " ▲" if (sort_dir or "").lower() == "asc" else " ▼"
        thead_cells.append(
            Th(
                A(
                    f"{mk}{mark}",
                    cls="table-sort-link",
                    hx_get=f"/htmx/jobs/table?{q_params(sort_col=mcol, sort_dir=nd)}",
                    hx_target="#jobs-htmx-root",
                    hx_indicator="#jobs-htmx-indicator",
                    hx_swap="outerHTML",
                )
            )
        )
    thead_cells.append(_sort_th("Created", "created_at"))
    thead_cells.append(Th("Actions", cls="no-sort"))

    rows = []
    for j in jobs:
        jid = j.get("id") or ""
        st = j.get("status") or ""
        meta = j.get("meta") or {}
        meta_cells = [_meta_td(meta.get(k)) for k in meta_keys_sorted]

        act = [
            Button(
                "Delete",
                cls="btn btn-danger btn-sm",
                hx_delete=f"/api/rq/jobs/{jid}",
                hx_confirm="Delete this job?",
                hx_swap="none",
                **{"hx-on::after-request": "if(event.detail.successful) location.href='/htmx/jobs/'"},
            )
        ]
        if st == "failed":
            act.append(
                Button(
                    "Requeue",
                    cls="btn btn-ghost btn-sm",
                    hx_post=f"/api/rq/jobs/{jid}/requeue",
                    hx_confirm="Requeue this job?",
                    hx_swap="none",
                    **{"hx-on::after-request": "if(event.detail.successful) location.reload()"},
                )
            )

        rows.append(
            Tr(
                Td(
                    A(
                        short_id(jid),
                        href=f"/htmx/jobs/{jid}",
                        cls="link-job",
                        title=jid,
                    )
                ),
                Td(badge(st)),
                Td(Span(str(j.get("queue") or ""), cls="meta-tag")),
                Td(
                    truncate(str(j.get("name") or ""), 60),
                    title=str(j.get("name") or ""),
                ),
                *meta_cells,
                Td(time_ago(j.get("created_at"))),
                Td(Div(*act, style="display:flex;gap:6px;flex-wrap:nowrap")),
            )
        )

    can_next = page < pages and len(jobs) >= per_page

    pag_parts = []
    if page > 1:
        pag_parts.append(
            A(
                "« Prev",
                cls="btn btn-ghost btn-sm",
                hx_get=f"/htmx/jobs/table?{q_params(page=page - 1)}",
                hx_target="#jobs-htmx-root",
                hx_indicator="#jobs-htmx-indicator",
                hx_swap="outerHTML",
            )
        )
    pag_parts.append(
        Span(
            f"Page {page} / {pages} · {total} jobs total"
            + (" · search filters this page" if (search and search.strip()) else ""),
            cls="meta-tag",
        )
    )
    if can_next:
        pag_parts.append(
            A(
                "Next »",
                cls="btn btn-ghost btn-sm",
                hx_get=f"/htmx/jobs/table?{q_params(page=page + 1)}",
                hx_target="#jobs-htmx-root",
                hx_indicator="#jobs-htmx-indicator",
                hx_swap="outerHTML",
            )
        )

    pag = Div(*pag_parts, cls="pagination")

    ncol = len(thead_cells)
    table = Table(
        Thead(Tr(*thead_cells)),
        Tbody(*rows) if rows else Tbody(Tr(Td("No jobs", colspan=str(ncol)))),
        cls="data-table",
    )

    form = Form(
        Span(**{"aria-hidden": "true"}, id="jobs-htmx-indicator", cls="htmx-inline-indicator htmx-indicator"),
        Span("Queue", cls="filter-label"),
        Select(
            *q_opts,
            name="queue",
            cls="filter-select",
            hx_get="/htmx/jobs/table",
            hx_trigger="change",
            hx_target="#jobs-htmx-root",
            hx_indicator="#jobs-htmx-indicator",
            hx_swap="outerHTML",
            hx_include="closest form",
        ),
        Span("State", cls="filter-label"),
        Select(
            *state_opts,
            name="state",
            cls="filter-select",
            hx_get="/htmx/jobs/table",
            hx_trigger="change",
            hx_target="#jobs-htmx-root",
            hx_indicator="#jobs-htmx-indicator",
            hx_swap="outerHTML",
            hx_include="closest form",
        ),
        Input(
            name="search",
            type="text",
            value=search,
            placeholder="Search by ID, name, model, meta…",
            cls="filter-input htmx-table-search",
            hx_get="/htmx/jobs/table",
            hx_trigger="input changed delay:400ms",
            hx_target="#jobs-htmx-root",
            hx_indicator="#jobs-htmx-indicator",
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
        form,
        Div(
            Div(
                Div(
                    Span("Live queue", cls="panel-title"),
                    Span(
                        "What is in Redis right now. Completed jobs age out of here — "
                        "look in History for those.",
                        cls="panel-header-subtitle",
                    ),
                    style="display:flex;flex-wrap:wrap;align-items:baseline;gap:8px",
                ),
                A(
                    "Refresh",
                    cls="btn btn-ghost btn-sm",
                    hx_get=f"/htmx/jobs/table?{q_params()}",
                    hx_target="#jobs-htmx-root",
                    hx_indicator="#jobs-htmx-indicator",
                    hx_swap="outerHTML",
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
        id="jobs-htmx-root",
        **{"data-page-loader": "full"},
    )

    return ft_html(inner)


def render_jobs_page(queue: str = "all", state: str = "live", search: str = "") -> str:
    """Deep-linkable: the dashboard and queue cards link here pre-filtered."""
    qs = urlencode({"queue": queue or "all", "state": state or "live", "search": search or ""})
    return render_htmx_shell_page(
        document_title="IFC Pipeline — Jobs",
        header_title="Jobs",
        hx_fragment_path=f"/htmx/jobs/table?{qs}",
        json_href="/api/rq/jobs",
        json_label="JSON",
        active_nav="jobs",
        # No polling: the fragment carries the filter and search inputs, and a periodic
        # swap would wipe whatever the user is typing. Dashboard is the live view;
        # this one has an explicit Refresh.
        hx_trigger="load",
    )
