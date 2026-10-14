// Subscription page: while an M-Pesa prompt is waiting, ask the server every few seconds and show
// "Payment successful" (or why it failed) as soon as it is known. Without this the Refresh button still works.
(function () {
  "use strict";

  var box = document.querySelector("[data-pay-status]");
  if (!box) return;

  var title = box.querySelector("[data-pay-title]");
  var text = box.querySelector("[data-pay-text]");
  var spinner = box.querySelector("[data-pay-spinner]");
  var refresh = box.querySelector("[data-reload]");
  var url = box.dataset.payStatus;
  var started = Date.now();
  var GIVE_UP_MS = 3 * 60 * 1000;

  function finish(kind, heading, body) {
    box.className = "pay-notice pay-notice-" + kind;
    box.setAttribute("role", kind === "ok" ? "status" : "alert");
    if (spinner) {
      var icon = document.createElement("i");
      icon.className = "bi " + (kind === "ok" ? "bi-check-circle-fill" : "bi-exclamation-circle");
      icon.setAttribute("aria-hidden", "true");
      spinner.replaceWith(icon);
    }
    title.textContent = heading;
    text.textContent = body;
    if (refresh) refresh.hidden = true;
  }

  function check() {
    fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (data) {
        if (data.status === "PAID") {
          finish("ok", box.dataset.okTitle || "Payment successful", box.dataset.okText || "Thank you. Loading your receipt...");
          setTimeout(function () { window.location.reload(); }, 2000);
        } else if (data.status === "FAILED") {
          finish("fail", box.dataset.failTitle || "Payment was not completed", data.result || "");
        } else {
          wait();
        }
      })
      .catch(wait);
  }

  function wait() {
    if (Date.now() - started > GIVE_UP_MS) {
      if (spinner) spinner.hidden = true;
      return;
    }
    setTimeout(check, 3000);
  }

  wait();
})();
