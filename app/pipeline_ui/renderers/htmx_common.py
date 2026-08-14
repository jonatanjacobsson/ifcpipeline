"""Shared helpers for HTMX HTML fragments (badges, time, tables)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from html import escape
from typing import Any

from fastcore.xml import to_xml
from fasthtml.components import Div, Span


def ft_html(node) -> str:
    """Serialize FastHTML FT nodes to HTML. Prefer over str() when the root uses id= (str() is wrong)."""
    return to_xml(node)


def short_id(job_id: str) -> str:
    if not job_id:
        return "-"
    return job_id[:10] + "..." if len(job_id) > 12 else job_id


def time_ago(iso: str | None) -> str:
    if not iso:
        return "-"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        diff = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()
    except (ValueError, TypeError):
        return "-"
    if diff < 0:
        diff = 0.0
    if diff < 60:
        return f"{int(diff)}s ago"
    if diff < 3600:
        return f"{int(diff // 60)}m ago"
    if diff < 86400:
        return f"{int(diff // 3600)}h ago"
    return f"{int(diff // 86400)}d ago"


def truncate(s: str, length: int = 50) -> str:
    if not s:
        return ""
    return s if len(s) <= length else s[: length - 3] + "..."


def badge(status: str, label: str | None = None) -> Span:
    """Badge whose colour comes from *status* and whose text is *label* (default: status)."""
    raw = str(status or "unknown")
    safe = re.sub(r"[^a-zA-Z0-9_-]", "-", raw).strip("-") or "unknown"
    return Span(escape(str(label if label is not None else raw)), cls=f"badge badge-{safe}")


def worker_state_badge(state: str | None) -> Span:
    """Reuse the job-status palette for worker state, but keep the worker's own wording."""
    s = (state or "").lower()
    if s == "busy":
        return badge("started", "busy")
    if s == "idle":
        return badge("finished", "idle")
    return badge(str(state or "unknown"))


def status_dot(connected: bool | None, label: str) -> Span:
    if connected is None:
        cls = "loading"
    elif connected:
        cls = "online"
    else:
        cls = "offline"
    return Span(escape(label), cls=f"status-dot {cls}")


def format_datetime_local(iso: str | None) -> str:
    """Human-readable local datetime for detail views (matches SPA `formatDate` intent)."""
    if not iso:
        return "-"
    try:
        s = str(iso).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return escape(str(iso)[:40])


def format_duration(seconds: float | int | None) -> str:
    """Format seconds like classic dashboard `formatDuration` (app.js)."""
    if seconds is None:
        return "—"
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return "—"
    if s < 60:
        return f"{round(s)}s"
    m = int(s // 60)
    r = round(s % 60)
    if m < 60:
        return f"{m}m {r}s"
    h = m // 60
    return f"{h}h {m % 60}m"


def format_bytes(n: int | float | None) -> str:
    if n is None:
        return "-"
    try:
        b = float(n)
    except (TypeError, ValueError):
        return "-"
    if b >= 1e9:
        return f"{b / 1e9:.1f} GB"
    if b >= 1e6:
        return f"{b / 1e6:.1f} MB"
    if b >= 1e3:
        return f"{b / 1e3:.1f} KB"
    return str(int(b)) + " B"


def expand_embedded_json_strings(val: Any, max_depth: int = 16) -> Any:
    """Recursively parse string values that hold JSON objects/arrays (e.g. meta.views_by_type).

    Workers sometimes store nested payloads as JSON strings; expanding them makes the UI
    pretty-print and search behave like real nested JSON.
    """
    if max_depth <= 0:
        return val
    if isinstance(val, dict):
        return {k: expand_embedded_json_strings(v, max_depth - 1) for k, v in val.items()}
    if isinstance(val, list):
        return [expand_embedded_json_strings(x, max_depth - 1) for x in val]
    if isinstance(val, str):
        s = val.strip()
        if len(s) < 2:
            return val
        first, last = s[0], s[-1]
        if (first == "{" and last == "}") or (first == "[" and last == "]"):
            try:
                parsed = json.loads(val)
            except (json.JSONDecodeError, TypeError, ValueError):
                return val
            if isinstance(parsed, (dict, list)):
                return expand_embedded_json_strings(parsed, max_depth - 1)
            return parsed
    return val


def highlight_json_html(obj: Any, max_len: int = 80000) -> str:
    """Mirror ``dashboard/static/app.js`` ``highlightJson`` — pretty JSON with ``.jh-*`` spans."""
    if isinstance(obj, str):
        json_str = obj
    else:
        json_str = json.dumps(obj, indent=2, default=str)
    if len(json_str) > max_len:
        json_str = json_str[:max_len] + "\n…"
    s = json_str
    s = re.sub(r'("(?:\\.|[^"\\])*")\s*:', r'<span class="jh-key">\1</span>:', s)
    s = re.sub(r':\s*("(?:\\.|[^"\\])*")', r': <span class="jh-str">\1</span>', s)
    s = re.sub(r":\s*(true|false)\b", r': <span class="jh-bool">\1</span>', s)
    s = re.sub(r":\s*(null)\b", r': <span class="jh-null">\1</span>', s)
    s = re.sub(
        r":\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\b",
        r': <span class="jh-num">\1</span>',
        s,
    )
    return s


def basename_path(p: str | None) -> str:
    if not p:
        return ""
    return str(p).replace("\\", "/").rsplit("/", 1)[-1]


def share_relative_path(path: str | None) -> str | None:
    """Map a worker-side job path to a path relative to the Network Share root.

    Workers see the pipeline volumes as ``/uploads`` and ``/output``; the dashboard mounts
    them under ``NETWORK_SHARE_PATH`` as ``uploads/`` and ``output/``, so a job's
    ``/uploads/foo.ifc`` becomes ``uploads/foo.ifc`` and can be opened in the viewer.
    Returns ``None`` for paths outside the share (no link is rendered).
    """
    if not path:
        return None
    normalized = str(path).replace("\\", "/")
    for marker in ("/uploads/", "/output/", "/examples/"):
        idx = normalized.lower().find(marker)
        if idx >= 0:
            return marker.strip("/") + "/" + normalized[idx + len(marker) :]
    return None


def service_card(title: Any, rows: list[tuple[str, str]]) -> Div:
    return Div(
        Div(Span(title, cls="service-card-title"), cls="service-card-header"),
        Div(
            *[
                Div(
                    Span(escape(k), cls="service-card-key"),
                    Span(escape(v), cls="service-card-val"),
                    cls="service-card-row",
                )
                for k, v in rows
            ],
            cls="service-card-body",
        ),
        cls="service-card",
    )
