"""Network share: classic two-pane UI (same DOM as dashboard #view-network-share)."""

from __future__ import annotations

from html import escape

from fasthtml.components import Link, Script

from pipeline_ui.renderers.htmx_layout import render_htmx_shell_page
from services import network_share_service


def _network_share_extra_head() -> tuple:
    """CodeMirror + network share client (IFC viewer: bundled ``/static/ifc-lite-ns-viewer.js``)."""
    return (
        Link(
            rel="stylesheet",
            href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.18/codemirror.min.css",
        ),
        Link(
            rel="stylesheet",
            href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.18/theme/material-ocean.min.css",
        ),
        Link(
            rel="stylesheet",
            href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.18/addon/fold/foldgutter.min.css",
        ),
        Script(src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.18/codemirror.min.js"),
        Script(src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.18/mode/xml/xml.min.js"),
        Script(src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.18/addon/edit/closetag.min.js"),
        Script(src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.18/addon/edit/matchbrackets.min.js"),
        Script(src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.18/addon/selection/active-line.min.js"),
        Script(src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.18/addon/fold/foldcode.min.js"),
        Script(src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.18/addon/fold/foldgutter.min.js"),
        Script(src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.18/addon/fold/xml-fold.min.js"),
        Script(src="/static/htmx-network-share.js?v=3", defer=True),
    )


def render_network_share_main_html(browse: str, open_: str | None) -> str:
    """Same structure as index.html view-network-share (lines 269–323); ids/classes for style.css."""
    bb = escape(browse)
    oo = escape(open_ or "")
    return f"""<div class="ns-split" id="ns-split" data-ns-browse="{bb}" data-ns-open="{oo}">
        <div class="ns-tree-pane">
          <div class="panel ns-tree-panel">
            <div class="panel-header">
              <span class="panel-title">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
                Files
              </span>
              <span class="ns-mount-dot" id="ns-mount-dot"></span>
            </div>
            <div class="panel-body no-pad">
              <div class="ns-file-search">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                <input type="text" id="ns-file-search" placeholder="Filter files..." autocomplete="off">
              </div>
              <div class="breadcrumb" id="ns-breadcrumb"></div>
              <div class="ns-file-scroll">
                <ul class="file-list" id="ns-files"></ul>
              </div>
            </div>
          </div>
        </div>
        <div class="ns-resize-handle" id="ns-resize-handle"></div>
        <div class="ns-editor-pane">
          <div class="panel ns-editor-panel">
            <div class="panel-header">
              <span class="panel-title" id="ns-editor-title">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                <span id="ns-editor-label">No file selected</span>
              </span>
              <div class="panel-actions" id="ns-editor-actions" style="display:none">
                <span id="ns-editor-filename" style="font-family:var(--font-mono);font-size:11px;color:var(--text-tertiary);margin-right:8px"></span>
                <button type="button" class="btn btn-ghost btn-sm" id="ns-edit-toggle">Edit</button>
                <button type="button" class="btn btn-primary btn-sm" id="ns-save-btn" style="display:none">Save</button>
              </div>
            </div>
            <div class="panel-body no-pad">
              <div class="ns-empty-file" id="ns-empty-file">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1" stroke-linecap="round" stroke-linejoin="round" style="opacity:.25"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                <p>Select a file to view</p>
              </div>
              <pre class="log-content code-view" id="ns-file-view" style="display:none"></pre>
              <textarea id="ns-file-edit" style="display:none;width:100%;height:100%;background:var(--bg-root);color:#c9d1d9;border:none;padding:14px 18px;font-family:var(--font-mono);font-size:12px;line-height:1.7;resize:none;outline:none"></textarea>
              <div id="ns-ifc-viewer" style="display:none">
                <div id="ns-ifc-overlay">
                  <div class="ns-ifc-spinner"></div>
                  <p id="ns-ifc-status">Loading viewer...</p>
                </div>
              </div>
              <iframe id="ns-pdf-viewer" title="PDF" style="display:none;width:100%;height:100%;border:none"></iframe>
            </div>
          </div>
        </div>
      </div>"""


def render_network_share_page(path: str = "", open_file: str | None = None) -> str:
    browse, open_rel = network_share_service.resolve_network_share_paths(path, open_file)
    inner = render_network_share_main_html(browse, open_rel)
    return render_htmx_shell_page(
        document_title="IFC Pipeline — Network Share",
        header_title="Network Share",
        static_main_inner=inner,
        json_href="/api/network-share/status",
        json_label="JSON",
        active_nav="network-share",
        extra_head=_network_share_extra_head(),
    )


def render_network_share_deprecated_fragment() -> str:
    """Legacy /htmx/network-share/content — Network Share is now a static shell page."""
    return (
        '<div class="panel"><div class="panel-body"><p class="stat-card-value" style="margin:0">'
        "This fragment is unused. Open <a class=\"link-job\" href=\"/htmx/network-share/\">"
        "/htmx/network-share/</a>.</p></div></div>"
    )
