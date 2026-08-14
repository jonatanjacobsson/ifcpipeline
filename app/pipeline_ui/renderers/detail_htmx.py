"""Job detail (active RQ or history) — SPA parity grid, logs, actions."""

from __future__ import annotations

import glob
import json
import os
from typing import Any
from urllib.parse import quote

from fasthtml.components import A, Button, Div, Input, Label, NotStr, P, Pre, Span

from config import settings
from pipeline_ui.renderers.htmx_common import (
    badge,
    basename_path,
    expand_embedded_json_strings,
    format_datetime_local,
    highlight_json_html,
    short_id,
    share_relative_path,
)
from pipeline_ui.renderers.htmx_layout import render_htmx_shell_page
from services import job_history_service, redis_service

_BACK_SVG = (
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>'
)


def short_id_display(jid: str) -> str:
    if len(jid) <= 14:
        return jid
    return jid[:12] + "…"


def _merge_job(job_id: str) -> dict | None:
    try:
        return redis_service.get_job_detail(job_id)
    except Exception:
        pass
    return job_history_service.get_job_from_history(job_id)


def _log_files_for_job(job_id: str, job: dict) -> list[dict]:
    files = list(job.get("log_files") or [])
    if files:
        return files
    logs_dir = settings.WORKER_LOGS_DIR
    pattern = os.path.join(logs_dir, f"{job_id}-*")
    out = []
    for path in sorted(glob.glob(pattern)):
        fname = os.path.basename(path)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        out.append({"filename": fname, "size_bytes": size})
    return out


def _coerce_args_dict(job: dict) -> dict | None:
    """Job args are normally a dict; tolerate a JSON string for display and batch/script rows."""
    raw = job.get("args")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return None


_DETAIL_JSON_MAX = 250_000


def _json_display_block(expanded: Any) -> Div:
    """Syntax-highlighted JSON with optional substring check (use Ctrl+F on the block to search)."""
    return Div(
        Input(
            type="search",
            cls="json-block-filter",
            placeholder="Quick check: substring missing? (outline) · Ctrl+F to search",
            **{
                "aria-label": "Check JSON substring",
                "oninput": (
                    "var w=this.closest('.detail-json-wrap');var p=w&&w.querySelector('pre.detail-json');"
                    "if(!p)return;var q=this.value.trim().toLowerCase();"
                    "p.classList.toggle('json-filter-miss',q.length>0&&(p.innerText||'').toLowerCase().indexOf(q)<0);"
                ),
            },
        ),
        Pre(
            NotStr(highlight_json_html(expanded, max_len=_DETAIL_JSON_MAX)),
            cls="detail-json",
            **{"tabindex": "0"},
        ),
        cls="detail-json-wrap",
    )


def _fmt_cell(val) -> Any:
    """Match SPA ``fmt()`` / ``highlightJson``: dict/list → highlighted ``<pre>``, else escaped text."""
    if val is None:
        return Span("None", cls="json-none", style="color:var(--text-tertiary)")
    if isinstance(val, (dict, list)):
        return _json_display_block(expand_embedded_json_strings(val))
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, (dict, list)):
                return _json_display_block(expand_embedded_json_strings(parsed))
        except (json.JSONDecodeError, TypeError):
            pass
    return Pre(str(val), cls="detail-json")


def _ns_link(path: str | None) -> str | None:
    """Return href for HTMX network-share (browse folder + open file)."""
    rel = share_relative_path(path)
    if not rel:
        return None
    if "/" in rel:
        parent = rel.rsplit("/", 1)[0]
        return f"/htmx/network-share/?path={quote(parent)}&open={quote(rel)}"
    return f"/htmx/network-share/?open={quote(rel)}"


def _file_link_cell(path: str | None) -> Div:
    if not path:
        return Div("-")
    rel = share_relative_path(path)
    label = basename_path(path.replace("\\", "/"))
    if href := _ns_link(path):
        return Div(
            A(
                label,
                href=href,
                title="Open in Network Share",
                cls="link-job",
            ),
            Span(
                f" {rel}",
                style="margin-left:6px;font-family:var(--font-mono);font-size:11px;color:var(--text-tertiary)",
            ),
        )
    return Div(
        Span(str(path), style="font-family:var(--font-mono);font-size:12px"),
    )


