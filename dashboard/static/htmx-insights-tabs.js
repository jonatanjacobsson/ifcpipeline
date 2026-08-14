/**
 * Revit analytics portfolio insights: tab buttons + pane visibility (HTMX fragments).
 */
(function () {
  function switchInsightsTab(root, tab) {
    root.querySelectorAll('.ra-insights-tab-btn').forEach(function (b) {
      b.classList.toggle('active', b.getAttribute('data-ra-insights-tab') === tab);
    });
    root.querySelectorAll('.ra-insights-pane').forEach(function (p) {
      var show = p.getAttribute('data-ra-insights-pane') === tab;
      p.classList.toggle('ra-insights-pane--active', show);
    });
  }

  document.body.addEventListener('click', function (e) {
    var btn = e.target.closest('.ra-insights-tab-btn');
    if (!btn || !btn.closest('.ra-css-insights')) return;
    e.preventDefault();
    var tab = btn.getAttribute('data-ra-insights-tab');
    if (!tab) return;
    switchInsightsTab(btn.closest('.ra-css-insights'), tab);
  });
})();
