// Dashboard charts (D-051, D-113). The figures come from the page; the same numbers are in its tables.
(function () {
  var printButton = document.querySelector("[data-print]");
  if (printButton) printButton.addEventListener("click", function () { window.print(); });

  var el = document.getElementById("dashboard-data");
  if (!el || typeof Chart === "undefined") return;
  var d = JSON.parse(el.textContent);
  var fmt = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
  var short = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
  var money = function (v) { return d.currency + " " + fmt.format(v); };
  // Colours come from static/css/app.css (D-111), so the charts follow light and dark mode.
  var c = function (name) { return window.appTheme ? window.appTheme.color(name) : ""; };
  var series = function () { return [c("--chart-1"), c("--chart-2"), c("--chart-3"), c("--chart-4"), c("--chart-5")]; };
  var late = function () { return [c("--chart-late-1"), c("--chart-late-2"), c("--chart-late-3"), c("--chart-late-4")]; };
  var charts = [];

  var moneyTip = { callbacks: { label: function (x) {
    return x.dataset.label + ": " + money(x.parsed.y !== undefined && x.chart.options.indexAxis !== "y" ? x.parsed.y : x.parsed.x);
  } } };
  var pctTip = { callbacks: { label: function (x) { return x.dataset.label + ": " + (x.parsed.y === null ? "—" : x.parsed.y + "%"); } } };
  var moneyAxis = { beginAtZero: true, ticks: { callback: function (v) { return short.format(v); } } };
  var pctAxis = { min: 0, max: 100, ticks: { stepSize: 25, callback: function (v) { return v + "%"; } } };
  // The legend keeps the datasets' own order, not their drawing order.
  var legend = { position: "bottom", labels: { boxWidth: 10, boxHeight: 10, useBorderRadius: true, borderRadius: 2,
                                               sort: function (a, b) { return a.datasetIndex - b.datasetIndex; } } };
  var hover = { mode: "index", intersect: false };

  function theme() {
    Chart.defaults.color = c("--chart-text");
    Chart.defaults.borderColor = c("--chart-grid");
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    Chart.defaults.font.size = 12;
    Chart.defaults.maintainAspectRatio = false;
    Chart.defaults.elements.bar.borderRadius = 4;
    Chart.defaults.elements.line.borderWidth = 2;
    Chart.defaults.elements.point.radius = 0;
    Chart.defaults.elements.point.hoverRadius = 5;
    Chart.defaults.plugins.tooltip.padding = 10;
    Chart.defaults.plugins.tooltip.boxPadding = 4;
    Chart.defaults.scale.grid.drawTicks = false;
  }

  function make(id, config) {
    var canvas = document.getElementById(id);
    if (!canvas) return;
    // Horizontal bar charts grow with their rows, so long property lists stay readable.
    var box = canvas.parentElement;
    if (box.hasAttribute("data-rows") && config.rows) box.style.minHeight = (config.rows * 34 + 48) + "px";
    delete config.rows;
    charts.push(new Chart(canvas, config));
  }

  // A doughnut: colours in fixed order, a 2px surface gap between slices, the share in the tooltip.
  function doughnut(id, labels, values, colors) {
    var total = values.reduce(function (a, b) { return a + b; }, 0);
    make(id, {
      type: "doughnut",
      data: { labels: labels, datasets: [{ data: values, backgroundColor: colors, borderColor: c("--bs-body-bg"),
                                           borderWidth: 2, hoverOffset: 4 }] },
      options: { cutout: "68%", plugins: { legend: { display: false }, tooltip: { callbacks: { label: function (x) {
        var share = total ? Math.round(x.parsed / total * 100) : 0;
        return x.label + ": " + (d.currency && !x.dataset.counts ? money(x.parsed) : fmt.format(x.parsed)) + " (" + share + "%)";
      } } } } }
    });
    return charts[charts.length - 1];
  }

  function draw() {
    theme();
    var s = series();

    make("trend-chart", {
      data: { labels: d.labels, datasets: [
        { type: "bar", label: d.names.expected, data: d.expected, backgroundColor: c("--chart-1-soft"), order: 2 },
        { type: "bar", label: d.names.collected, data: d.collected, backgroundColor: s[0], order: 2 },
        { type: "line", label: d.names.cash, data: d.cash, borderColor: s[2], backgroundColor: s[2], tension: 0.3,
          pointRadius: 3, order: 1 }
      ] },
      options: { interaction: hover, scales: { x: { grid: { display: false } }, y: moneyAxis },
                 datasets: { bar: { categoryPercentage: 0.7, barPercentage: 0.9 } },
                 plugins: { tooltip: moneyTip, legend: legend } }
    });

    make("rate-chart", {
      type: "line",
      data: { labels: d.labels, datasets: [{ label: d.names.rate, data: d.rate, borderColor: s[0], tension: 0.3,
                                             fill: true, backgroundColor: c("--chart-1-soft") + "66", spanGaps: true }] },
      options: { interaction: hover, scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 6 } }, y: pctAxis },
                 plugins: { tooltip: pctTip, legend: { display: false } } }
    });

    if (d.collection && d.collection.length) {
      make("collection-chart", {
        type: "bar", rows: d.collection.length,
        data: { labels: d.collection.map(function (r) { return r[0]; }), datasets: [
          { label: d.names.collected, data: d.collection.map(function (r) { return r[1]; }), backgroundColor: s[0] },
          { label: d.names.outstanding, data: d.collection.map(function (r) { return r[2]; }), backgroundColor: c("--chart-late-1") }
        ] },
        options: { indexAxis: "y", interaction: { mode: "index", axis: "y", intersect: false },
                   scales: { x: Object.assign({ stacked: true }, moneyAxis), y: { stacked: true, grid: { display: false } } },
                   plugins: { tooltip: { callbacks: { label: function (x) { return x.dataset.label + ": " + money(x.parsed.x); },
                     footer: function (items) {
                       var r = d.collection[items[0].dataIndex], whole = r[1] + r[2];
                       return whole ? Math.round(r[1] / whole * 100) + "% " + d.names.collected.toLowerCase() : "";
                     } } }, legend: legend } }
      });
    }

    if (d.methods && d.methods.length) {
      doughnut("methods-chart", d.methods.map(function (r) { return r[0]; }), d.methods.map(function (r) { return r[1]; }),
               d.methods.map(function (_r, i) { return s[i] || c("--chart-neutral"); }));
    }

    if (d.money) {
      make("money-chart", {
        data: { labels: d.money.labels, datasets: [
          { type: "bar", label: d.money.names.income, data: d.money.income, backgroundColor: s[0], order: 2 },
          { type: "bar", label: d.money.names.expenses, data: d.money.expenses, backgroundColor: s[1], order: 2 },
          { type: "line", label: d.money.names.noi, data: d.money.noi, borderColor: s[2], backgroundColor: s[2],
            tension: 0.3, pointRadius: 3, order: 1 }
        ] },
        options: { interaction: hover, scales: { x: { grid: { display: false } },
                                                 y: { ticks: moneyAxis.ticks } },
                   datasets: { bar: { categoryPercentage: 0.7, barPercentage: 0.9 } },
                   plugins: { tooltip: moneyTip, legend: legend } }
      });
      if (d.money.categories.length) {
        // Five colours, then the rest folds into the neutral grey.
        doughnut("categories-chart", d.money.categories.map(function (r) { return r[0]; }),
                 d.money.categories.map(function (r) { return r[1]; }),
                 d.money.categories.map(function (_r, i) { return s[i] || c("--chart-neutral"); }));
      }
      if (d.money.places.length) {
        make("places-chart", {
          type: "bar", rows: d.money.places.length,
          data: { labels: d.money.places.map(function (r) { return r[0]; }), datasets: [{
            label: d.money.names.noi, data: d.money.places.map(function (r) { return r[1]; }),
            backgroundColor: d.money.places.map(function (r) { return r[1] < 0 ? c("--chart-late-2") : s[0]; }) }] },
          options: { indexAxis: "y", scales: { x: { ticks: moneyAxis.ticks }, y: { grid: { display: false } } },
                     plugins: { tooltip: moneyTip, legend: { display: false } } }
        });
      }
    }

    if (d.occupancy) {
      var now = d.occupancy.now;
      var occ = doughnut("occupancy-now-chart", now.map(function (r) { return r[0]; }), now.map(function (r) { return r[1]; }),
                         [s[0], c("--chart-late-1"), s[2], c("--chart-neutral")]);
      if (occ) {
        occ.data.datasets[0].counts = true;
        occ.options.plugins.tooltip.callbacks.label = function (x) {
          var total = now.reduce(function (a, r) { return a + r[1]; }, 0);
          return x.label + ": " + x.parsed + " " + d.names.units + (total ? " (" + Math.round(x.parsed / total * 100) + "%)" : "");
        };
        occ.update();
      }
      make("occupancy-chart", {
        type: "line",
        data: { labels: d.occupancy.labels, datasets: [{ label: d.names.occupancy, data: d.occupancy.rate, borderColor: s[0],
                                                         fill: true, backgroundColor: c("--chart-1-soft") + "66",
                                                         tension: 0.3, spanGaps: true }] },
        options: { interaction: hover, scales: { x: { grid: { display: false } }, y: pctAxis },
                   plugins: { tooltip: pctTip, legend: { display: false } } }
      });
      if (d.occupancy.places.length > 1) {
        make("places-occupancy-chart", {
          type: "bar", rows: d.occupancy.places.length,
          data: { labels: d.occupancy.places.map(function (r) { return r[0]; }), datasets: [
            { label: d.names.occupied, data: d.occupancy.places.map(function (r) { return r[1]; }), backgroundColor: s[0] },
            { label: d.names.vacant, data: d.occupancy.places.map(function (r) { return r[2]; }), backgroundColor: c("--chart-late-1") }
          ] },
          options: { indexAxis: "y", interaction: { mode: "index", axis: "y", intersect: false },
                     scales: { x: { stacked: true, ticks: { precision: 0 } }, y: { stacked: true, grid: { display: false } } },
                     plugins: { legend: legend } }
        });
      }
    }

    make("aging-chart", {
      type: "bar",
      data: { labels: d.aging_labels, datasets: [{ label: d.names.arrears, data: d.aging, backgroundColor: late() }] },
      options: { scales: { x: { grid: { display: false } }, y: moneyAxis },
                 plugins: { tooltip: moneyTip, legend: { display: false } } }
    });

    // Arrears at month end (D-077): stacked by days overdue; a month with no snapshot is left empty.
    if (d.history) {
      var colors = late();
      make("history-chart", {
        type: "bar",
        data: { labels: d.history_labels,
                datasets: d.history.map(function (h, i) { return { label: h.label, data: h.data, backgroundColor: colors[i] }; }) },
        options: { interaction: hover, scales: { x: { stacked: true, grid: { display: false } }, y: Object.assign({ stacked: true }, moneyAxis) },
                   plugins: { tooltip: moneyTip, legend: legend } }
      });
    }

    // Grades run from good to bad: greens, a neutral middle, then the late colours.
    if (d.grades && d.grades.length) {
      var gradeColors = [s[0], c("--chart-1-soft"), c("--chart-neutral"), c("--chart-late-2"), c("--chart-late-4"), c("--chart-3")];
      make("grades-chart", {
        type: "bar",
        data: { labels: d.grades.map(function (g) { return g[0]; }),
                datasets: [{ label: d.names.tenants, data: d.grades.map(function (g) { return g[1]; }), backgroundColor: gradeColors }] },
        options: { scales: { x: { grid: { display: false } }, y: { beginAtZero: true, ticks: { precision: 0 } } },
                   plugins: { legend: { display: false } } }
      });
    }

    // The colour keys beside the doughnut tables follow the slices.
    document.querySelectorAll(".key[data-series]").forEach(function (key) {
      key.style.background = s[Number(key.getAttribute("data-series"))] || c("--chart-neutral");
    });
  }

  draw();
  document.addEventListener("app:theme", function () {
    charts.forEach(function (chart) { chart.destroy(); });
    charts = [];
    draw();
  });
})();
