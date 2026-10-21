/* The one-meter reading page a QR code opens (D-157), used standing at the meter on a phone.
   Shows the use since the last reading as the number is typed, warns when it is lower than last time,
   names the photo taken, and stops a second tap on Save from sending the reading twice. The server checks it all again. */
(function () {
  "use strict";
  var form = document.querySelector("form[data-quick-read]");
  if (!form) return;
  var t = form.dataset;
  var input = form.querySelector("[name=value]");
  var out = form.querySelector("[data-quick-used]");
  var replaced = form.querySelector("[name=replaced]");
  var NUMBER = /^\d+(\.\d+)?$/;

  function num(text) {
    text = String(text == null ? "" : text).replace(/,/g, "").trim();
    return NUMBER.test(text) ? parseFloat(text) : NaN;
  }

  function update() {
    var prev = num(t.prev), now = num(input.value);
    var text = "", bad = false;
    if (isFinite(prev) && isFinite(now) && !(replaced && replaced.checked)) {
      var used = Math.round((now - prev) * 1000) / 1000;
      if (used < 0) { text = t.lower; bad = true; }
      else text = t.used.replace("{n}", String(used)).replace("{unit}", t.unit);
    }
    out.textContent = text;
    out.classList.toggle("text-danger", bad);
    out.classList.toggle("fw-semibold", bad);
  }
  input.addEventListener("input", update);
  if (replaced) replaced.addEventListener("change", update);
  update();

  var photo = form.querySelector("[data-photo-input]");
  var name = form.querySelector("[data-photo-name]");
  if (photo && name) photo.addEventListener("change", function () {
    name.textContent = photo.files.length ? photo.files[0].name : name.dataset.empty;
  });

  form.addEventListener("submit", function () {
    var save = form.querySelector("button:not([type=button])");
    if (save) setTimeout(function () { save.disabled = true; }, 0);
  });
})();
