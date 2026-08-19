"""Live log viewer — one container, or every container serving a queue."""

from __future__ import annotations

from urllib.parse import urlencode

from fasthtml.components import (
    A,
    Button,
    Div,
    Input,
    Option,
    P,
    Script,
    Select,
    Span,
)

from pipeline_ui.renderers.htmx_common import ft_html
from pipeline_ui.renderers.htmx_layout import render_htmx_shell_page
from services import docker_logs, redis_service


def _unavailable_panel() -> str:
    return ft_html(
        Div(
            Div(Span("Live logs unavailable", cls="panel-title"), cls="panel-header"),
            Div(
                P(
                    "The docker socket is not mounted into this container, so log "
                    "streaming is disabled.",
                    style="margin:0 0 8px",
                ),
                P(
                    "Add the mount to the dashboard service and restart:",
                    cls="stat-card-sub",
                    style="margin:0 0 8px",
                ),
                Div(
                    "volumes:\n  - /var/run/docker.sock:/var/run/docker.sock:ro\n"
                    "group_add:\n  - \"${DOCKER_GID:-999}\"",
                    cls="log-content code-view",
                    style="white-space:pre;padding:12px",
                ),
                cls="panel-body",
            ),
            cls="panel",
        )
    )


def _toolbar(title: str, subtitle: str, stream_url: str) -> Div:
    return Div(
        Div(
            Div(
                Span(title, cls="logs-title"),
                Span(subtitle, cls="logs-subtitle"),
                cls="logs-heading",
            ),
            Div(
                Span("connecting…", id="log-status", cls="log-status log-status--connecting"),
                Span("0 lines", id="log-count", cls="meta-tag"),
                cls="logs-status-group",
            ),
            cls="logs-bar-row",
        ),
        Div(
            Input(
                type="search",
                id="log-filter",
                cls="input-sm logs-filter",
                placeholder="Filter lines…",
                **{"aria-label": "Filter log lines"},
            ),
            Select(
                Option("All output", value="all"),
                Option("stdout", value="stdout"),
                Option("stderr", value="stderr"),
                id="log-stream-filter",
                cls="filter-select",
                **{"aria-label": "Filter by stream"},
            ),
            Button("Following", id="log-follow", cls="btn btn-primary btn-sm", type="button"),
            Button("Wrap", id="log-wrap", cls="btn btn-ghost btn-sm", type="button"),
            Button("Clear", id="log-clear", cls="btn btn-ghost btn-sm", type="button"),
            A("Raw", href=stream_url, cls="btn btn-ghost btn-sm", target="_blank", rel="noopener"),
            cls="logs-bar-row logs-controls",
        ),
        Div(id="log-legend", cls="logs-legend"),
        cls="logs-bar",
    )


def _viewer(stream_url: str) -> Div:
    return Div(
        Div(Div(id="log-lines", cls="log-lines"), cls="log-scroll"),
        id="logs-root",
        cls="logs-viewer",
        **{"data-stream-url": stream_url},
    )


def render_logs_page(
    container: str | None = None,
    queue: str | None = None,
    tail: int = 200,
) -> str:
    if not docker_logs.available():
        return render_htmx_shell_page(
            document_title="IFC Pipeline — Logs",
            header_title="Logs",
            static_main_inner=_unavailable_panel(),
            json_href="/api/logs/status",
            active_nav="logs",
        )

    if queue:
        title = f"Queue · {queue}"
        try:
            workers = (redis_service.get_overview() or {}).get("workers") or []
        except Exception:
            workers = []
        found = docker_logs.containers_for_queue(queue, workers)
        subtitle = (
            f"{len(found)} container(s) serving this queue"
            if found
            else "no running worker is serving this queue"
        )
        qs = urlencode({"queue": queue, "tail": tail, "follow": "true"})
    elif container:
        c = docker_logs.resolve(container)
        title = f"Container · {(c or {}).get('name', container)}"
        subtitle = (c or {}).get("service", "") or "unknown service"
        qs = urlencode({"container": container, "tail": tail, "follow": "true"})
    else:
        return render_htmx_shell_page(
            document_title="IFC Pipeline — Logs",
            header_title="Logs",
            static_main_inner=_picker_panel(),
            json_href="/api/logs/containers",
            active_nav="logs",
        )

    stream_url = f"/api/logs/stream?{qs}"
    inner = ft_html(
        Div(_toolbar(title, subtitle, stream_url), _viewer(stream_url), cls="logs-page")
    )
    return render_htmx_shell_page(
        document_title=f"IFC Pipeline — Logs · {queue or container}",
        header_title="Logs",
        static_main_inner=inner,
        json_href=stream_url,
        json_label="Raw stream",
        active_nav="logs",
        extra_head=(Script(src="/static/logs-viewer.js?v=1", defer=True),),
    )


def _picker_panel() -> str:
    """No target chosen: list what can be streamed, grouped by queue then service."""
    try:
        workers = (redis_service.get_overview() or {}).get("workers") or []
    except Exception:
        workers = []

    queues = sorted({q for w in workers for q in (w.get("queues") or [])})
    queue_links = [
        A(
            Span(q, cls="logs-pick-name"),
            Span(
                f"{sum(1 for w in workers if q in (w.get('queues') or []))} worker(s)",
                cls="logs-pick-meta",
            ),
            href=f"/htmx/logs/?queue={q}",
            cls="logs-pick",
        )
        for q in queues
    ]

    containers = docker_logs.list_containers()
    container_links = [
        A(
            Span(c["name"].replace("ifcpipeline-", ""), cls="logs-pick-name"),
            Span(c["state"], cls="logs-pick-meta"),
            href=f"/htmx/logs/?container={c['name']}",
            cls="logs-pick",
        )
        for c in containers
    ]

    return ft_html(
        Div(
            Div(
                Div(Span("By queue", cls="panel-title"), cls="panel-header"),
                Div(
                    Div(*queue_links, cls="logs-pick-grid")
                    if queue_links
                    else P("No live workers.", cls="stat-card-sub"),
                    cls="panel-body",
                ),
                cls="panel",
            ),
            Div(
                Div(
                    Span("By container", cls="panel-title"),
                    Span(
                        "this compose project only",
                        cls="panel-hint",
                    ),
                    cls="panel-header",
                ),
                Div(Div(*container_links, cls="logs-pick-grid"), cls="panel-body"),
                cls="panel",
            ),
            cls="htmx-page-stack",
        )
    )
