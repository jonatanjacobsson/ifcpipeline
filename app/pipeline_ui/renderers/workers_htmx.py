"""Queues as cards, each holding the workers that serve it.

The unit an operator thinks in is the queue — "is ifcclash moving?" — not the worker,
so queues are the cards and workers are rows inside them. Activity is shown by state
rather than by number alone: a card with queued work but no worker reads as blocked,
which a bare depth count does not.
"""

from __future__ import annotations

from fasthtml.components import A, Div, P, Span

from pipeline_ui.renderers.htmx_common import ft_html, short_id, time_ago
from pipeline_ui.renderers.htmx_layout import render_htmx_shell_page
from routers import rq
from services import docker_logs

_ICO_LOGS = (
    '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M4 19V5a2 2 0 0 1 2-2h9l5 5v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/>'
    '<path d="M14 3v5h5"/><path d="M8 13h8"/><path d="M8 17h5"/></svg>'
)


def _queue_state(q: dict, worker_count: int) -> tuple[str, str]:
    """(state, label) — drives the card's accent and its animation."""
    if q["started"]:
        return "running", "running"
    if q["queued"] and worker_count == 0:
        return "blocked", "no worker"
    if q["queued"]:
        return "waiting", "queued"
    if worker_count == 0:
        return "offline", "no worker"
    return "idle", "idle"


def _stat(label: str, value, *, tone: str = "") -> Div:
    return Div(
        Div(str(value), cls=f"qstat-value {tone}".strip()),
        Div(label, cls="qstat-label"),
        cls="qstat",
    )


def _worker_row(w: dict, logs_available: bool) -> Div:
    state = (w.get("state") or "unknown").lower()
    job = w.get("current_job_id")
    name = str(w.get("name") or "")

    right = []
    if job:
        right.append(
            A(short_id(job), href=f"/htmx/jobs/{job}", cls="wrow-job", title=job)
        )
    else:
        right.append(Span("—", cls="wrow-job wrow-job--none"))

    if logs_available and w.get("hostname"):
        right.append(
            A(
                Span(cls="wrow-logs-ico"),
                "logs",
                href=f"/htmx/logs/?container={w['hostname']}",
                cls="wrow-logs",
                title=f"Live logs for {name}",
            )
        )

    return Div(
        Span(cls=f"wdot wdot--{state}", title=state),
        Span(name[:12], cls="wrow-name", title=name),
        Div(*right, cls="wrow-right"),
        cls=f"wrow wrow--{state}",
    )


def render_workers_fragment() -> str:
    try:
        overview = rq.overview()
    except Exception as e:
        return ft_html(Div(P(f"Could not load workers: {e}", cls="error")))

    workers = overview.get("workers") or []
    queues = overview.get("queues") or []
    totals = overview.get("totals") or {}
    stale = int(totals.get("stale_workers") or 0)
    logs_available = docker_logs.available()

    by_queue: dict[str, list[dict]] = {}
    for w in workers:
        for q in w.get("queues") or []:
            by_queue.setdefault(q, []).append(w)

    # A queue with registered workers but no RQ queue object still deserves a card.
    known = {q["name"] for q in queues}
    for name in by_queue:
        if name not in known:
            queues.append(
                {"name": name, "queued": 0, "started": 0, "failed": 0,
                 "finished": 0, "scheduled": 0, "deferred": 0}
            )

    # Busiest and most broken first — the cards you need are at the top.
    queues.sort(
        key=lambda q: (
            -(q["started"] or 0),
            -(q["queued"] or 0),
            -(q["failed"] or 0),
            q["name"],
        )
    )

    cards = []
    for q in queues:
        qw = sorted(by_queue.get(q["name"], []), key=lambda w: str(w.get("name") or ""))
        state, label = _queue_state(q, len(qw))

        header = Div(
            Div(
                Span(cls=f"qpulse qpulse--{state}"),
                Span(q["name"], cls="qcard-name"),
                cls="qcard-title",
            ),
            Span(label, cls=f"qbadge qbadge--{state}"),
            cls="qcard-header",
        )

        stats = Div(
            _stat("queued", q["queued"], tone="accent" if q["queued"] else ""),
            _stat("running", q["started"], tone="run" if q["started"] else ""),
            _stat("failed", q["failed"], tone="err" if q["failed"] else ""),
            _stat("workers", len(qw), tone="" if qw else "err"),
            cls="qcard-stats",
        )

        if qw:
            worker_block = Div(*[_worker_row(w, logs_available) for w in qw], cls="qcard-workers")
        else:
            worker_block = Div(
                P("No worker is listening on this queue.", cls="qcard-empty"),
                cls="qcard-workers",
            )

        actions = [
            A("Jobs", href=f"/htmx/jobs/?queue={q['name']}", cls="btn btn-ghost btn-sm"),
        ]
        if logs_available and qw:
            actions.append(
                A(
                    "Queue logs",
                    href=f"/htmx/logs/?queue={q['name']}",
                    cls="btn btn-ghost btn-sm qcard-logs",
                )
            )

        cards.append(
            Div(
                header,
                stats,
                worker_block,
                Div(*actions, cls="qcard-actions"),
                cls=f"qcard qcard--{state}",
            )
        )

    summary = Div(
        _stat("queues", len(queues)),
        _stat("workers online", len(workers), tone="accent"),
        _stat("running", totals.get("started") or 0, tone="run" if totals.get("started") else ""),
        _stat("queued", totals.get("queued") or 0, tone="accent" if totals.get("queued") else ""),
        *([_stat("stale registrations", stale, tone="err")] if stale else []),
        cls="qsummary",
    )

    notes = []
    if stale:
        notes.append(
            P(
                f"{stale} worker registration(s) in Redis have no recent heartbeat and are "
                "not shown — they are leaked records from workers that died without "
                "deregistering.",
                cls="qnote",
            )
        )
    if not logs_available:
        notes.append(
            P(
                "Live logs are unavailable: the docker socket is not mounted into the "
                "dashboard container.",
                cls="qnote",
            )
        )

    return ft_html(
        Div(
            summary,
            *notes,
            Div(*cards, cls="qgrid") if cards else P("No queues found.", cls="qnote"),
            cls="htmx-page-stack",
        )
    )


def render_workers_page() -> str:
    return render_htmx_shell_page(
        document_title="IFC Pipeline — Workers",
        header_title="Workers",
        hx_fragment_path="/htmx/workers/content",
        json_href="/api/rq/overview",
        json_label="JSON",
        active_nav="workers",
        hx_trigger="load, every 5s",
    )
