/**
 * HTMX Network Share — keep aligned with dashboard/static/app.js (Network Share + IFC + syntax).
 */
(function () {
  let nsPath = '';
  let nsEditMode = false;
  let nsCurrentFile = null;
  let cmEditor = null;
  let pendingNsFile = null;
  let nsFilesData = null;
  let ifcViewer = null;

  function api(path) {
    return fetch(path).then(r => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.headers.get('content-type')?.includes('json') ? r.json() : r.text();
    });
  }

  function apiMethod(path, method, body) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body !== undefined) opts.body = JSON.stringify(body);
    return fetch(path, opts).then(async r => {
      if (!r.ok) {
        let detail = `HTTP ${r.status}`;
        try { const j = await r.json(); if (j.detail) detail = j.detail; } catch (_) {}
        throw new Error(detail);
      }
      return r.json();
    });
  }

  function escapeHtml(str) {
    const d = document.createElement('div');
    d.textContent = String(str ?? '');
    return d.innerHTML;
  }

  function formatBytes(b) {
    if (!b || b === 0) return '0 B';
    const u = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(b) / Math.log(1024));
    return (b / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0) + ' ' + u[i];
  }

  function setHtml(id, html) {
    const el = document.getElementById(id);
    if (el) el.innerHTML = html;
  }

  function statusDot(connected, label) {
    const cls = connected === null ? 'loading' : connected ? 'online' : 'offline';
    return `<span class="status-dot ${cls}">${escapeHtml(label)}</span>`;
  }

  const _esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

  function detectFileType(name, content) {
    const ext = (name || '').split('.').pop().toLowerCase();
    if (ext === 'ids' || ext.endsWith('xml')) return 'xml';
    if (ext === 'txt' && content && /^PropertySet:/m.test(content)) return 'pset';
    return 'text';
  }

  function addLineNumbers(html) {
    return html.split('\n').map((line, i) =>
      `<span class="hl-line"><span class="hl-ln">${i + 1}</span>${line}</span>`
    ).join('');
  }

  function hlAttrs(str) {
    if (!str.trim()) return _esc(str);
    let html = '', last = 0, m;
    const re = /([a-zA-Z0-9_:.-]+)\s*=\s*("[^"]*"|'[^']*')/g;
    while ((m = re.exec(str)) !== null) {
      if (m.index > last) html += _esc(str.slice(last, m.index));
      html += `<span class="hl-attr">${_esc(m[1])}</span>=<span class="hl-val">${_esc(m[2])}</span>`;
      last = re.lastIndex;
    }
    if (last < str.length) html += _esc(str.slice(last));
    return html;
  }

  function hlXml(raw) {
    let depth = 0, out = '', i = 0;
    while (i < raw.length) {
      if (raw[i] === '<') {
        if (raw.startsWith('<!--', i)) {
          const end = raw.indexOf('-->', i + 4);
          const s = end === -1 ? raw.slice(i) : raw.slice(i, end + 3);
          out += `<span class="hl-comment">${_esc(s)}</span>`;
          i += s.length;
        } else if (raw.startsWith('<?', i)) {
          const end = raw.indexOf('?>', i + 2);
          const s = end === -1 ? raw.slice(i) : raw.slice(i, end + 2);
          out += `<span class="hl-pi">${_esc(s)}</span>`;
          i += s.length;
        } else if (raw.startsWith('</', i)) {
          const end = raw.indexOf('>', i);
          if (end === -1) { out += _esc(raw.slice(i)); break; }
          depth = Math.max(0, depth - 1);
          const name = (raw.slice(i, end + 1).match(/<\/\s*([^\s>]+)/) || [])[1] || '';
          out += `<span class="hl-d${depth % 7}">&lt;/<span class="hl-tn">${_esc(name)}</span>&gt;</span>`;
          i = end + 1;
        } else {
          const end = raw.indexOf('>', i);
          if (end === -1) { out += _esc(raw.slice(i)); break; }
          const tag = raw.slice(i, end + 1);
          const sc = tag.endsWith('/>');
          const nm = (tag.match(/^<\s*([^\s/>]+)/) || [])[1] || '';
          const nmLen = (tag.match(/^<\s*[^\s/>]+/) || [''])[0].length;
          const rest = tag.slice(nmLen, tag.length - (sc ? 2 : 1));
          const d = depth % 7;
          out += `<span class="hl-d${d}">&lt;<span class="hl-tn">${_esc(nm)}</span>`;
          out += hlAttrs(rest);
          out += `${sc ? '/&gt;' : '&gt;'}</span>`;
          if (!sc) depth++;
          i = end + 1;
        }
      } else {
        const next = raw.indexOf('<', i);
        const txt = next === -1 ? raw.slice(i) : raw.slice(i, next);
        out += _esc(txt);
        i += txt.length;
        if (next === -1) break;
      }
    }
    return out;
  }

  function hlPset(raw) {
    return raw.split('\n').map(line => {
      if (/^\s*#/.test(line)) return `<span class="hl-comment">${_esc(line)}</span>`;

      const pset = line.match(/^(PropertySet:)\s+(\S+)\s+([IT])\s+(.*)/);
      if (pset) {
        const classes = pset[4].split(/,\s*/).map(c =>
          c.trim() ? `<span class="hl-ifc">${_esc(c.trim())}</span>` : ''
        ).join(', ');
        return `<span class="hl-kw">${_esc(pset[1])}</span>\t<span class="hl-pset-name">${_esc(pset[2])}</span>\t<span class="hl-pset-flag">${_esc(pset[3])}</span>\t${classes}`;
      }

      const prop = line.match(/^(\s+)(\S+)(\s+)(\S+)(?:(\s+)(.+))?/);
      if (prop) {
        let h = _esc(prop[1]);
        h += `<span class="hl-prop">${_esc(prop[2])}</span>`;
        h += _esc(prop[3]);
        h += `<span class="hl-type">${_esc(prop[4])}</span>`;
        if (prop[5] && prop[6]) {
          h += _esc(prop[5]);
          h += `<span class="hl-revit">${_esc(prop[6].trimEnd())}</span>`;
        }
        return h;
      }

      return _esc(line);
    }).join('\n');
  }

  function highlightFile(content, filename) {
    const type = detectFileType(filename, content);
    let highlighted;
    switch (type) {
      case 'xml': highlighted = hlXml(content); break;
      case 'pset': highlighted = hlPset(content); break;
      default: highlighted = _esc(content); break;
    }
    return addLineNumbers(highlighted);
  }

  if (typeof CodeMirror !== 'undefined') {
    CodeMirror.defineMode('pset', function() {
      return {
        token: function(stream) {
          if (stream.sol() && stream.peek() === '#') { stream.skipToEnd(); return 'comment'; }
          if (stream.sol() && stream.match('PropertySet:')) return 'keyword';
          if (stream.match(/Ifc[A-Z]\w*/)) return 'tag';
          if (stream.match(/\b[IT]\b/)) return 'atom';
          stream.next();
          return null;
        }
      };
    });
  }

  function getCmMode(filename, content) {
    const ext = (filename || '').split('.').pop().toLowerCase();
    if (ext === 'ids' || ext.endsWith('xml')) return 'xml';
    if (ext === 'txt' && content && /^PropertySet:/m.test(content)) return 'pset';
    if (ext === 'json') return { name: 'javascript', json: true };
    return null;
  }

  function destroyCm() {
    if (cmEditor) {
      cmEditor.toTextArea();
      cmEditor = null;
    }
  }

  async function initIfcViewer(container) {
    const { createNsViewer } = await import('/static/ifc-lite-ns-viewer.js');
    ifcViewer = await createNsViewer(container);
    return ifcViewer;
  }

  function clearIfcModels() {
    if (!ifcViewer) return;
    ifcViewer.clear();
  }

  async function openIfcFile(path) {
    destroyCm();
    nsEditMode = false;

    const container = document.getElementById('ns-ifc-viewer');
    const overlay = document.getElementById('ns-ifc-overlay');
    const statusEl = document.getElementById('ns-ifc-status');

    document.getElementById('ns-empty-file').style.display = 'none';
    document.getElementById('ns-file-view').style.display = 'none';
    document.getElementById('ns-file-edit').style.display = 'none';
    const pdfViewer = document.getElementById('ns-pdf-viewer');
    pdfViewer.style.display = 'none';
    pdfViewer.src = '';
    container.style.display = '';
    overlay.classList.remove('hidden');

    const name = path.split('/').pop();
    document.getElementById('ns-editor-label').textContent = name;
    document.getElementById('ns-editor-filename').textContent = path;
    document.getElementById('ns-editor-actions').style.display = 'none';

    nsCurrentFile = { path, name, isIfc: true };

    document.querySelectorAll('#ns-files .file-item').forEach(el => {
      const onclick = el.getAttribute('onclick') || '';
      el.classList.toggle('active', onclick.includes(escapeHtml(path)));
    });

    try {
      if (!ifcViewer) {
        statusEl.textContent = 'Loading 3D engine...';
        await initIfcViewer(container);
      } else {
        clearIfcModels();
        ifcViewer.resize();
      }

      statusEl.textContent = 'Downloading IFC file...';
      const response = await fetch(`/api/network-share/download?path=${encodeURIComponent(path)}`);
      if (!response.ok) throw new Error(`Download failed (${response.status})`);
      const bytes = new Uint8Array(await response.arrayBuffer());

      statusEl.textContent = 'Parsing IFC model...';
      const modelName = name.replace(/\.ifc$/i, '');
      await ifcViewer.loadIfc(bytes, modelName);

      overlay.classList.add('hidden');

      setTimeout(() => {
        try {
          ifcViewer.fit();
        } catch (e) { console.warn('Camera fit:', e); }
      }, 300);
    } catch (err) {
      console.error('IFC load error:', err);
      statusEl.textContent = 'Failed to load: ' + err.message;
    }
  }

  function openPdfFile(path) {
    destroyCm();
    nsEditMode = false;

    const name = path.split('/').pop();
    document.getElementById('ns-editor-label').textContent = name;
    document.getElementById('ns-editor-filename').textContent = path;
    document.getElementById('ns-editor-actions').style.display = 'none';

    document.getElementById('ns-empty-file').style.display = 'none';
    document.getElementById('ns-file-view').style.display = 'none';
    document.getElementById('ns-file-edit').style.display = 'none';
    document.getElementById('ns-ifc-viewer').style.display = 'none';
    const pdfEl = document.getElementById('ns-pdf-viewer');
    pdfEl.style.display = '';
    pdfEl.src = `/api/network-share/download?path=${encodeURIComponent(path)}&inline=true`;

    nsCurrentFile = { path, name, isPdf: true };

    document.querySelectorAll('#ns-files .file-item').forEach(el => {
      const onclick = el.getAttribute('onclick') || '';
      el.classList.toggle('active', onclick.includes(escapeHtml(path)));
    });
  }

  function openPendingNsFile(filePath) {
    const lower = filePath.toLowerCase();
    if (lower.endsWith('.ifc')) return openIfcFile(filePath);
    if (lower.endsWith('.pdf')) return openPdfFile(filePath);
    return openFile(filePath);
  }

  async function loadNetworkShare() {
    try {
      const [status, files] = await Promise.all([
        api('/api/network-share/status'),
        api(`/api/network-share/files?path=${encodeURIComponent(nsPath)}`),
      ]);
      setHtml('ns-mount-dot', statusDot(status.mounted, status.mounted ? 'Mounted' : 'Offline'));
      renderNsBreadcrumb(nsPath);
      nsFilesData = files;
      renderNsFileList(files);
      if (pendingNsFile) {
        const filePath = pendingNsFile;
        pendingNsFile = null;
        openPendingNsFile(filePath);
      }
    } catch (err) { console.error('Network Share error:', err); }
  }

  function renderNsBreadcrumb(path) {
    const parts = path.split('/').filter(Boolean);
    let html = `<a href="#" onclick="NsHtmx.browseNs('');return false">/network-share</a>`;
    let acc = '';
    parts.forEach(p => { acc += (acc ? '/' : '') + p; const c = acc; html += ` <span class="sep">/</span> <a href="#" onclick="NsHtmx.browseNs('${escapeHtml(c)}');return false">${escapeHtml(p)}</a>`; });
    setHtml('ns-breadcrumb', html);
  }

  function renderNsFileList(files) {
    const dirIcon = '<svg class="file-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>';
    const fileIcon = '<svg class="file-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>';
    const editIcon = '<svg class="file-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="color:var(--accent)"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>';
    const ifcIcon = '<svg class="file-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>';

    if (!files.length) { setHtml('ns-files', '<li class="file-item" style="justify-content:center;color:var(--text-tertiary)">Empty directory</li>'); return; }

    setHtml('ns-files', files.map(f => {
      let icon, cls, click;
      const isActive = !f.is_dir && nsCurrentFile && nsCurrentFile.path === f.path;
      const size = f.is_dir ? '' : `<span class="file-size">${formatBytes(f.size)}</span>`;

      if (f.is_dir) {
        icon = dirIcon; cls = 'dir'; click = `onclick="NsHtmx.browseNs('${escapeHtml(f.path)}')"`;
      } else if (f.ifc_viewable) {
        icon = ifcIcon; cls = 'file ifc'; click = `onclick="NsHtmx.openIfcFile('${escapeHtml(f.path)}')"`;
      } else if (f.pdf_viewable) {
        icon = fileIcon; cls = 'file'; click = `onclick="NsHtmx.openPdfFile('${escapeHtml(f.path)}')"`;
      } else if (f.editable) {
        icon = editIcon; cls = 'file'; click = `onclick="NsHtmx.openFile('${escapeHtml(f.path)}')"`;
      } else {
        icon = fileIcon; cls = 'file'; click = '';
      }
      if (isActive) cls += ' active';
      return `<li class="file-item ${cls}" ${click}>${icon}<span class="file-name">${escapeHtml(f.name)}</span>${size}</li>`;
    }).join(''));
  }

  function filterNsFiles() {
    if (!nsFilesData) return;
    const q = (document.getElementById('ns-file-search')?.value || '').toLowerCase();
    if (!q) {
      renderNsFileList(nsFilesData);
      return;
    }
    const filtered = nsFilesData.filter(f => f.name.toLowerCase().includes(q));
    renderNsFileList(filtered);
  }

  function initNsResize() {
    const handle = document.getElementById('ns-resize-handle');
    const split = document.getElementById('ns-split');
    if (!handle || !split) return;

    let dragging = false;
    let startX, startW;

    handle.addEventListener('mousedown', e => {
      e.preventDefault();
      dragging = true;
      startX = e.clientX;
      startW = split.querySelector('.ns-tree-pane').getBoundingClientRect().width;
      handle.classList.add('dragging');
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
    });

    document.addEventListener('mousemove', e => {
      if (!dragging) return;
      const delta = e.clientX - startX;
      const newW = Math.max(180, Math.min(startW + delta, window.innerWidth * 0.6));
      split.style.setProperty('--ns-tree-w', newW + 'px');
    });

    document.addEventListener('mouseup', () => {
      if (!dragging) return;
      dragging = false;
      handle.classList.remove('dragging');
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    });
  }

  function syncUrl() {
    const u = new URL(window.location.href);
    u.searchParams.set('path', nsPath);
    u.searchParams.delete('open');
    history.replaceState({}, '', u);
  }

  function browseNs(path) {
    nsPath = path;
    const searchInput = document.getElementById('ns-file-search');
    if (searchInput) searchInput.value = '';
    syncUrl();
    loadNetworkShare();
  }

  async function openFile(path) {
    try {
      destroyCm();
      nsEditMode = false;

      const data = await api(`/api/network-share/file?path=${encodeURIComponent(path)}`);
      nsCurrentFile = { path: data.path, name: data.name, content: data.content };

      document.getElementById('ns-empty-file').style.display = 'none';
      document.getElementById('ns-ifc-viewer').style.display = 'none';
      const pdfV = document.getElementById('ns-pdf-viewer');
      pdfV.style.display = 'none';
      pdfV.src = '';
      document.getElementById('ns-editor-actions').style.display = '';
      document.getElementById('ns-editor-filename').textContent = data.path;
      document.getElementById('ns-editor-label').textContent = data.name;

      const viewEl = document.getElementById('ns-file-view');
      const editEl = document.getElementById('ns-file-edit');
      viewEl.innerHTML = highlightFile(data.content, data.name);
      viewEl.classList.add('code-view');
      editEl.value = data.content;
      viewEl.style.display = '';
      editEl.style.display = 'none';
      document.getElementById('ns-edit-toggle').textContent = 'Edit';
      document.getElementById('ns-save-btn').style.display = 'none';
      nsEditMode = false;

      document.querySelectorAll('#ns-files .file-item').forEach(el => {
        const onclick = el.getAttribute('onclick') || '';
        el.classList.toggle('active', onclick.includes(escapeHtml(data.path)));
      });
    } catch (err) {
      console.error('Open file error:', err);
      showToast('Failed to open file: ' + err.message, true);
    }
  }

  function toggleEdit() {
    nsEditMode = !nsEditMode;
    const viewEl = document.getElementById('ns-file-view');
    const editEl = document.getElementById('ns-file-edit');
    const toggleBtn = document.getElementById('ns-edit-toggle');
    const saveBtn = document.getElementById('ns-save-btn');

    if (nsEditMode) {
      destroyCm();
      editEl.value = nsCurrentFile.content;
      viewEl.style.display = 'none';
      editEl.style.display = '';
      toggleBtn.textContent = 'Cancel';
      saveBtn.style.display = '';

      if (typeof CodeMirror !== 'undefined') {
        const mode = getCmMode(nsCurrentFile.name, nsCurrentFile.content);
        const isXml = mode === 'xml';
        cmEditor = CodeMirror.fromTextArea(editEl, {
          mode: mode,
          theme: 'material-ocean',
          lineNumbers: true,
          lineWrapping: true,
          tabSize: 4,
          indentWithTabs: true,
          indentUnit: 4,
          matchBrackets: true,
          autoCloseTags: isXml,
          styleActiveLine: true,
          foldGutter: isXml,
          gutters: isXml
            ? ['CodeMirror-linenumbers', 'CodeMirror-foldgutter']
            : ['CodeMirror-linenumbers'],
          extraKeys: { 'Ctrl-S': function() { saveFile(); } },
        });
        const wrapper = cmEditor.getWrapperElement();
        wrapper.style.flex = '1';
        wrapper.style.minHeight = '0';
        cmEditor.refresh();
        setTimeout(() => cmEditor.refresh(), 50);
        cmEditor.focus();
      } else {
        editEl.focus();
      }
    } else {
      destroyCm();
      viewEl.style.display = '';
      editEl.style.display = 'none';
      toggleBtn.textContent = 'Edit';
      saveBtn.style.display = 'none';
    }
  }

  async function saveFile() {
    if (!nsCurrentFile) return;
    const content = cmEditor ? cmEditor.getValue() : document.getElementById('ns-file-edit').value;
    const saveBtn = document.getElementById('ns-save-btn');

    saveBtn.textContent = 'Saving...';
    saveBtn.disabled = true;

    try {
      await apiMethod(`/api/network-share/file?path=${encodeURIComponent(nsCurrentFile.path)}`, 'PUT', { content });
      nsCurrentFile.content = content;
      document.getElementById('ns-file-view').innerHTML = highlightFile(content, nsCurrentFile.name);

      destroyCm();
      nsEditMode = false;
      document.getElementById('ns-file-view').style.display = '';
      document.getElementById('ns-file-edit').style.display = 'none';
      document.getElementById('ns-edit-toggle').textContent = 'Edit';
      saveBtn.style.display = 'none';

      showToast('File saved successfully');
    } catch (err) {
      console.error('Save error:', err);
      showToast('Failed to save: ' + err.message, true);
    } finally {
      saveBtn.textContent = 'Save';
      saveBtn.disabled = false;
    }
  }

  function showToast(message, isError = false) {
    const toast = document.createElement('div');
    toast.style.cssText = `position:fixed;bottom:24px;right:24px;padding:10px 20px;border-radius:8px;font-size:13px;font-family:var(--font-sans);z-index:999;color:#fff;background:${isError ? 'var(--error)' : 'var(--success)'};box-shadow:var(--shadow-lg);animation:fadeIn .2s ease`;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => { toast.style.opacity = '0'; toast.style.transition = 'opacity .3s'; setTimeout(() => toast.remove(), 300); }, 3000);
  }

  function initNsPage() {
    const root = document.getElementById('ns-split');
    if (!root) return;

    const browse = (root.getAttribute('data-ns-browse') || '').trim();
    const openRel = (root.getAttribute('data-ns-open') || '').trim();
    nsPath = browse;
    if (openRel) pendingNsFile = openRel;

    document.getElementById('ns-edit-toggle')?.addEventListener('click', toggleEdit);
    document.getElementById('ns-save-btn')?.addEventListener('click', saveFile);
    document.getElementById('ns-file-search')?.addEventListener('input', filterNsFiles);
    initNsResize();

    loadNetworkShare();
  }

  window.NsHtmx = {
    browseNs,
    openFile,
    openIfcFile,
    openPdfFile,
  };

  document.addEventListener('DOMContentLoaded', initNsPage);
})();
