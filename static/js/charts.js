// Charts described by the page (D-114). Each <canvas data-chart="ID"> draws the JSON in <script id="ID"> (Django's
// json_script), so a page adds a chart without new script. The same figures are always in a table on the page.
//
//   type      "bar" | "line" | "doughnut"
//   labels    the categories (months, days, properties...)
//   series    [{label, data, color, type?, colors?, negative?}]
//             color: a token from static/css/app.css without "--chart-", e.g. "1", "1-soft", "late-2", "neutral"
//             colors: one token per point (doughnuts, or bars coloured by category)
//             type: "line" on a bar chart draws that series as a line over the bars
//             negative: the token for bars below zero
//   axis      "money" (default) | "count" | "pct"
//   stacked   bars stacked on each other
//   share     stacked to 100%: each category's bars as shares of its total; the tooltip keeps the real figure
//   horizontal  bars run across, one row per label (the box grows with the rows)
//   currency  shown before money in tooltips
//   links     one address per label; clicking a bar or slice opens it (the page's own filters ride in the address)
//   totalLabel  when given, the tooltip adds the total of the bars under it
(function () {
  if (typeof Chart === "undefined") return;
  var fmt = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
  var short = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
  var css = function (name) {
    return window.appTheme ? window.appTheme.color(name) : getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  };
  var token = function (name) { return css("--chart-" + (name || "neutral")) || css("--chart-neutral"); };
  var charts = [];

  function theme() {
    Chart.defaults.color = token("text");
    Chart.defaults.borderColor = token("grid");
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    Chart.defaults.font.size = 12;
    Chart.defaults.maintainAspectRatio = false;
    Chart.defaults.elements.bar.borderRadius = 4;
    Chart.defaults.elements.line.borderWidth = 2;
    Chart.defaults.elements.point.radius = 0;
    Chart.defaults.elements.point.hoverRadius = 5;
    // Tooltips look like the app's own cards, with a square colour key per series.
    var tip = Chart.defaults.plugins.tooltip;
    tip.padding = 10;
    tip.boxPadding = 5;
    tip.cornerRadius = 8;
    tip.usePointStyle = true;
    tip.backgroundColor = css("--bs-body-bg");
    tip.titleColor = css("--bs-emphasis-color");
    tip.bodyColor = css("--bs-body-color");
    tip.footerColor = css("--bs-secondary-color");
    tip.borderColor = css("--bs-border-color");
    tip.borderWidth = 1;
    tip.titleFont = { weight: "600" };
    tip.footerFont = { weight: "600" };
    Chart.defaults.scale.grid.drawTicks = false;
    Chart.defaults.scale.border.display = false;
    Chart.defaults.elements.point.borderWidth = 2;
    Chart.defaults.elements.point.hoverBorderWidth = 2;
    Chart.defaults.animation.duration = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 400;
  }

  function sum(values) { return values.reduce(function (a, b) { return a + (b || 0); }, 0); }

  function build(spec) {
    var axis = spec.axis || "money";
    var value = function (v) {
      if (v === null || v === undefined) return "—";
      if (axis === "pct") return v + "%";
      if (axis === "count") return fmt.format(v);
      return (spec.currency ? spec.currency + " " : "") + fmt.format(v);
    };
    var series = spec.series || [];

    if (spec.type === "doughnut") {
      var s = series[0], total = sum(s.data);
      return {
        type: "doughnut",
        data: { labels: spec.labels, datasets: [{ label: s.label, data: s.data, backgroundColor: s.colors.map(token),
                                                  borderColor: css("--bs-body-bg"), borderWidth: 2, hoverOffset: 4 }] },
        options: { cutout: "68%", plugins: { legend: { display: false }, tooltip: { callbacks: { label: function (x) {
          return x.label + ": " + value(x.parsed) + (total ? " (" + Math.round(x.parsed / total * 100) + "%)" : "");
        } } } } }
      };
    }

    // A 100% stack draws shares; the real figures stay on the dataset for the tooltip.
    var totals = spec.share ? spec.labels.map(function (_l, i) {
      return sum(series.map(function (s) { return s.data[i]; }));
    }) : null;
    var datasets = series.map(function (s) {
      var color = token(s.color);
      var data = spec.share ? s.data.map(function (v, i) { return totals[i] ? Math.round(v / totals[i] * 1000) / 10 : 0; })
                            : s.data;
      var set = { label: s.label, data: data, raw: s.data, backgroundColor: color, borderColor: color,
                  order: s.type === "line" ? 1 : 2, pointBackgroundColor: color, pointBorderColor: css("--bs-body-bg") };
      // Stacked and side-by-side fills are parted by a 2px gap in the surface colour, not by a darker edge.
      if (spec.type !== "line" && s.type !== "line") { set.borderColor = css("--bs-body-bg"); set.borderWidth = 2; set.borderSkipped = false; }
      if (s.colors) set.backgroundColor = s.colors.map(token);
      if (s.negative) set.backgroundColor = s.data.map(function (v) { return v < 0 ? token(s.negative) : color; });
      if (s.type === "line" || spec.type === "line") {
        set.type = "line";
        set.tension = 0.3;
        set.borderColor = color;
        set.borderWidth = 2;
        set.spanGaps = true;
        set.pointRadius = spec.labels.length > 24 ? 0 : 3;
        if (spec.type === "line" && series.length === 1) { set.fill = true; set.backgroundColor = token("1") + "22"; }
      }
      if (spec.stacked || spec.share) set.stack = "all";
      return set;
    });

    var across = !!spec.horizontal;
    var valueAxis = { beginAtZero: true, grid: { color: token("grid") }, border: { dash: [3, 3] }, ticks: { callback: function (v) {
      return spec.share || axis === "pct" ? v + "%" : axis === "count" ? fmt.format(v) : short.format(v);
    } } };
    if (spec.share || axis === "pct") { valueAxis.min = 0; valueAxis.max = 100; valueAxis.ticks.stepSize = 25; }
    if (axis === "count") valueAxis.ticks.precision = 0;
    if (spec.stacked || spec.share) valueAxis.stacked = true;
    var categoryAxis = { grid: { display: false }, stacked: !!(spec.stacked || spec.share),
                         ticks: { autoSkip: true, maxRotation: 0, maxTicksLimit: across ? undefined : 12 } };

    return {
      type: spec.type === "line" ? "line" : "bar",
      rows: across ? spec.labels.length : 0,
      data: { labels: spec.labels, datasets: datasets },
      options: {
        indexAxis: across ? "y" : "x",
        interaction: { mode: "index", axis: across ? "y" : "x", intersect: false },
        datasets: { bar: { categoryPercentage: 0.72, barPercentage: 0.9 } },
        scales: across ? { x: valueAxis, y: categoryAxis } : { x: categoryAxis, y: valueAxis },
        plugins: {
          legend: { display: spec.legend !== undefined ? spec.legend : datasets.length > 1, position: "bottom",
                    labels: { boxWidth: 10, boxHeight: 10, useBorderRadius: true, borderRadius: 2, padding: 14,
                              sort: function (a, b) { return a.datasetIndex - b.datasetIndex; } } },
          tooltip: { callbacks: {
            labelColor: function (x) {
              var bg = x.dataset.backgroundColor;
              var c = Array.isArray(bg) ? bg[x.dataIndex] : bg;
              return { borderColor: c, backgroundColor: c, borderRadius: 2 };
            },
            label: function (x) {
              var raw = x.dataset.raw[x.dataIndex];
              var shown = value(raw);
              if (spec.share) shown += " (" + x.formattedValue + "%)";
              return x.dataset.label + ": " + shown;
            },
            footer: spec.totalLabel ? function (items) {
              if (items.length < 2) return "";
              var i = items[0].dataIndex;
              var whole = sum(series.filter(function (s) { return s.type !== "line"; }).map(function (s) { return s.data[i]; }));
              return spec.totalLabel + ": " + value(whole);
            } : undefined
          } }
        }
      }
    };
  }

  function draw() {
    theme();
    // Only canvases not drawn yet: the console loads further topics below the page (D-178) and asks for a draw again.
    document.querySelectorAll("canvas[data-chart]:not([data-drawn])").forEach(function (canvas) {
      canvas.setAttribute("data-drawn", "1");
      var source = document.getElementById(canvas.getAttribute("data-chart"));
      if (!source) return;
      var spec = JSON.parse(source.textContent);
      var config = build(spec);
      if (spec.links && spec.links.length) {
        config.options.onClick = function (_e, hits) {
          if (hits.length && spec.links[hits[0].index]) (window.appNavigate || function (u) { window.location.href = u; })(spec.links[hits[0].index]);
        };
        config.options.onHover = function (e, hits) {
          e.native.target.style.cursor = hits.length && spec.links[hits[0].index] ? "pointer" : "default";
        };
        if (config.options.interaction === undefined) config.options.interaction = {};
        config.options.interaction.intersect = true;
        config.options.interaction.mode = "nearest";
      }
      // Bars that run across grow with their rows, so a long list of properties stays readable.
      if (config.rows) canvas.parentElement.style.minHeight = (config.rows * 32 + 56) + "px";
      delete config.rows;
      charts.push(new Chart(canvas, config));
    });
    // Colour keys in the tables beside the charts: <span class="key" data-color="late-2">.
    document.querySelectorAll(".key[data-color]").forEach(function (key) {
      key.style.background = token(key.getAttribute("data-color"));
    });
  }

  draw();
  window.drawCharts = draw;
  document.addEventListener("app:theme", function () {
    charts.forEach(function (chart) { chart.destroy(); });
    charts = [];
    document.querySelectorAll("canvas[data-drawn]").forEach(function (c) { c.removeAttribute("data-drawn"); });
    draw();
  });
})();
