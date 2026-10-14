// Report centre reader (D-148): the chosen report is drawn in a frame beside the list. Clicking a report, Previous/Next,
// the drop-down or the left/right arrow keys swap the report without leaving the page; the address keeps ?r=<report>
// so Back, Forward, reload and a shared link land on the same report.
(function () {
  "use strict";
  var root = document.querySelector("[data-reader]");
  var data = document.getElementById("reader-reports");
  if (!root || !data) return;

  var reports = JSON.parse(data.textContent);
  var frame = root.querySelector("[data-reader-frame]");
  var wrap = root.querySelector("[data-reader-wrap]");
  var pick = root.querySelector("[data-reader-pick]");
  var title = root.querySelector("[data-reader-title]");
  var group = root.querySelector("[data-reader-group]");
  var count = root.querySelector("[data-reader-count]");
  var fulls = root.querySelectorAll("[data-reader-full]");  // "Full page", and the link shown when the frame fails
  var status = root.querySelector("[data-reader-status]");
  var countText = count ? count.textContent.trim() : "";  // "3 of 18", translated; the numbers are swapped below
  var current = indexOf(new URLSearchParams(window.location.search).get("r"));
  if (current < 0) current = 0;

  function indexOf(key) {
    for (var i = 0; i < reports.length; i++) if (reports[i].key === key) return i;
    return -1;
  }

  function byPath(path) {
    for (var i = 0; i < reports.length; i++) if (reports[i].url === path) return i;
    return -1;
  }

  function reducedMotion() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  // Keeps the chosen report visible in the list's own scroll, without moving the page.
  function revealInNav(a) {
    var nav = a.closest(".reader-nav");
    if (!nav || !nav.offsetParent) return;  // the list is hidden on a phone
    var box = nav.getBoundingClientRect(), item = a.getBoundingClientRect();
    if (item.top < box.top) nav.scrollTop -= box.top - item.top + 8;
    else if (item.bottom > box.bottom) nav.scrollTop += item.bottom - box.bottom + 8;
  }

  function setFull(url) { fulls.forEach(function (a) { a.setAttribute("href", url); }); }

  function href(i) { return "?r=" + encodeURIComponent(reports[i].key); }

  function setStep(a, i) {
    var on = i >= 0 && i < reports.length;
    a.classList.toggle("disabled", !on);
    if (a.classList.contains("reader-next")) a.classList.toggle("invisible", !on);
    a.setAttribute("href", on ? href(i) : "#");
    if (on) { a.removeAttribute("aria-disabled"); a.removeAttribute("tabindex"); }
    else { a.setAttribute("aria-disabled", "true"); a.setAttribute("tabindex", "-1"); }
    var name = a.querySelector("[data-reader-name]");
    if (name) name.textContent = on ? reports[i].label : "";
  }

  // Draws the list, heading and buttons for report i. load=false when the frame is already showing it.
  function show(i, opts) {
    opts = opts || {};
    if (i < 0 || i >= reports.length) return;
    var r = reports[i];
    current = i;
    root.querySelectorAll("a[data-report]").forEach(function (a) {
      var on = a.getAttribute("data-report") === r.key;
      a.classList.toggle("active", on);
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
      if (on) revealInNav(a);
    });
    if (pick) pick.value = r.key;
    if (title) title.textContent = r.label;
    if (group) group.textContent = r.group;
    if (count) count.textContent = countText.replace(/\d+/, String(i + 1)).replace(/(\d+)(?!.*\d)/, String(reports.length));
    setFull(opts.full || r.url);
    root.querySelectorAll("[data-reader-step]").forEach(function (a) {
      setStep(a, i + parseInt(a.getAttribute("data-reader-step"), 10));
    });
    frame.title = r.label;
    document.title = document.title.replace(/^[^·]*·\s*/, r.label + " · ");
    if (opts.load !== false) {
      // A new report starts at its top: the page goes back up to the report's title.
      window.scrollTo({ top: 0, behavior: reducedMotion() ? "auto" : "smooth" });
      wrap.classList.remove("is-failed");
      wrap.classList.add("is-loading");
      frame.src = r.url + "?pane=1";
      if (status) status.textContent = r.label;
    }
    if (opts.push !== false) history.pushState({ r: r.key }, "", href(i));
  }

  function go(i) {
    if (i === current || i < 0 || i >= reports.length) return;
    show(i);
  }

  function plainClick(event) {
    return event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
  }

  root.addEventListener("click", function (event) {
    if (!plainClick(event)) return;
    var a = event.target.closest("a[data-report], a[data-reader-step]");
    if (!a) return;
    event.preventDefault();
    if (a.hasAttribute("data-report")) go(indexOf(a.getAttribute("data-report")));
    else go(current + parseInt(a.getAttribute("data-reader-step"), 10));
  });

  if (pick) pick.addEventListener("change", function () { go(indexOf(pick.value)); });

  window.addEventListener("popstate", function () {
    var i = indexOf(new URLSearchParams(window.location.search).get("r"));
    show(i < 0 ? 0 : i, { push: false });
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    if (event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;
    var el = document.activeElement;
    if (el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName))) return;
    event.preventDefault();
    go(current + (event.key === "ArrowLeft" ? -1 : 1));
  });

  // The framed report tells us its height, and which report it shows after a link inside it was followed.
  window.addEventListener("message", function (event) {
    if (event.origin !== window.location.origin || event.source !== frame.contentWindow) return;
    var msg = event.data;
    if (!msg || msg.type !== "report-pane") return;
    wrap.classList.remove("is-loading");
    if (msg.height > 0) frame.style.height = msg.height + "px";
    var params = new URLSearchParams(msg.search || "");
    params.delete("pane");
    var query = params.toString();
    var i = byPath(msg.path);
    if (i >= 0 && i !== current) {
      show(i, { load: false, full: msg.path + (query ? "?" + query : "") });
      window.scrollTo({ top: 0, behavior: reducedMotion() ? "auto" : "smooth" });
    } else {
      setFull(msg.path + (query ? "?" + query : ""));
    }
  });

  // A report that could not be drawn in the frame (the login ran out, the server failed) shows the browser's own
  // "refused" page or a page without our pane marker. Say so, and offer the report on its own page instead.
  function checkFrame() {
    wrap.classList.remove("is-loading");
    var ok = false;
    try {
      var doc = frame.contentDocument;
      ok = !!(doc && doc.documentElement.classList.contains("is-pane"));
    } catch (e) { ok = false; }
    wrap.classList.toggle("is-failed", !ok);
    if (!ok && status) status.textContent = failText();
  }
  frame.addEventListener("load", checkFrame);
  window.addEventListener("load", checkFrame);  // the first report may have loaded before this script ran

  function failText() {
    var box = root.querySelector("[data-reader-failed]");
    return box ? box.textContent.trim() : "";
  }

  // The frame follows the page's light/dark choice when it reloads.
  window.addEventListener("app:theme", function () {
    try { frame.contentWindow.location.reload(); } catch (e) { /* not ours */ }
  });

  // The first report may have loaded before this script ran: ask it to say its height again.
  try { frame.contentWindow.postMessage({ type: "report-reader" }, window.location.origin); } catch (e) { /* not loaded */ }
  history.replaceState({ r: reports[current].key }, "", href(current));
})();
