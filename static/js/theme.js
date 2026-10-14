// Light, dark or System (D-111). Loaded in <head> without defer, so the page is drawn in the right colours from the
// start instead of flashing white. The choice is kept on this device only (localStorage); without it, System is used.
//   data-theme-value="light|dark|auto"  on a button: picks that theme (aria-pressed shows the current one)
// Pages with charts listen for the "app:theme" event on document and redraw with window.appTheme.color("--chart-1").
(function () {
  "use strict";
  var KEY = "theme";
  var root = document.documentElement;
  var dark = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function stored() {
    try { var v = localStorage.getItem(KEY); return v === "light" || v === "dark" ? v : "auto"; } catch (e) { return "auto"; }
  }
  function resolve(choice) { return choice === "auto" ? (dark && dark.matches ? "dark" : "light") : choice; }

  function apply(choice) {
    var mode = resolve(choice);
    var changed = root.getAttribute("data-bs-theme") !== mode;
    root.setAttribute("data-bs-theme", mode);
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", mode === "dark" ? "#171d1a" : "#ffffff");
    document.querySelectorAll("[data-theme-value]").forEach(function (el) {
      el.setAttribute("aria-pressed", String(el.getAttribute("data-theme-value") === choice));
    });
    if (changed) document.dispatchEvent(new CustomEvent("app:theme", { detail: { mode: mode } }));
  }

  window.appTheme = {
    mode: function () { return root.getAttribute("data-bs-theme") || "light"; },
    color: function (name) { return getComputedStyle(root).getPropertyValue(name).trim(); }
  };

  apply(stored());

  if (dark) {
    var follow = function () { if (stored() === "auto") apply("auto"); };
    if (dark.addEventListener) dark.addEventListener("change", follow); else if (dark.addListener) dark.addListener(follow);
  }

  document.addEventListener("click", function (event) {
    var el = event.target.closest("[data-theme-value]");
    if (!el) return;
    var choice = el.getAttribute("data-theme-value");
    try { if (choice === "auto") localStorage.removeItem(KEY); else localStorage.setItem(KEY, choice); } catch (e) { /* private mode */ }
    apply(choice);
  });

  document.addEventListener("DOMContentLoaded", function () { apply(stored()); });

  // Printing uses the light palette, so dark mode does not print a black page.
  var before = null;
  window.addEventListener("beforeprint", function () { before = root.getAttribute("data-bs-theme"); root.setAttribute("data-bs-theme", "light"); });
  window.addEventListener("afterprint", function () { if (before) root.setAttribute("data-bs-theme", before); before = null; });
})();
