// Shared expense form (D-197): the percentage boxes show only for "Percentages I type in", and only for the
// properties that are ticked. Without this script every box stays visible, which still works.
(function () {
  var rule = document.getElementById("id_rule");
  if (!rule) return;
  function sync() {
    var byPercent = rule.value === "percent";
    document.querySelectorAll('input[name^="percent_"]').forEach(function (box) {
      var id = box.name.slice("percent_".length);
      var ticked = document.querySelector('input[name="properties"][value="' + id + '"]');
      var row = box.closest(".mb-3");
      if (row) row.hidden = !(byPercent && ticked && ticked.checked);
    });
  }
  rule.addEventListener("change", sync);
  document.querySelectorAll('input[name="properties"]').forEach(function (c) { c.addEventListener("change", sync); });
  sync();
})();
