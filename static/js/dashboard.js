// Dashboard charts (D-051). The figures come from the page; the same numbers are in its tables.
(function () {
  var el = document.getElementById("dashboard-data");
  if (!el || typeof Chart === "undefined") return;
  var d = JSON.parse(el.textContent);
  var fmt = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
  var money = function (v) { return d.currency + " " + fmt.format(v); };
  var tooltip = { callbacks: { label: function (c) { return c.dataset.label + ": " + money(c.parsed.y); } } };
  var axis = { beginAtZero: true, ticks: { callback: function (v) { return fmt.format(v); } } };

  var trend = document.getElementById("trend-chart");
  if (trend) {
    new Chart(trend, {
      data: {
        labels: d.labels,
        datasets: [
          { type: "bar", label: d.names.expected, data: d.expected, backgroundColor: "#c7d2fe" },
          { type: "bar", label: d.names.collected, data: d.collected, backgroundColor: "#4f46e5" },
          { type: "line", label: d.names.cash, data: d.cash, borderColor: "#059669", backgroundColor: "#059669",
            tension: 0.2, pointRadius: 2 }
        ]
      },
      options: { maintainAspectRatio: false, scales: { y: axis },
                 plugins: { tooltip: tooltip, legend: { position: "bottom", labels: { boxWidth: 12 } } } }
    });
  }

  var aging = document.getElementById("aging-chart");
  if (aging) {
    new Chart(aging, {
      type: "bar",
      data: { labels: d.aging_labels,
              datasets: [{ label: d.names.arrears, data: d.aging,
                           backgroundColor: ["#fcd34d", "#fb923c", "#f87171", "#b91c1c"] }] },
      options: { maintainAspectRatio: false, scales: { y: axis },
                 plugins: { tooltip: tooltip, legend: { display: false } } }
    });
  }
})();
