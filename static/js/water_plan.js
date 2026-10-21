// Water settings: the flat amount boxes show only when the plan is "a flat amount every month".
(function () {
  var form = document.querySelector("[data-water-plan]");
  if (!form) return;
  var flat = form.querySelector("[data-water-flat]");
  var radios = form.querySelectorAll("input[name='method']");
  function apply() {
    var chosen = form.querySelector("input[name='method']:checked");
    if (flat) flat.hidden = !chosen || chosen.value !== "FLAT";
  }
  radios.forEach(function (r) { r.addEventListener("change", apply); });
  apply();
})();
