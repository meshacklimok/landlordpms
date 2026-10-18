/* The reading sheet (D-143). One row per meter: type the reading now or the units used and the other fills in,
   with the amount it would bill. The figures here are a preview; the server works them out again on save.
   Enter goes to the next row not read yet. A reading far above the usual use, or with an extra digit, is
   questioned as it is typed (the server flags it again on save). Pasted lines only fill the rows in. */
(function () {
  "use strict";
  var form = document.querySelector("form[data-reading-sheet]");
  if (!form) return;
  var t = form.dataset;
  var lang = document.documentElement.lang || "en";
  var money = new Intl.NumberFormat(lang, { style: "currency", currency: "KES", minimumFractionDigits: 0,
                                             maximumFractionDigits: 2 });
  var NUMBER = /^\d+(\.\d+)?$/;
  // The same rule as meters/services.py flags_for: over HIGH_TIMES the usual use, and at least HIGH_MIN over it.
  var HIGH_TIMES = 3, HIGH_MIN = { WATER: 5, ELECTRICITY: 50 };
  var DAY = 86400000;

  function num(text) {
    text = String(text == null ? "" : text).replace(/,/g, "").trim();
    return NUMBER.test(text) ? parseFloat(text) : NaN;
  }
  function round3(x) { return Math.round(x * 1000) / 1000; }
  function show(x) { return String(round3(x)); }
  function rows() { return Array.prototype.slice.call(form.querySelectorAll("tbody[data-meter]")); }
  function el(body, name) { return body.querySelector("[data-sheet-" + name + "]"); }

  /* The bill divided over the rooms the meter serves, as meters/split.py does for rooms let today: by weight or
     equally, among the rooms let (or over all of them, the landlord paying for the empty ones), with the minimum
     charge per room let. data-rooms is "weight:let,..." in room order. */
  function divide(d, total, minimum) {
    var rooms = (d.rooms || "1:1").split(",").map(function (s) {
      var p = s.split(":");
      return { w: d.split === "WEIGHTED" ? (num(p[0]) || 1) : 1, let: p[1] === "1" };
    });
    var overAll = d.vacant === "LANDLORD" && rooms.length > 1;
    var whole = rooms.reduce(function (a, r) { return a + (r.let || overAll ? r.w : 0); }, 0);
    var out = { amount: 0, atMinimum: false, anyLet: false, parts: [] };
    rooms.forEach(function (r) {
      var a = 0;
      if (r.let && whole > 0) {
        a = total * r.w / whole;
        if (minimum > 0 && a < minimum) { a = minimum; out.atMinimum = true; }
        out.anyLet = true;
      }
      out.parts.push(a);
      out.amount += a;
    });
    return out;
  }

  /* Fills the other box and the amount for one row. `from` is "value" or "units": the box just typed in. */
  function compute(body, from) {
    var d = body.dataset;
    var value = el(body, "value"), units = el(body, "units"), byUnits = el(body, "byunits");
    var replaced = el(body, "replaced");
    var prev = num(d.prev);
    var isNew = replaced && replaced.checked;
    units.disabled = !isFinite(prev) || isNew;
    if (from === "units") byUnits.value = units.value.trim() ? "1" : "";
    if (from === "value" || units.disabled) byUnits.value = "";
    var reading, used;
    if (byUnits.value === "1") {
      used = num(units.value);
      reading = isFinite(used) ? prev + used : NaN;
      value.value = isFinite(reading) ? show(reading) : "";
    } else {
      reading = num(value.value);
      used = isFinite(reading) && isFinite(prev) && !isNew ? round3(reading - prev) : NaN;
      if (!units.disabled) units.value = isFinite(used) && used >= 0 ? show(used) : "";
      if (units.disabled) units.value = "";
    }
    var hint = "", bad = false, warn = false, amount = NaN, atMinimum = false, parts = [];
    var daily = num(d.usualDaily);
    if (value.value.trim() && !isFinite(prev)) hint = t.noPrevious;
    else if (isFinite(used) && used < 0) { hint = t.lower; bad = true; }
    else if (isFinite(used) && used > 0 && !d.prepaid) {
      var shared = (d.rooms || "").indexOf(",") >= 0;
      if (d.landlord) hint = t.landlord;
      else if (shared && d.split === "USAGE") hint = t.ownMeters;
      else {
        var r = divide(d, used * num(d.rate), num(d.minimum) || 0);
        amount = r.amount; atMinimum = r.atMinimum; parts = r.parts;
        if (!r.anyLet) hint = t.vacantRoom;
      }
    }
    if (!hint && isFinite(daily) && daily > 0 && !d.prepaid) {
      var readOn = form.querySelector("[name=read_on]");
      var days = d.prevOn && readOn && readOn.value ? Math.round((new Date(readOn.value) - new Date(d.prevOn)) / DAY) : NaN;
      if (isFinite(used) && used > 0 && days > 0 && !isNew) {
        var expected = daily * days;
        if (used > expected * HIGH_TIMES && used - expected >= (HIGH_MIN[d.utility] || 0)) {
          hint = t.high.replace("{n}", show(Math.round(expected * 10) / 10) + " " + d.unit);
          warn = true;
        } else if (isFinite(prev) && prev >= 10 && String(Math.floor(reading)).length > String(Math.floor(prev)).length
                   && used > prev / 2) {
          hint = t.digit; warn = true;
        }
      } else if (!value.value.trim()) {
        hint = t.usual.replace("{n}", show(Math.round(daily * 30 * 10) / 10) + " " + d.unit);
      }
    }
    body.querySelectorAll("[data-share-amount]").forEach(function (span, i) {
      span.textContent = i < parts.length ? "· " + money.format(parts[i]) : "";
    });
    body.classList.toggle("sheet-bad", bad);
    body.classList.toggle("sheet-read", !!value.value.trim());
    value.classList.toggle("is-invalid", bad || body.classList.contains("sheet-error"));
    var text = isFinite(amount) ? money.format(amount) : "";
    var cell = el(body, "amount");
    if (!d.prepaid) cell.textContent = text;
    el(body, "amount-sm").textContent = text;
    var hintEl = el(body, "hint");
    hintEl.textContent = hint || (atMinimum ? "(" + t.minimum + ")" : "");
    hintEl.className = bad ? "text-danger" : warn ? "text-warning-emphasis fw-semibold" : "text-muted";
    body.classList.toggle("sheet-warn", warn);
    return { read: !!value.value.trim(), amount: isFinite(amount) ? amount : 0 };
  }

  var total = form.querySelector("[data-sheet-total]");
  function totals() {
    var read = 0, amount = 0, all = rows();
    all.forEach(function (body) {
      var r = compute(body);
      if (r.read) read += 1;
      amount += r.amount;
    });
    if (total) {
      total.textContent = total.dataset.text.replace("{read}", read).replace("{all}", all.length)
        .replace("{amount}", money.format(amount));
    }
    filter();
  }

  // ---- Filters: water or electricity, and rows not read yet -------------------------------------------------
  var unread = form.querySelector("[data-sheet-unread]");
  function utility() {
    var on = form.querySelector("[data-sheet-utility]:checked");
    return on ? on.value : "";
  }
  function filter() {
    var u = utility(), onlyUnread = unread && unread.checked;
    rows().forEach(function (body) {
      var focused = body.contains(document.activeElement);
      body.hidden = (u && body.dataset.utility !== u) || (onlyUnread && (body.classList.contains("sheet-read") || body.dataset.done) && !focused);
    });
  }
  form.querySelectorAll("[data-sheet-utility]").forEach(function (r) { r.addEventListener("change", filter); });
  if (unread) unread.addEventListener("change", filter);

  // ---- Typing ---------------------------------------------------------------------------------------------------
  form.addEventListener("input", function (event) {
    var body = event.target.closest("tbody[data-meter]");
    if (!body) return;
    body.classList.remove("sheet-error");
    if (event.target.matches("[data-sheet-value]")) compute(body, "value");
    else if (event.target.matches("[data-sheet-units]")) compute(body, "units");
    totals();
  });
  form.addEventListener("change", function (event) {
    if (event.target.matches("[data-sheet-replaced], [name=read_on]")) totals();
  });
  // Enter goes to the same box on the next row shown, instead of sending the form half filled.
  form.addEventListener("keydown", function (event) {
    if (event.key !== "Enter" || !event.target.matches("[data-sheet-value], [data-sheet-units]")) return;
    event.preventDefault();
    var kind = event.target.matches("[data-sheet-units]") ? "units" : "value";
    var shown = rows().filter(function (b) { return !b.hidden; });
    var after = shown.slice(shown.indexOf(event.target.closest("tbody")) + 1);
    var next = after.filter(waiting)[0] || after[0];
    if (!next) { form.querySelector("button:not([type=button])").focus(); return; }
    var target = el(next, kind);
    (target.disabled ? el(next, "value") : target).focus();
  });
  // A row is still waiting when nothing is typed and it was not read earlier this month.
  function waiting(body) { return !body.hidden && !body.dataset.done && !el(body, "value").value.trim(); }
  var nextButton = form.querySelector("[data-sheet-next]");
  if (nextButton) nextButton.addEventListener("click", function () {
    var next = rows().filter(waiting)[0];
    if (!next) { form.querySelector("button:not([type=button])").focus(); return; }
    var box = el(next, "value");
    box.scrollIntoView({ block: "center", behavior: "smooth" });
    box.focus({ preventScroll: true });
  });
  form.addEventListener("click", function (event) {
    var button = event.target.closest("[data-sheet-more]");
    if (!button) return;
    var more = document.getElementById(button.getAttribute("aria-controls"));
    more.hidden = !more.hidden;
    button.setAttribute("aria-expanded", String(!more.hidden));
  });
  // offline.js clears the form once a round is kept on the phone.
  form.addEventListener("reset", function () { setTimeout(totals, 0); });
  document.addEventListener("lpms:offline-changed", function () { setTimeout(totals, 0); });

  // ---- Paste or upload: the server matches rooms to meters; we only fill the rows in ---------------------------
  var box = document.querySelector("[data-sheet-import]");
  if (box) {
    var result = box.querySelector("[data-import-result]");
    var go = box.querySelector("[data-import-go]");
    var say = function (text, kind) {
      result.textContent = "";
      var p = document.createElement("div");
      p.className = kind === "bad" ? "text-danger" : "";
      p.textContent = text;
      result.appendChild(p);
      return p;
    };
    go.addEventListener("click", function () {
      if (!navigator.onLine) { say(box.dataset.offline, "bad"); return; }
      var data = new FormData();
      var file = box.querySelector("[data-import-file]");
      var photo = box.querySelector("[data-import-photo]");
      var mode = box.querySelector("[data-import-mode]:checked").value;
      data.append("csrfmiddlewaretoken", form.querySelector("[name=csrfmiddlewaretoken]").value);
      data.append("text", box.querySelector("[data-import-text]").value);
      data.append("mode", mode);
      data.append("utility", utility());
      if (file.files.length) data.append("file", file.files[0]);
      // A photo of the notebook is read by the model on the server (D-144); it wins over pasted lines.
      var scanning = photo && photo.files.length;
      if (scanning) { data.append("photo", photo.files[0]); say(box.dataset.reading); }
      go.disabled = true;
      fetch(box.dataset.url, { method: "POST", body: data, credentials: "same-origin",
                               headers: { "Accept": "application/json" } })
        .then(function (r) { return r.json().then(function (body) { return { ok: r.ok, body: body }; }); })
        .then(function (r) {
          scansLeft(r.body.left);
          if (!r.ok) { say(r.body.error || box.dataset.failed, "bad"); return; }
          if (scanning) photo.value = "";
          fill(r.body, mode);
        })
        .catch(function () { say(box.dataset.failed, "bad"); })
        .finally(function () { go.disabled = false; });
    });

    var scansLeft = function (n) {
      var p = box.querySelector("[data-scan-left]");
      if (p && typeof n === "number") p.textContent = box.dataset.left.replace("{n}", n);
    };

    var fill = function (body, mode) {
      var filled = 0;
      var skipped = body.skipped.slice();
      rows().forEach(function (b) { b.classList.remove("sheet-filled"); });
      body.matches.forEach(function (m) {
        var row = form.querySelector('tbody[data-meter="' + m.meter + '"]');
        if (!row) return;
        if (mode === "units") {
          if (el(row, "units").disabled) {
            skipped.push({ line: m.line, reason: box.dataset.noUnits });
            return;
          }
          el(row, "units").value = m.value;
          compute(row, "units");
        } else {
          el(row, "value").value = m.value;
          compute(row, "value");
        }
        row.classList.add("sheet-filled");
        filled += 1;
      });
      totals();
      say(filled ? box.dataset.filled.replace("{n}", filled) : box.dataset.none, filled ? "" : "bad");
      if (skipped.length) {
        var ul = document.createElement("ul");
        ul.className = "mb-0 mt-1 text-danger";
        skipped.forEach(function (s) {
          var li = document.createElement("li");
          li.textContent = s.line + " — " + s.reason;
          ul.appendChild(li);
        });
        result.appendChild(ul);
      }
      var first = form.querySelector("tbody.sheet-filled");
      if (first) first.scrollIntoView({ block: "center" });
    };
  }

  totals();
})();
