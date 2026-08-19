/**
 * HTMX dashboard: sticky first column (with CSS), client sort, optional single
 * client-side "Search table…" toolbar (whole-row match). Re-inits on htmx:afterSwap.
 * Skips tables marked data-table-skip-enhance.
 *
 * Server-backed fragments that include input[name=search] skip the client toolbar,
 * except tables under [data-htmx-client-table-search] (e.g. Revit warning leaderboard
 * next to a form that searches the models table).
 */
(function () {
  'use strict';

  function parseCellText(td) {
    return (td && td.innerText) ? td.innerText.trim() : '';
  }

  function cmp(a, b) {
    const na = parseFloat(a);
    const nb = parseFloat(b);
    if (!isNaN(na) && !isNaN(nb) && a === String(na) && b === String(nb)) {
      return na - nb;
    }
    return a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' });
  }

  /** Cells with data-sort-value (seconds) sort numerically; -1 = missing, always last. */
  function cmpDataSortValue(a, b, dir) {
    const na = parseFloat(a);
    const nb = parseFloat(b);
    const aMiss = a === '-1' || isNaN(na);
    const bMiss = b === '-1' || isNaN(nb);
    if (aMiss && bMiss) return 0;
    if (aMiss) return 1;
    if (bMiss) return -1;
    const c = na - nb;
    return dir === 'asc' ? c : -c;
  }

  function sortTable(table, colIndex, toggle) {
    const tbody = table.tBodies[0];
    if (!tbody) return;
    const rows = Array.from(tbody.rows);
    const cur = table.dataset.sortCol;
    let dir = table.dataset.sortDir === 'asc' ? 'asc' : 'desc';
    if (toggle) {
      if (String(colIndex) === cur) {
        dir = dir === 'asc' ? 'desc' : 'asc';
      } else {
        dir = 'desc';
      }
    }
    table.dataset.sortCol = String(colIndex);
    table.dataset.sortDir = dir;

    rows.sort(function (ra, rb) {
      const ca = ra.cells[colIndex];
      const cb = rb.cells[colIndex];
      const da = ca && ca.dataset ? ca.dataset.sortValue : undefined;
      const db = cb && cb.dataset ? cb.dataset.sortValue : undefined;
      if (da !== undefined && db !== undefined) {
        return cmpDataSortValue(String(da), String(db), dir);
      }
      const ta = parseCellText(ca);
      const tb = parseCellText(cb);
      const c = cmp(ta, tb);
      return dir === 'asc' ? c : -c;
    });
    rows.forEach(function (r) { tbody.appendChild(r); });
  }

  /** Show/hide rows where any cell text matches query (case-insensitive substring). */
  function filterDataTableRows(table, query) {
    const tbody = table.tBodies[0];
    if (!tbody) return;
    const q = (query || '').trim().toLowerCase();
    Array.from(tbody.rows).forEach(function (row) {
      if (!q) {
        row.style.display = '';
        return;
      }
      let text = '';
      for (let i = 0; i < row.cells.length; i++) {
        text += parseCellText(row.cells[i]) + ' ';
      }
      row.style.display = text.toLowerCase().indexOf(q) !== -1 ? '' : 'none';
    });
  }

  function getFragmentRoot(table) {
    return table.closest('[id$="-htmx-root"]') || table.closest('#main-content') || document.body;
  }

  function shouldInjectClientSearchToolbar(table) {
    if (table.closest('[data-htmx-client-table-search]') || table.closest('.htmx-client-table-search')) return true;
    const root = getFragmentRoot(table);
    return !root.querySelector('input[name="search"]');
  }

  function injectToolbar(wrap, table) {
    if (wrap.querySelector('.htmx-table-toolbar')) return;
    const bar = document.createElement('div');
    bar.className = 'htmx-table-toolbar';
    const inp = document.createElement('input');
    inp.type = 'text';
    inp.className = 'input-sm htmx-table-search';
    inp.placeholder = 'Search table…';
    inp.setAttribute('aria-label', 'Search table');
    inp.addEventListener('input', function () {
      filterDataTableRows(table, inp.value);
    });
    bar.appendChild(inp);
    wrap.insertBefore(bar, wrap.firstChild);
  }

  function enhanceTable(table) {
    if (table.closest('[data-table-skip-enhance]')) return;
    if (table.dataset.htmxTableEnhanced) return;
    const wrap = table.closest('.table-scroll');
    if (!wrap || !wrap.classList.contains('table-sticky-first')) return;

    table.dataset.htmxTableEnhanced = '1';

    const thead = table.querySelector('thead');
    if (!thead) return;
    const hdrRow = thead.querySelector('tr');
    if (!hdrRow) return;

    if (shouldInjectClientSearchToolbar(table)) {
      injectToolbar(wrap, table);
    }

    Array.from(hdrRow.cells).forEach(function (th, colIndex) {
      if (th.querySelector('a[hx-get]')) return;
      if (th.classList.contains('no-sort')) return;
      th.classList.add('sortable');
      th.style.cursor = 'pointer';
      th.title = 'Sort';
      th.addEventListener('click', function (e) {
        if (e.target.closest('a')) return;
        sortTable(table, colIndex, true);
      });
    });
  }

  function scan(root) {
    root = root || document;
    root.querySelectorAll('table.data-table').forEach(enhanceTable);
  }

  document.addEventListener('DOMContentLoaded', function () { scan(document); });
  document.body.addEventListener('htmx:afterSwap', function (evt) {
    scan(evt.detail && evt.detail.target ? evt.detail.target : document);
  });
})();
