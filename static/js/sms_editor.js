// Message boxes for SMS wording: field buttons, a running length and SMS count, and a send button that waits for a
// fresh preview. Driven by data- attributes, since the Content Security Policy runs no inline code (D-107):
//   textarea[data-sms-box]   the message; data-count="#id" shows data-count-format there, with {n} the length and
//                            {parts} the SMS count; data-extra adds characters sent after it; data-trim ignores
//                            spaces at either end
//   [data-insert="name"]     a button that puts {name} into its form's message box at the cursor
//   form[data-stale-button]  "#id" of a send button turned off once anything changes, and data-stale-note
//                            "#id" of the note shown with it
(function () {
  "use strict";

  function parts(n) { return n <= 160 ? 1 : Math.ceil(n / 153); }

  document.querySelectorAll("textarea[data-sms-box]").forEach(function (box) {
    var count = box.dataset.count ? document.querySelector(box.dataset.count) : null;
    if (!count) return;
    var extra = parseInt(box.dataset.extra || "0", 10), format = box.dataset.countFormat || "{n} · {parts}";
    var update = function () {
      var n = (box.hasAttribute("data-trim") ? box.value.trim() : box.value).length + extra;
      count.textContent = format.replace("{n}", n).replace("{parts}", parts(n));
    };
    box.addEventListener("input", update);
    update();
  });

  document.querySelectorAll("[data-insert]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var box = btn.form && btn.form.querySelector("textarea[data-sms-box]");
      if (!box) return;
      var token = "{" + btn.dataset.insert + "}", s = box.selectionStart, e = box.selectionEnd;
      box.value = box.value.slice(0, s) + token + box.value.slice(e);
      box.focus();
      box.selectionStart = box.selectionEnd = s + token.length;
      box.dispatchEvent(new Event("input", { bubbles: true }));
    });
  });

  document.querySelectorAll("form[data-stale-button]").forEach(function (form) {
    var send = document.querySelector(form.dataset.staleButton);
    var note = form.dataset.staleNote ? document.querySelector(form.dataset.staleNote) : null;
    if (!send) return;
    var markStale = function () { send.disabled = true; if (note) note.classList.remove("d-none"); };
    form.addEventListener("input", markStale);
    form.addEventListener("change", markStale);
  });
})();