def _detail_grid_from_job(job: dict) -> Div:
    """Build `.detail-grid` matching SPA job-detail-grid / history-detail-grid."""
    is_hist = job.get("source") == "history"
    jid = job.get("id") or ""
    meta = job.get("meta") or {}
    args = _coerce_args_dict(job)
    func_name = job.get("func_name") or ""
    batch_file = (args or {}).get("batch_file") if args else None
    script_path = (args or {}).get("script_path") if args else None

    cells: list = []
    cells.extend(
        [
            Div("ID", cls="detail-key"),
            Div(str(jid), cls="detail-val", style="font-family:var(--font-mono);font-size:12px"),
            Div("Status", cls="detail-key"),
            Div(badge(job.get("status") or ""), cls="detail-val"),
        ]
    )
    if not is_hist:
        cells.extend(
            [
                Div("Queue", cls="detail-key"),
                Div(str(job.get("queue") or "-"), cls="detail-val"),
            ]
        )

    cells.extend(
        [
            Div("Function", cls="detail-key"),
            Div(
                str(func_name or job.get("name") or "-"),
                cls="detail-val",
                style="font-family:var(--font-mono);font-size:12px",
            ),
        ]
    )

    if args:
        cells.extend([Div("Arguments", cls="detail-key"), Div(_fmt_cell(args), cls="detail-val")])
    else:
        cells.extend([Div("Name", cls="detail-key"), Div(_fmt_cell(job.get("name")), cls="detail-val")])

    if batch_file:
        cells.extend(
            [
                Div("Batch File", cls="detail-key"),
                Div(_file_link_cell(batch_file), cls="detail-val"),
            ]
        )
    if script_path:
        cells.extend(
            [
                Div("Script", cls="detail-key"),
                Div(_file_link_cell(script_path), cls="detail-val"),
            ]
        )

    cells.extend(
        [
            Div("Created", cls="detail-key"),
            Div(format_datetime_local(job.get("created_at")), cls="detail-val"),
            Div("Enqueued", cls="detail-key"),
            Div(format_datetime_local(job.get("enqueued_at")), cls="detail-val"),
            Div("Ended", cls="detail-key"),
            Div(format_datetime_local(job.get("ended_at")) if job.get("ended_at") else "-", cls="detail-val"),
        ]
    )

    if not is_hist:
        cells.extend(
            [
                Div("Result", cls="detail-key"),
                Div(_fmt_cell(job.get("result")), cls="detail-val"),
            ]
        )

    exc = job.get("exc_info")
    cells.extend(
        [
            Div("Exception", cls="detail-key"),
            Div(
                Pre(
                    str(exc)[:120000],
                    cls="detail-json",
                    style="color:var(--error)",
                )
                if exc
                else Span("None", style="color:var(--text-tertiary)"),
                cls="detail-val",
            ),
        ]
    )

    cells.extend(
        [
            Div("Meta", cls="detail-key"),
            Div(_fmt_cell(meta if meta else None), cls="detail-val"),
        ]
    )

    return Div(*cells, cls="detail-grid")


def _job_logs_panel(job: dict, jid: str) -> Div | None:
    """Right-column logs panel (tabs + lazy log viewer). ``None`` if no log files or archived job."""
    in_redis = job.get("source") != "history"
    log_files = _log_files_for_job(jid, job)
    if not log_files or not in_redis:
        return None
    eid = quote(str(jid), safe="")
    tab_btns = []
    for lf in log_files:
        fn = lf.get("filename") or ""
        short = fn.replace(f"{jid}-", "", 1) if fn.startswith(f"{jid}-") else fn
        tab_btns.append(
            Button(
                f"{short} ({lf.get('size_bytes', 0)} B)",
                cls="btn btn-ghost btn-sm",
                style="font-size:11px;margin-right:4px",
                hx_get=f"/htmx/jobs/{eid}/log-view?filename={quote(fn, safe='')}",
                hx_target="#log-viewer",
                hx_swap="innerHTML",
                hx_indicator="#job-log-view-indicator",
            )
        )
    first_fn = quote(log_files[0].get("filename", ""), safe="")
    viewer = Div(
        Span(**{"aria-hidden": "true"}, id="job-log-view-indicator", cls="htmx-inline-indicator htmx-indicator"),
        Div(
            id="log-viewer",
            hx_get=f"/htmx/jobs/{eid}/log-view?filename={first_fn}",
            hx_trigger="load",
            hx_swap="innerHTML",
            hx_indicator="#job-log-view-indicator",
        ),
        style="position:relative",
    )
    return Div(
        Div(
            Span("Logs", cls="panel-title"),
            Div(*tab_btns, cls="panel-actions", style="display:flex;flex-wrap:wrap;gap:4px"),
            cls="panel-header",
            style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px",
        ),
        Div(viewer, cls="panel-body no-pad"),
        cls="panel",
    )


def render_job_logs_panel_fragment(job_id: str) -> str:
    """HTML for the job detail logs column (loaded after main grid via ``hx-trigger=\"load\"``)."""
    job = _merge_job(job_id)
    if not job:
        return str(Div(P("Job not found.", cls="error"), cls="panel"))
    jid = job.get("id") or job_id
    panel = _job_logs_panel(job, str(jid))
    if not panel:
        return str(
            Div(
                P("No log files for this job.", style="padding:12px;color:var(--text-tertiary);margin:0"),
                cls="panel",
            )
        )
    return str(panel)


