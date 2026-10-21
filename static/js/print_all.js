// Print all reports: every report is drawn in its own frame (?pane=1), each frame grows to its report's height
// (report_pane.js tells us), and Print then gives one document with a page break before each report.
(function () {
  "use strict";
  var frames = Array.prototype.slice.call(document.querySelectorAll("[data-print-frame]"));
  var button = document.querySelector("[data-print-all]");
  var label = document.querySelector("[data-print-label]");
  var status = document.querySelector("[data-print-status]");
  if (!frames.length || !button) return;
  var loaded = 0, ready = false;
  var doneText = label.getAttribute("data-done") || "Print / Save as PDF";

  function finish() {
    if (ready) return;
    ready = true;
    button.disabled = false;
    label.textContent = doneText;
    if (status) status.textContent = doneText;
  }

  frames.forEach(function (f) {
    f.addEventListener("load", function () {
      loaded += 1;
      // Charts draw just after load; give them a moment before declaring everything ready.
      if (loaded >= frames.length) window.setTimeout(finish, 1200);
    });
  });
  window.setTimeout(finish, 20000);  // a slow report must not block printing forever

  window.addEventListener("message", function (event) {
    if (event.origin !== window.location.origin || !event.data || event.data.type !== "report-pane") return;
    frames.forEach(function (f) {
      if (f.contentWindow === event.source && event.data.height) f.style.height = event.data.height + "px";
    });
  });

  button.addEventListener("click", function () { window.print(); });
})();
