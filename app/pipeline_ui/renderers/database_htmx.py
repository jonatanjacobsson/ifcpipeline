"""PostgreSQL stats (SPA parity: counts + recent rows from /api/system/db/stats)."""

from __future__ import annotations

from html import escape

from fasthtml.components import Div, P, Span, Table, Tbody, Td, Th, Thead, Tr

from pipeline_ui.renderers.htmx_common import time_ago
from pipeline_ui.renderers.htmx_layout import render_htmx_shell_page
from services import cache, db_service


def _basename(path: str | None) -> str:
    if not path:
        return ""
    return str(path).replace("\\", "/").rsplit("/", 1)[-1]


def render_database_fragment() -> str:
    try:
        stats = cache.get_or_set("db_stats", 60, db_service.get_stats)
    except Exception as e:
        return str(Div(P(f"Could not load database info: {escape(str(e))}", cls="error")))

    if not stats.get("connected"):
        return str(
            Div(
                Div(
                    Span("Status", cls="stat-card-label"),
                    Span("Offline", cls="stat-card-value error"),
                    cls="stat-card",
                )
            )
        )

    c = stats.get("counts") or {}
    count_cards = Div(
        Div(
            Div("Test Results", cls="stat-card-label"),
            Div(str(c.get("tester_results", 0)), cls="stat-card-value"),
            cls="stat-card",
        ),
        Div(
            Div("Clash Results", cls="stat-card-label"),
            Div(str(c.get("clash_results", 0)), cls="stat-card-value"),
            cls="stat-card",
        ),
        Div(
            Div("Diff Results", cls="stat-card-label"),
            Div(str(c.get("diff_results", 0)), cls="stat-card-value"),
            cls="stat-card",
        ),
        Div(
            Div("Conversions", cls="stat-card-label"),
            Div(str(c.get("conversion_results", 0)), cls="stat-card-value"),
            cls="stat-card",
        ),
        cls="stat-grid",
        style="margin-bottom:16px",
    )

    recent_tests = stats.get("recent_tests") or []
    test_rows = []
    for t in recent_tests:
        if not isinstance(t, dict):
            continue
        test_rows.append(
            Tr(
                Td(escape(_basename(t.get("ifc_filename"))), title=escape(str(t.get("ifc_filename") or ""))),
                Td(escape(_basename(t.get("ids_filename"))), title=escape(str(t.get("ids_filename") or ""))),
                Td(str(t.get("pass_count", 0)), style="color:var(--success)"),
                Td(
                    str(t.get("fail_count", 0)),
                    style="color:var(--error)" if (t.get("fail_count") or 0) > 0 else "color:var(--text-tertiary)",
                ),
                Td(time_ago(t.get("created_at"))),
            )
        )

    recent_clashes = stats.get("recent_clashes") or []
    clash_rows = []
    for row in recent_clashes:
        if not isinstance(row, dict):
            continue
        label = row.get("label") or row.get("clash_set_name") or "-"
        cc = row.get("clash_count") or 0
        clash_rows.append(
            Tr(
                Td(escape(str(label))),
                Td(
                    str(cc),
                    style="color:var(--warning)" if cc > 0 else "color:var(--success)",
                ),
                Td(time_ago(row.get("created_at"))),
            )
        )

    recent_diffs = stats.get("recent_diffs") or []
    diff_rows = []
    for row in recent_diffs:
        if not isinstance(row, dict):
            continue
        diff_rows.append(
            Tr(
                Td(escape(_basename(row.get("old_file"))), title=escape(str(row.get("old_file") or ""))),
                Td(escape(_basename(row.get("new_file"))), title=escape(str(row.get("new_file") or ""))),
                Td(str(row.get("diff_count", 0))),
                Td(time_ago(row.get("created_at"))),
            )
        )

    tests_table = Div(
        Div(Span("Recent Test Results", cls="panel-title"), cls="panel-header"),
        Div(
            Div(
                Table(
                    Thead(Tr(Th("IFC File"), Th("IDS File"), Th("Pass"), Th("Fail"), Th("Date"))),
                    Tbody(*test_rows) if test_rows else Tbody(Tr(Td("No test results", colspan=5))),
                    cls="data-table",
                ),
                cls="table-scroll table-sticky-first htmx-data-table",
                style="max-height:400px",
            ),
            cls="panel-body no-pad",
        ),
        cls="panel",
    )
    clashes_table = Div(
        Div(Span("Recent Clash Results", cls="panel-title"), cls="panel-header"),
        Div(
            Div(
                Table(
                    Thead(Tr(Th("Clash Set"), Th("Count"), Th("Date"))),
                    Tbody(*clash_rows) if clash_rows else Tbody(Tr(Td("No clash results", colspan=3))),
                    cls="data-table",
                ),
                cls="table-scroll table-sticky-first htmx-data-table",
                style="max-height:400px",
            ),
            cls="panel-body no-pad",
        ),
        cls="panel",
    )
    diffs_table = Div(
        Div(Span("Recent diffs", cls="panel-title"), cls="panel-header"),
        Div(
            Div(
                Table(
                    Thead(Tr(Th("Old"), Th("New"), Th("Diffs"), Th("When"))),
                    Tbody(*diff_rows) if diff_rows else Tbody(Tr(Td("No diff results", colspan=4))),
                    cls="data-table",
                ),
                cls="table-scroll table-sticky-first htmx-data-table",
                style="max-height:400px",
            ),
            cls="panel-body no-pad",
        ),
        cls="panel",
    )

    return str(
        Div(
            count_cards,
            Div(
                tests_table,
                clashes_table,
                style="display:grid;grid-template-columns:1fr 1fr;gap:16px",
            ),
            diffs_table,
            cls="htmx-page-stack",
        )
    )


def render_database_page() -> str:
    return render_htmx_shell_page(
        document_title="IFC Pipeline — Database",
        header_title="Database",
        hx_fragment_path="/htmx/database/content",
        json_href="/api/system/db/stats",
        json_label="JSON",
        active_nav="database",
        hx_trigger="load, every 45s",
    )
