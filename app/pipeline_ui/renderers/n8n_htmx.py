"""Server-rendered n8n workflows and executions (SPA parity)."""

from __future__ import annotations


from fasthtml.components import Div, P, Span, Strong, Table, Tbody, Td, Th, Thead, Tr

from pipeline_ui.renderers.htmx_common import badge, time_ago, truncate
from pipeline_ui.renderers.htmx_layout import render_htmx_shell_page
from services import cache, n8n_service


def render_n8n_fragment() -> str:
    try:
        status = n8n_service.check_health()
        wf = cache.get_or_set("n8n_workflows", 120, n8n_service.get_workflows)
        ex = cache.get_or_set("n8n_executions_30_None", 60, n8n_service.get_executions, 30, None)
    except Exception as e:
        return str(Div(P(f"Could not load n8n: {str(e)}", cls="error")))

    workflows = wf if isinstance(wf, list) else []
    active_ct = sum(1 for w in workflows if isinstance(w, dict) and w.get("active"))

    eng = "Online" if status.get("connected") else "Offline"
    status_row = Div(
        Div(
            Div("Engine", cls="stat-card-label"),
            Div(eng, cls="stat-card-value success" if status.get("connected") else "stat-card-value error"),
            cls="stat-card",
        ),
        Div(
            Div("Workflows", cls="stat-card-label"),
            Div(str(len(workflows)), cls="stat-card-value"),
            Div(f"{active_ct} active", cls="stat-card-sub"),
            cls="stat-card",
        ),
        cls="stat-grid",
    )

    wf_rows = []
    for w in workflows[:200]:
        if not isinstance(w, dict):
            continue
        tags = w.get("tags") or []
        tag_spans = [Span(str(t), cls="meta-tag") for t in tags] if tags else [Span("-")]
        wf_rows.append(
            Tr(
                Td(Strong(str(w.get("name") or w.get("id") or "?"))),
                Td(badge("active" if w.get("active") else "inactive")),
                Td(Div(*tag_spans, cls="meta-tags")),
            )
        )

    ex_rows = []
    execs = ex if isinstance(ex, list) else []
    for e in execs[:50]:
        if not isinstance(e, dict):
            continue
        ex_rows.append(
            Tr(
                Td(str(e.get("id") or "-"), style="font-family:var(--font-mono);font-size:12px"),
                Td(truncate(str(e.get("workflowName") or e.get("workflowId") or "-"), 25)),
                Td(badge(str(e.get("status") or "unknown"))),
                Td(time_ago(e.get("startedAt") or e.get("stoppedAt"))),
                Td(str(e.get("mode") or "-")),
            )
        )

    wf_table = Div(
        Div(Span("Workflows", cls="panel-title"), cls="panel-header"),
        Div(
            Div(
                Table(
                    Thead(Tr(Th("Name"), Th("Status"), Th("Tags"))),
                    Tbody(*wf_rows) if wf_rows else Tbody(Tr(Td("No workflows found", colspan=3))),
                    cls="data-table",
                ),
                cls="table-scroll table-sticky-first htmx-data-table",
                style="max-height:500px",
            ),
            cls="panel-body no-pad",
        ),
        cls="panel",
    )
    ex_table = Div(
        Div(Span("Recent Executions", cls="panel-title"), cls="panel-header"),
        Div(
            Div(
                Table(
                    Thead(Tr(Th("ID"), Th("Workflow"), Th("Status"), Th("Started"), Th("Mode"))),
                    Tbody(*ex_rows) if ex_rows else Tbody(Tr(Td("No executions found", colspan=5))),
                    cls="data-table",
                ),
                cls="table-scroll table-sticky-first htmx-data-table",
                style="max-height:500px",
            ),
            cls="panel-body no-pad",
        ),
        cls="panel",
    )
    return str(
        Div(
            status_row,
            Div(
                wf_table,
                ex_table,
                style="display:grid;grid-template-columns:1fr 1fr;gap:16px",
            ),
            cls="htmx-page-stack",
        )
    )


def render_n8n_page() -> str:
    return render_htmx_shell_page(
        document_title="IFC Pipeline — n8n",
        header_title="n8n",
        hx_fragment_path="/htmx/n8n/content",
        json_href="/api/n8n/workflows",
        json_label="JSON",
        active_nav="n8n",
        hx_trigger="load, every 30s",
    )
