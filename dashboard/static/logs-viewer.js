/* Live log viewer: EventSource -> virtualised-ish append with a capped buffer.
 *
 * Kept deliberately small and dependency-free, matching htmx-network-share.js. The
 * server does the merging across containers; this only renders and filters. */
(function () {
  "use strict";

  const MAX_LINES = 5000; // ring buffer — a busy queue would otherwise pin the tab
  const COLORS = 8; // container colour classes defined in style.css

  function init() {
    const root = document.getElementById("logs-root");
    if (!root || root.dataset.wired === "1") return;
    root.dataset.wired = "1";

    const out = document.getElementById("log-lines");
    const statusEl = document.getElementById("log-status");
    const followBtn = document.getElementById("log-follow");
    const clearBtn = document.getElementById("log-clear");
    const wrapBtn = document.getElementById("log-wrap");
    const filterInput = document.getElementById("log-filter");
    const streamSel = document.getElementById("log-stream-filter");
    const countEl = document.getElementById("log-count");
    const legend = document.getElementById("log-legend");

    const url = root.dataset.streamUrl;
    let follow = true;
    let filter = "";
    let streamFilter = "all";
    let total = 0;
    let shown = 0;
    let paused = false;
    const colorOf = new Map();

    function containerClass(name) {
      if (!colorOf.has(name)) colorOf.set(name, "lc-" + (colorOf.size % COLORS));
      return colorOf.get(name);
    }

    function setStatus(state, text) {
      statusEl.className = "log-status log-status--" + state;
      statusEl.textContent = text;
    }

    function matches(row) {
      if (streamFilter !== "all" && row.dataset.stream !== streamFilter) return false;
      if (!filter) return true;
      return row.dataset.search.indexOf(filter) !== -1;
    }

    function renderCount() {
      countEl.textContent =
        filter || streamFilter !== "all"
          ? shown + " / " + total + " lines"
          : total + " lines";
    }

    function applyFilter() {
      shown = 0;
      for (const row of out.children) {
        const ok = matches(row);
        row.hidden = !ok;
        if (ok) shown++;
      }
      renderCount();
    }

    function addLine(d) {
      const row = document.createElement("div");
      row.className = "log-line";
      row.dataset.stream = d.stream || "stdout";
      const msg = d.message || "";
      row.dataset.search = ((d.container || "") + " " + msg).toLowerCase();

      if (d.ts) {
        const t = document.createElement("span");
        t.className = "log-ts";
        // 2026-08-14T09:22:22.635860188Z -> 09:22:22
        t.textContent = (d.ts.split("T")[1] || "").slice(0, 8);
        row.appendChild(t);
      }
      if (d.container) {
        const c = document.createElement("span");
        c.className = "log-container " + containerClass(d.container);
        // Replica suffix is what distinguishes merged streams; keep it.
        c.textContent = d.container.replace(/^ifcpipeline-/, "");
        row.appendChild(c);
      }
      const m = document.createElement("span");
      m.className = "log-msg" + (d.stream === "stderr" ? " log-msg--err" : "");
      m.textContent = msg;
      row.appendChild(m);

      const visible = matches(row);
      row.hidden = !visible;
      out.appendChild(row);
      total++;
      if (visible) shown++;

      while (out.children.length > MAX_LINES) {
        if (!out.firstChild.hidden) shown--;
        total--;
        out.removeChild(out.firstChild);
      }
      renderCount();
      if (follow && !paused) out.parentElement.scrollTop = out.parentElement.scrollHeight;
    }

    function renderLegend(containers) {
      if (!legend) return;
      legend.innerHTML = "";
      containers.forEach(function (c) {
        const chip = document.createElement("span");
        chip.className = "log-legend-chip " + containerClass(c.name);
        chip.textContent = c.name.replace(/^ifcpipeline-/, "");
        legend.appendChild(chip);
      });
    }

    setStatus("connecting", "connecting…");
    const es = new EventSource(url);

    es.addEventListener("meta", function (e) {
      const d = JSON.parse(e.data);
      renderLegend(d.containers || []);
      setStatus("live", (d.containers || []).length + " container(s) · live");
    });

    es.addEventListener("log", function (e) {
      addLine(JSON.parse(e.data));
    });

    es.addEventListener("error", function (e) {
      if (e.data) {
        const d = JSON.parse(e.data);
        addLine({ container: d.container, message: "[" + d.message + "]", stream: "stderr" });
      }
    });

    es.addEventListener("end", function () {
      setStatus("done", "stream ended");
      es.close();
    });

    es.onerror = function () {
      if (es.readyState === EventSource.CLOSED) setStatus("down", "disconnected");
      else setStatus("connecting", "reconnecting…");
    };

    followBtn.addEventListener("click", function () {
      follow = !follow;
      followBtn.classList.toggle("btn-primary", follow);
      followBtn.classList.toggle("btn-ghost", !follow);
      followBtn.textContent = follow ? "Following" : "Follow";
      if (follow) out.parentElement.scrollTop = out.parentElement.scrollHeight;
    });

    // Scrolling up detaches follow, like a terminal pager.
    out.parentElement.addEventListener("scroll", function () {
      const el = out.parentElement;
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
      paused = !atBottom;
      if (!atBottom && follow) {
        follow = false;
        followBtn.classList.remove("btn-primary");
        followBtn.classList.add("btn-ghost");
        followBtn.textContent = "Follow";
      }
    });

    clearBtn.addEventListener("click", function () {
      out.innerHTML = "";
      total = 0;
      shown = 0;
      renderCount();
    });

    wrapBtn.addEventListener("click", function () {
      const on = out.classList.toggle("log-lines--wrap");
      wrapBtn.classList.toggle("btn-primary", on);
      wrapBtn.classList.toggle("btn-ghost", !on);
    });

    let t = null;
    filterInput.addEventListener("input", function () {
      clearTimeout(t);
      t = setTimeout(function () {
        filter = filterInput.value.trim().toLowerCase();
        applyFilter();
      }, 120);
    });

    streamSel.addEventListener("change", function () {
      streamFilter = streamSel.value;
      applyFilter();
    });

    window.addEventListener("beforeunload", function () {
      es.close();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