def render_job_detail_fragment(job_id: str) -> str:
    job = _merge_job(job_id)
    if not job:
        return str(Div(P("Job not found.", cls="error"), cls="panel"))

    jid = job.get("id") or job_id
    in_redis = job.get("source") != "history"
    st = job.get("status") or ""

    actions = []
    if in_redis:
        actions.append(
            Button(
                "Delete",
                cls="btn btn-danger btn-sm",
                hx_delete=f"/api/rq/jobs/{jid}",
                hx_confirm="Delete this job from the queue?",
                hx_swap="none",
                **{"hx-on::after-request": "if(event.detail.successful) location.href='/htmx/jobs/'"},
            )
        )
        if st == "failed":
            actions.append(
                Button(
                    "Requeue",
                    cls="btn btn-ghost btn-sm",
                    hx_post=f"/api/rq/jobs/{jid}/requeue",
                    hx_confirm="Requeue this job?",
                    hx_swap="none",
                    **{"hx-on::after-request": "if(event.detail.successful) location.reload()"},
                )
            )

    header_inner = [Span(f"Job {short_id(jid)}", cls="panel-title")]
    if actions:
        header_inner.append(Div(*actions, cls="panel-actions", style="display:flex;gap:8px"))

    grid = _detail_grid_from_job(job)
    main_panel = Div(
        Div(
            *header_inner,
            cls="panel-header",
            style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px",
        ),
        Div(grid, cls="panel-body no-pad"),
        cls="panel",
    )

    eid = quote(str(jid), safe="")
    deferred_logs = _job_logs_panel(job, str(jid))
    logs_panel = None
    if deferred_logs:
        logs_panel = Div(
            Span(**{"aria-hidden": "true"}, id="job-logs-panel-indicator", cls="htmx-inline-indicator htmx-indicator"),
            Div(
                Div(
                    Div(cls="htmx-main-skeleton__line htmx-main-skeleton__line--lg"),
                    Div(cls="htmx-main-skeleton__line htmx-main-skeleton__line--md"),
                    cls="htmx-main-skeleton",
                    style="min-height:100px;padding:8px 0",
                ),
                id="job-logs-panel-mount",
                hx_get=f"/htmx/jobs/{eid}/logs-panel",
                hx_trigger="load",
                hx_swap="innerHTML",
                hx_indicator="#job-logs-panel-indicator",
            ),
            cls="job-detail-right",
        )

    back_row = Div(
        A(
            NotStr(_BACK_SVG),
            " Back to Jobs",
            href="/htmx/jobs/",
            cls="btn btn-ghost",
        ),
        style="margin-bottom:14px",
    )

    left = Div(main_panel, cls="job-detail-left")
    if logs_panel:
        layout_inner = Div(
            left,
            logs_panel,
            cls="job-detail-layout",
        )
    else:
        layout_inner = Div(left, cls="job-detail-layout no-logs")

    return str(Div(back_row, layout_inner))


def render_history_detail_fragment(job_id: str) -> str:
    job = _merge_job(job_id)
    if not job:
        return str(Div(P("Job not found.", cls="error"), cls="panel"))

    jid = job.get("id") or job_id
    back_row = Div(
        A(
            NotStr(_BACK_SVG),
            " Back to History",
            href="/htmx/history/",
            cls="btn btn-ghost",
        ),
        style="margin-bottom:14px",
    )
    grid = _detail_grid_from_job(job)
    panel = Div(
        Div(
            Span(f"Archived Job {short_id(jid)}", cls="panel-title"),
            Span(
                "FROM HISTORY",
                cls="badge badge-deferred",
                style="margin-left:8px;font-size:10px",
            ),
            cls="panel-header",
        ),
        Div(grid, cls="panel-body no-pad"),
        cls="panel",
    )
    return str(Div(back_row, panel))


def render_job_detail_page(job_id: str) -> str:
    esc = job_id
    return render_htmx_shell_page(
        document_title=f"IFC Pipeline — Job {short_id_display(job_id)}",
        header_title=f"Job {short_id_display(job_id)}",
        hx_fragment_path=f"/htmx/jobs/{esc}/content",
        json_href=f"/api/rq/jobs/{esc}",
        json_label="JSON",
        active_nav="jobs",
        hx_trigger="load",
    )


def render_log_view_fragment(job_id: str, filename: str) -> str:
    try:
        text = redis_service.get_log_content(job_id, filename)
    except Exception as e:
        return str(P(f"Could not read log: {str(e)}", cls="error"))
    safe = text[:200000]
    return str(
        Div(
            Label(
                Input(type="checkbox", checked=True, id="log-wrap-toggle"),
                " Wrap",
                style="font-size:11px;display:block;margin-bottom:6px",
                onclick="var p=document.getElementById('log-pre');var c=document.getElementById('log-wrap-toggle');if(p&&c){p.style.whiteSpace=c.checked?'pre-wrap':'pre';}",
            ),
            Pre(
                safe,
                id="log-pre",
                style="white-space:pre-wrap;font-size:12px;max-height:480px;overflow:auto",
                cls="log-content",
            ),
        )
    )


def render_history_detail_page(job_id: str) -> str:
    esc = job_id
    return render_htmx_shell_page(
        document_title=f"IFC Pipeline — History {short_id_display(job_id)}",
        header_title=f"History job {short_id_display(job_id)}",
        hx_fragment_path=f"/htmx/history/{esc}/content",
        json_href=f"/api/rq/history/{esc}",
        json_label="JSON",
        active_nav="history",
        hx_trigger="load",
    )

