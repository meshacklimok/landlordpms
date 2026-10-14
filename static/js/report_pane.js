// A report drawn inside the report centre's frame (?pane=1, D-148).
// Links and filters to other reports stay in the frame; anything else (a lease, an invoice, a POST) opens in the whole
// window, because only reports may be framed. The frame tells the centre its height and which report it shows.
(function () {
  "use strict";
  var PREFIX = "/reports/", CENTRE = "/reports/centre/";

  function isReport(url) {
    return url.origin === window.location.origin && url.pathname.indexOf(PREFIX) === 0 && url.pathname.indexOf(CENTRE) !== 0;
  }

  function paneUrl(href) {
    var url = new URL(href, window.location.href);
    if (!isReport(url)) return null;
    url.searchParams.set("pane", "1");
    return url;
  }

  // Chart clicks (charts.js) go through here.
  window.appNavigate = function (href) {
    var url = paneUrl(href);
    if (url) window.location.href = url.href;
    else window.top.location.href = new URL(href, window.location.href).href;
  };

  function fixLink(a) {
    var href = a.getAttribute("href");
    if (!href || href.charAt(0) === "#" || /^(javascript|mailto|tel|sms):/i.test(href)) return;
    if (a.hasAttribute("download")) return;
    var url = paneUrl(a.href);
    if (url) a.href = url.href;
    else if (!a.target) a.target = "_top";
  }

  function fixForm(form) {
    var action = new URL(form.getAttribute("action") || window.location.href, window.location.href);
    if ((form.method || "get").toLowerCase() === "get" && isReport(action)) {
      if (!form.querySelector("input[name=pane]")) {
        var input = document.createElement("input");
        input.type = "hidden"; input.name = "pane"; input.value = "1";
        form.appendChild(input);
      }
    } else if (!form.target) {
      form.target = "_top";
    }
  }

  function tell() {
    if (window.parent === window) return;
    var main = document.querySelector("main") || document.body;
    window.parent.postMessage({
      type: "report-pane",
      // The page's own content, not the frame: scrollHeight never shrinks below the frame's height.
      height: Math.ceil(main.getBoundingClientRect().bottom + window.scrollY + 16),
      path: window.location.pathname,
      search: window.location.search,
      title: document.title
    }, window.location.origin);
  }

  function ready() {
    document.querySelectorAll("a[href]").forEach(fixLink);
    document.querySelectorAll("form").forEach(fixForm);
    // Links added later (tables filled by script) are fixed when clicked.
    document.addEventListener("click", function (event) {
      var a = event.target.closest && event.target.closest("a[href]");
      if (a) fixLink(a);
    }, true);
    document.addEventListener("submit", function (event) { fixForm(event.target); }, true);
    tell();
    if (window.ResizeObserver) new ResizeObserver(tell).observe(document.body);
    window.addEventListener("load", tell);
    window.addEventListener("message", function (event) {
      if (event.origin === window.location.origin && event.source === window.parent && event.data && event.data.type === "report-reader") tell();
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready);
  else ready();
})();
