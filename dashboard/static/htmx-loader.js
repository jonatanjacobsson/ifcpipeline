/**
 * Full-screen loading overlay for HTMX swaps into main content.
 * - Click/submit: show overlay when targeting a main root.
 * - load: show overlay for the same targets, but not for hx-trigger interval polls (`every …`).
 * Opt-in via data-page-loader="full" on the swap target, or legacy id map.
 */
(function () {
  var LEGACY_MAIN_IDS = {
    'htmx-page-inner': true,
    'ix-htmx-root': true,
    'jobs-htmx-root': true,
    'history-htmx-root': true,
    'ifc-qa-root': true,
  };

  function isLegacyMainId(id) {
    if (!id) return false;
    return LEGACY_MAIN_IDS[id] === true;
  }

  function isMainSwapTarget(detail) {
    var el = detail.target;
    if (!el || !el.getAttribute) return false;
    if (el.getAttribute('data-page-loader') === 'full') return true;
    return isLegacyMainId(el.id);
  }

  function isIntervalTrigger(detail) {
    var te = detail.requestConfig && detail.requestConfig.triggeringEvent;
    if (!te || !te.type) return false;
    var t = String(te.type).toLowerCase();
    if (t.indexOf('every') === 0) return true;
    if (t === 'htmx:poll') return true;
    return false;
  }

  function isUserNavigation(detail) {
    var te = detail.requestConfig && detail.requestConfig.triggeringEvent;
    if (!te) return false;
    var t = te.type;
    if (t === 'click' || t === 'submit') return true;
    return false;
  }

  function isLoadNavigation(detail) {
    var te = detail.requestConfig && detail.requestConfig.triggeringEvent;
    if (!te) return false;
    return te.type === 'load' && !isIntervalTrigger(detail);
  }

  function shouldShow(detail) {
    if (!isMainSwapTarget(detail)) return false;
    if (!(isUserNavigation(detail) || isLoadNavigation(detail))) return false;
    var elt = detail.elt;
    if (elt && elt.closest && elt.closest('[data-no-page-loader]')) return false;
    return true;
  }

  var depth = 0;

  function loaderEl() {
    return document.getElementById('htmx-page-loader');
  }

  function mainEl() {
    return document.getElementById('main-content');
  }

  function sync() {
    var on = depth > 0;
    var el = loaderEl();
    if (el) {
      el.classList.toggle('htmx-page-loader--visible', on);
      el.setAttribute('aria-hidden', on ? 'false' : 'true');
    }
    var m = mainEl();
    if (m) m.setAttribute('aria-busy', on ? 'true' : 'false');
  }

  document.addEventListener('htmx:beforeSend', function (evt) {
    if (!shouldShow(evt.detail)) return;
    depth += 1;
    sync();
  });

  document.addEventListener('htmx:afterRequest', function (evt) {
    if (!shouldShow(evt.detail)) return;
    depth = Math.max(0, depth - 1);
    sync();
  });
})();
