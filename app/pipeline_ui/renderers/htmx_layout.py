"""Shared full-page shell for /htmx/* previews (sidebar, header, HTMX polling main)."""

from __future__ import annotations

from fastcore.xml import to_xml
from fasthtml.components import A, Aside, Body, Div, Head, Header, Html, Link, Main, NotStr, Script, Span, Title

from pipeline_ui.renderers.nav import render_sidebar_nav


def _main_area_skeleton() -> Div:
    """Placeholder inside ``#htmx-page-inner`` until the first fragment swap (``load`` trigger)."""
    return Div(
        Div(cls="htmx-main-skeleton__line htmx-main-skeleton__line--lg"),
        Div(
            Div(cls="htmx-main-skeleton__card"),
            Div(cls="htmx-main-skeleton__card"),
            Div(cls="htmx-main-skeleton__card"),
            cls="htmx-main-skeleton__grid",
        ),
        Div(cls="htmx-main-skeleton__line"),
        Div(cls="htmx-main-skeleton__line htmx-main-skeleton__line--md"),
        cls="htmx-main-skeleton",
        role="status",
        **{"aria-label": "Loading content"},
    )


def render_htmx_shell_page(
    *,
    document_title: str,
    header_title: str,
    hx_fragment_path: str | None = None,
    static_main_inner: str | None = None,
    main_root_id: str = "htmx-page-inner",
    json_href: str,
    json_label: str = "JSON",
    active_nav: str = "overview",
    footer_note: str = "IFC Pipeline · HTMX dashboard",
    hx_trigger: str = "load, every 6s",
    extra_head: tuple | None = None,
    extra_body: str | None = None,
) -> str:
    """Full HTML document matching dashboard `.app` grid (sidebar | header + main).

    Pass either ``hx_fragment_path`` (HTMX-driven main) or ``static_main_inner`` (fixed HTML,
    e.g. Network Share with IFC viewer) — not both.
    """
    if (hx_fragment_path is None) == (static_main_inner is None):
        raise ValueError("Provide exactly one of hx_fragment_path or static_main_inner")

    nav = render_sidebar_nav(active_nav)

    if static_main_inner is not None:
        main_block = Main(
            Div(
                NotStr(static_main_inner),
                id=main_root_id,
                cls="view active",
            ),
            cls="main",
            id="main-content",
        )
    else:
        main_block = Main(
            Div(
                _main_area_skeleton(),
                id=main_root_id,
                hx_get=hx_fragment_path,
                hx_trigger=hx_trigger,
                hx_swap="innerHTML",
                **{"data-page-loader": "full"},
            ),
            cls="main",
            id="main-content",
        )

    inner = Div(
        Aside(
            Div(
                Span("IFC Pipeline"),
                Span(
                    "HTMX",
                    style="opacity:0.7;font-size:12px;margin-left:8px;font-weight:500",
                ),
                cls="sidebar-brand",
            ),
            nav,
            Div(footer_note, cls="sidebar-footer"),
            cls="sidebar",
        ),
        Header(
            Div(header_title, cls="header-title"),
            Div(
                Div(
                    id="header-status",
                    hx_get="/htmx/header-status",
                    hx_trigger="load, every 30s",
                    hx_swap="innerHTML",
                ),
                A(json_label, href=json_href, cls="btn btn-ghost btn-sm"),
                cls="header-actions",
            ),
            cls="header",
        ),
        main_block,
        cls="app",
    )

    page_loader = Div(
        Div(cls="htmx-page-loader__backdrop"),
        Div(
            Div(cls="htmx-page-loader__ring", **{"aria-hidden": "true"}),
            Div("Loading…", cls="htmx-page-loader__label"),
            cls="htmx-page-loader__panel",
        ),
        id="htmx-page-loader",
        cls="htmx-page-loader",
        role="status",
        **{"aria-live": "polite", "aria-hidden": "true"},
    )

    head_children = [
        Title(document_title),
        Link(rel="stylesheet", href="/static/style.css?v=1"),
        Link(rel="preconnect", href="https://fonts.googleapis.com"),
        Link(
            rel="stylesheet",
            href="https://fonts.googleapis.com/css2?family=Inter:wght@400;450;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap",
        ),
        Script(src="https://unpkg.com/htmx.org@2.0.4"),
        Script(src="/static/htmx-loader.js?v=1", defer=True),
        Script(src="/static/htmx-tables.js?v=1", defer=True),
    ]
    if extra_head:
        head_children.extend(extra_head)

    body_children = [inner, page_loader]
    if extra_body:
        body_children.append(NotStr(extra_body))
    page = Html(
        Head(*head_children),
        Body(*body_children),
    )
    return to_xml(page)
