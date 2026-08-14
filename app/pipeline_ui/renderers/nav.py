"""Sidebar navigation for the HTMX shell pages."""

from __future__ import annotations

from fasthtml.components import A, Div, Nav, NotStr

_ICO_OVERVIEW = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>"""
_ICO_JOBS = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 7V4a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v3"/><line x1="12" y1="12" x2="12" y2="12.01"/></svg>"""
_ICO_HISTORY = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>"""
_ICO_WORKERS = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M15 2v2"/><path d="M15 20v2"/><path d="M2 15h2"/><path d="M2 9h2"/><path d="M20 15h2"/><path d="M20 9h2"/><path d="M9 2v2"/><path d="M9 20v2"/></svg>"""
_ICO_NETWORK = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="8" rx="2" ry="2"/><rect x="2" y="14" width="20" height="8" rx="2" ry="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/></svg>"""
_ICO_N8N = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>"""
_ICO_DATABASE = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>"""
_ICO_LOGS = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19V5a2 2 0 0 1 2-2h9l5 5v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/><path d="M14 3v5h5"/><path d="M8 13h8"/><path d="M8 17h5"/></svg>"""


def _nav_link(
    active_nav: str,
    key: str,
    label: str,
    page_url: str,
    svg: str,
    *,
    htmx: bool = True,
) -> A:
    cls = "nav-item active" if active_nav == key else "nav-item"
    children = [NotStr(svg), label]
    if not htmx:
        return A(*children, href=page_url, cls=cls)
    return A(
        *children,
        href=page_url,
        cls=cls,
        hx_get=page_url,
        hx_target="#htmx-page-inner",
        hx_swap="outerHTML",
        hx_select="#htmx-page-inner",
        hx_push_url="true",
    )


def render_sidebar_nav(active_nav: str) -> Nav:
    parts: list = []

    parts.append(Div("Overview", cls="nav-section-label"))
    parts.append(_nav_link(active_nav, "overview", "Dashboard", "/htmx/", _ICO_OVERVIEW))

    parts.append(Div("Queue", cls="nav-section-label"))
    parts.append(_nav_link(active_nav, "jobs", "Jobs", "/htmx/jobs/", _ICO_JOBS))
    parts.append(_nav_link(active_nav, "history", "History", "/htmx/history/", _ICO_HISTORY))
    parts.append(_nav_link(active_nav, "workers", "Workers", "/htmx/workers/", _ICO_WORKERS))
    # Full page load: the viewer opens an EventSource in DOMContentLoaded, which an
    # HTMX fragment swap would never fire.
    parts.append(_nav_link(active_nav, "logs", "Logs", "/htmx/logs/", _ICO_LOGS, htmx=False))

    parts.append(Div("Files", cls="nav-section-label"))
    # Full page load: Network Share needs head assets (CodeMirror, htmx-network-share.js) and
    # DOMContentLoaded init — an HTMX swap only replaces #htmx-page-inner, so no hx-get here.
    parts.append(
        _nav_link(active_nav, "network-share", "Network Share", "/htmx/network-share/", _ICO_NETWORK, htmx=False)
    )

    parts.append(Div("Data", cls="nav-section-label"))
    parts.append(_nav_link(active_nav, "n8n", "n8n", "/htmx/n8n/", _ICO_N8N))
    parts.append(_nav_link(active_nav, "database", "Database", "/htmx/database/", _ICO_DATABASE))

    return Nav(*parts, cls="sidebar-nav")
