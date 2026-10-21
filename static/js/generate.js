(function () {
  var f = document.getElementById("generate-form");
  if (!f) return;
  function sync() {
    var v = (f.querySelector("input[name=scope]:checked") || {}).value;
    f.querySelectorAll("[data-scope]").forEach(function (el) { el.hidden = el.dataset.scope !== v; });
  }
  f.addEventListener("change", sync);
  sync();
})();
