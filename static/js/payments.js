// Payments pages: live allocation totals, quick fills, clickable rows, bulk selection and confirmations.
// Everything here is an enhancement; the forms work the same without it.
(function () {
  "use strict";

  function parseAmount(text) {
    var clean = String(text || "").replace(/kshs|ksh|kes|[,\s]/gi, "");
    if (!/^\d+(\.\d+)?$/.test(clean)) return null;
    return Math.round(parseFloat(clean) * 100) / 100;
  }

  function fmt(value, currency) {
    return currency + " " + value.toLocaleString("en-KE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  // Rows that open a page when clicked (a real link inside stays the keyboard route).
  document.querySelectorAll("tr[data-href]").forEach(function (row) {
    row.addEventListener("click", function (e) {
      if (e.target.closest("a, button, input, label")) return;
      window.location = row.dataset.href;
    });
  });

  // Ask before a destructive submit.
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.dataset.confirm)) e.preventDefault();
    });
  });

  // Buttons that put a value into an input.
  document.querySelectorAll("[data-fill]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var input = document.querySelector(btn.dataset.fill);
      if (!input) return;
      input.value = btn.dataset.value;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
    });
  });

  // Reference hint follows the method picked.
  var reference = document.querySelector("[data-reference]");
  if (reference) {
    var hints = JSON.parse(document.getElementById(reference.dataset.reference).textContent);
    var update = function () {
      var picked = document.querySelector("input[name=method]:checked");
      if (picked && hints[picked.value] !== undefined) reference.placeholder = hints[picked.value];
    };
    document.querySelectorAll("input[name=method]").forEach(function (r) { r.addEventListener("change", update); });
    update();
  }

  // Allocation table: running total against the payment amount.
  document.querySelectorAll("[data-alloc]").forEach(function (box) {
    var currency = box.dataset.currency || "KES";
    var source = box.dataset.totalFrom ? document.querySelector(box.dataset.totalFrom) : null;
    var inputs = box.querySelectorAll("[data-alloc-input]");
    var summary = box.querySelector("[data-alloc-summary]");

    function total() { return source ? parseAmount(source.value) : parseFloat(box.dataset.total); }

    function refresh() {
      var sum = 0, any = false;
      inputs.forEach(function (i) {
        var v = parseAmount(i.value);
        if (v !== null) { sum += v; any = true; }
        i.classList.toggle("is-invalid", i.value.trim() !== "" && (v === null || v > parseFloat(i.dataset.max)));
      });
      var paid = total();
      summary.classList.remove("over");
      if (!any) {
        summary.textContent = summary.dataset.empty;
      } else if (paid === null || isNaN(paid)) {
        summary.textContent = fmt(sum, currency) + " " + summary.dataset.allocatedWord;
      } else if (sum > paid + 0.001) {
        summary.textContent = fmt(sum - paid, currency) + " " + summary.dataset.overWord;
        summary.classList.add("over");
      } else {
        summary.textContent = fmt(sum, currency) + " " + summary.dataset.allocatedWord +
          (paid - sum > 0.001 ? " · " + fmt(paid - sum, currency) + " " + summary.dataset.creditWord : "");
      }
    }

    // "Oldest first" fills the boxes the way the server would if left blank.
    var auto = box.querySelector("[data-alloc-auto]");
    if (auto) auto.addEventListener("click", function () {
      var left = total();
      if (left === null || isNaN(left)) return;
      inputs.forEach(function (i) {
        var take = Math.min(left, parseFloat(i.dataset.max));
        i.value = take > 0 ? take.toFixed(2) : "";
        left = Math.round((left - Math.max(take, 0)) * 100) / 100;
      });
      refresh();
    });
    var clear = box.querySelector("[data-alloc-clear]");
    if (clear) clear.addEventListener("click", function () { inputs.forEach(function (i) { i.value = ""; }); refresh(); });

    inputs.forEach(function (i) { i.addEventListener("input", refresh); });
    if (source) source.addEventListener("input", refresh);
    refresh();
  });

  // Bulk selection in the review queue.
  var bulk = document.querySelector("[data-bulk]");
  if (bulk) {
    var all = bulk.querySelector("[data-bulk-all]");
    var boxes = bulk.querySelectorAll("[data-bulk-item]");
    var button = bulk.querySelector("[data-bulk-submit]");
    var label = button.querySelector("[data-bulk-count]");
    var sync = function () {
      var n = 0, sum = 0;
      boxes.forEach(function (b) { if (b.checked) { n++; sum += parseFloat(b.dataset.amount); } });
      button.disabled = n === 0;
      label.textContent = n ? n + " · " + fmt(sum, bulk.dataset.currency || "KES") : "";
      if (all) { all.checked = n === boxes.length && n > 0; all.indeterminate = n > 0 && n < boxes.length; }
    };
    if (all) all.addEventListener("change", function () { boxes.forEach(function (b) { b.checked = all.checked; }); sync(); });
    boxes.forEach(function (b) { b.addEventListener("change", sync); });
    sync();
  }
})();
