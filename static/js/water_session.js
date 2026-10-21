// Water readings (D-172): the chips above the units table hide the rows that are not in the chosen state.
// Without JavaScript the chips stay hidden and every unit shows.
(function () {
  "use strict";
  var group = document.querySelector("[data-water-filter]");
  if (!group) return;
  var wrap = group.closest("[data-filter-wrap]");
  var rows = document.querySelectorAll(".water-table tbody tr[data-status]");
  if (wrap) wrap.hidden = false;
  group.addEventListener("click", function (event) {
    var chip = event.target.closest("button[data-filter]");
    if (!chip) return;
    var want = chip.getAttribute("data-filter");
    group.querySelectorAll("button").forEach(function (b) { b.setAttribute("aria-pressed", b === chip ? "true" : "false"); });
    rows.forEach(function (row) { row.hidden = want !== "" && row.getAttribute("data-status") !== want; });
  });
})();
