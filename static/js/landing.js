// Landing page: the sticky trial bar on phones and the rent-delay calculator.
(function () {
  calculator();
  var bar = document.querySelector("[data-land-sticky]"), hero = document.querySelector(".land-cta-row"),
      end = document.querySelector(".land-final");
  if (!bar || !hero || !("IntersectionObserver" in window)) return;
  var heroSeen = true, endSeen = false;
  function update() { bar.hidden = heroSeen || endSeen; }
  new IntersectionObserver(function (e) { heroSeen = e[0].isIntersecting; update(); }).observe(hero);
  if (end) new IntersectionObserver(function (e) { endSeen = e[0].isIntersecting; update(); }).observe(end);
})();

// Rent of late-paying tenants, from the three numbers typed in. Nothing leaves the page.
function calculator() {
  var form = document.querySelector("[data-land-calc]");
  if (!form) return;
  var units = form.querySelector("#calc-units"), rent = form.querySelector("#calc-rent"), late = form.querySelector("#calc-late"),
      out = form.querySelector("[data-calc-out]"), note = form.querySelector("[data-calc-note]");
  function num(el, max) { var n = parseFloat(el.value); return isNaN(n) || n < 0 ? 0 : Math.min(n, max); }
  function show() {
    var u = num(units, 100000), r = num(rent, 10000000), p = num(late, 100);
    out.textContent = "KSh " + Math.round(u * r * p / 100).toLocaleString("en-KE");
    note.textContent = Math.round(u * p / 100) + " of " + u + " tenants pay late. Matching payments to units and reminding by SMS is what brings this down.";
  }
  [units, rent, late].forEach(function (el) { el.addEventListener("input", show); });
  form.addEventListener("submit", function (e) { e.preventDefault(); });
  show();
}
