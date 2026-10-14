// Monthly / Yearly switch above the plan cards (pricing, landing and subscription pages). Without JavaScript the
// switch stays hidden and each card shows its first price, with the other prices listed under it.
(function () {
  "use strict";
  var sw = document.querySelector("[data-plan-switch]");
  var grid = document.querySelector("[data-plan-grid]");
  if (!sw || !grid) return;
  var cards = grid.querySelectorAll(".plan-card");
  var hasYearly = grid.querySelector('[data-interval="YEARLY"]');
  if (!hasYearly) return;

  function pick(interval) {
    cards.forEach(function (card) {
      var shown = card.querySelector('.plan-price[data-interval="' + interval + '"]') ? interval : null;
      var first = card.querySelector(".plan-price");
      card.querySelectorAll("[data-interval]").forEach(function (el) {
        var on = shown ? el.getAttribute("data-interval") === interval : el === first;
        el.hidden = !on;
      });
      var select = card.querySelector('select[name="interval"]');
      if (select && shown) select.value = interval;
    });
    sw.querySelectorAll("[data-pick]").forEach(function (btn) {
      var on = btn.getAttribute("data-pick") === interval;
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      btn.classList.toggle("btn-primary", on);
      btn.classList.toggle("btn-outline-primary", !on);
    });
  }

  sw.hidden = false;
  sw.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-pick]");
    if (btn) pick(btn.getAttribute("data-pick"));
  });
})();
