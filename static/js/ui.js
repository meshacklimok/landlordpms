// Small behaviours any page can ask for with a data- attribute. The Content Security Policy runs no inline code
// (D-107), so templates use these instead of onclick= and the like:
//   data-confirm="Question?"  on a form, or a button or link: asks first, and stops if the answer is no
//   data-autosubmit           on a select: submits its form when changed. On a filter form: submits it when any
//                             select, tick box or date/month changes. A search box named q reloads after a typing
//                             pause, clears on Escape, and the filters in use show as removable chips
//   data-print                on a button: prints the page
//   data-reload               on a button: reloads the page
//   data-copy="#id"           on a button: copies that element's value or text
//   data-select-on-focus      on an input: selects its text when focused
//   data-filter="#id"         on an input: hides children of #id whose data-text does not contain what is typed
// Every page also gets (D-141): the Back to top button, a loading bar while the next page opens, keyboard scrolling for
// wide tables, and focus on a form's error summary.
(function () {
  "use strict";

  // Confirmations listen in the capture phase, so a "no" stops the page's other handlers (offline sending) too.
  function cancel(event) { event.preventDefault(); event.stopImmediatePropagation(); }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (form.matches("form[data-confirm]") && !window.confirm(form.dataset.confirm)) cancel(event);
  }, true);

  document.addEventListener("click", function (event) {
    var el = event.target.closest("[data-confirm]:not(form)");
    if (el && !window.confirm(el.dataset.confirm)) cancel(event);
  }, true);

  document.addEventListener("click", function (event) {
    var el = event.target.closest("[data-print], [data-reload], [data-copy]");
    if (!el) return;
    if (el.hasAttribute("data-print")) {
      window.print();
    } else if (el.hasAttribute("data-reload")) {
      window.location.reload();
    } else {
      var source = document.querySelector(el.dataset.copy);
      if (source && navigator.clipboard) {
        navigator.clipboard.writeText(source.matches("input, textarea") ? source.value : source.innerText);
      }
    }
  });

  // A date or month typed by keyboard changes once per part, so wait for a whole year before reloading.
  function readyToSubmit(el) {
    if (el.matches("select, input[type=checkbox], input[type=radio]")) return true;
    if (el.matches("input[type=date], input[type=month]")) return /^(19|20)\d\d-/.test(el.value);
    return false;
  }

  document.addEventListener("change", function (event) {
    var el = event.target, form = el.form;
    if (!form) return;
    if (el.matches("select[data-autosubmit]") || (form.matches("form[data-autosubmit]") && readyToSubmit(el))) {
      form.submit();
    }
  });

  // A pressed submit button shows "sending" and cannot be pressed twice (no double payments). It resets after 10
  // seconds (a download keeps the page open) or when the user comes back with the Back button.
  // data-no-busy on a form skips this.
  document.addEventListener("submit", function (event) {
    var button = event.submitter;
    if (event.target.hasAttribute("data-no-busy") || !button) return;
    window.setTimeout(function () {
      if (event.defaultPrevented || button.disabled) return;
      button.classList.add("is-busy");
      button.disabled = true;
      window.setTimeout(function () { button.classList.remove("is-busy"); button.disabled = false; }, 10000);
    }, 0);
  });
  window.addEventListener("pageshow", function (event) {
    if (!event.persisted) return;
    document.querySelectorAll(".btn.is-busy").forEach(function (b) { b.classList.remove("is-busy"); b.disabled = false; });
  });

  // Success messages fade away; warnings and errors stay until the page changes.
  window.setTimeout(function () {
    document.querySelectorAll(".alert-autohide").forEach(function (a) {
      a.classList.add("is-leaving");
      window.setTimeout(function () { a.remove(); }, 450);
    });
  }, 8000);

  document.addEventListener("focusin", function (event) {
    var el = event.target;
    if (el.matches("input[data-select-on-focus], textarea[data-select-on-focus]")) el.select();
  });

  document.addEventListener("input", function (event) {
    var el = event.target;
    if (!el.matches("input[data-filter]")) return;
    var q = el.value.trim().toLowerCase(), list = document.querySelector(el.dataset.filter);
    if (!list) return;
    list.querySelectorAll("[data-text]").forEach(function (item) {
      item.classList.toggle("d-none", q !== "" && item.dataset.text.indexOf(q) === -1);
    });
  });

  // D-141. Back to top: shown once the page is scrolled well down; puts keyboard focus back at the start of the content.
  var toTop = document.querySelector("[data-back-to-top]");
  if (toTop) {
    var ticking = false;
    var place = function () {
      ticking = false;
      var long = document.documentElement.scrollHeight > window.innerHeight * 2;
      toTop.classList.toggle("is-visible", long && window.scrollY > Math.max(600, window.innerHeight * 1.2));
    };
    window.addEventListener("scroll", function () {
      if (!ticking) { ticking = true; window.requestAnimationFrame(place); }
    }, { passive: true });
    place();
    toTop.addEventListener("click", function () {
      var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      window.scrollTo({ top: 0, behavior: reduce ? "auto" : "smooth" });
      var main = document.getElementById("main");
      if (main) main.focus({ preventScroll: true });
    });
  }

  // D-141. A thin bar at the top while the next page loads. Not for new tabs, downloads, anchors on this page or
  // forms whose submit was cancelled; it clears after 10 seconds (a download keeps this page) or on Back.
  var root = document.documentElement, loadingTimer = null;
  function loading() {
    root.classList.add("is-loading");
    window.clearTimeout(loadingTimer);
    loadingTimer = window.setTimeout(function () { root.classList.remove("is-loading"); }, 10000);
  }
  document.addEventListener("click", function (event) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    var a = event.target.closest("a[href]");
    if (!a || a.target && a.target !== "_self" || a.hasAttribute("download") || a.dataset.bsToggle) return;
    var url = new URL(a.href, window.location.href);
    if (url.origin !== window.location.origin || /^(mailto|tel|sms):/.test(a.getAttribute("href"))) return;
    if (url.pathname === window.location.pathname && url.search === window.location.search && url.hash) return;
    if (/\.(csv|pdf|xlsx|zip|ics)$/i.test(url.pathname) || /[?&](format|export|download)=/i.test(url.search)) return;
    loading();
  });
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (form.target && form.target !== "_self") return;
    window.setTimeout(function () { if (!event.defaultPrevented) loading(); }, 0);
  });
  window.addEventListener("pageshow", function () { root.classList.remove("is-loading"); });

  // D-141. Tables wider than the screen: a labelled region that can be scrolled with arrow keys, with an edge shown on
  // the side that has more columns.
  function markOverflow(box) {
    var more = box.scrollWidth - box.clientWidth > 2;
    if (!more) {
      box.removeAttribute("data-overflow");
      if (box.dataset.madeFocusable) { box.removeAttribute("tabindex"); box.removeAttribute("role"); box.removeAttribute("aria-label"); delete box.dataset.madeFocusable; }
      return;
    }
    var left = box.scrollLeft > 2, right = box.scrollLeft + box.clientWidth < box.scrollWidth - 2;
    box.setAttribute("data-overflow", left && right ? "both" : left ? "end" : "start");
    if (!box.hasAttribute("tabindex")) {
      var table = box.querySelector("table"), caption = table && table.querySelector("caption");
      var heading = !caption && box.closest("section, .card") && box.closest("section, .card").querySelector("h2, h3, .card-header");
      box.setAttribute("tabindex", "0");
      box.setAttribute("role", "region");
      box.setAttribute("aria-label", (caption || heading) ? (caption || heading).textContent.trim() : document.title);
      box.dataset.madeFocusable = "1";
    }
  }
  function markAll() { document.querySelectorAll(".table-responsive").forEach(markOverflow); }
  document.addEventListener("scroll", function (event) {
    if (event.target.classList && event.target.classList.contains("table-responsive")) markOverflow(event.target);
  }, true);
  var resizeTimer = null;
  window.addEventListener("resize", function () { window.clearTimeout(resizeTimer); resizeTimer = window.setTimeout(markAll, 150); });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", markAll); else markAll();

  // List-page filters (any form[data-autosubmit] with a search box named q):
  //  - typing in the search box reloads the list after a short pause (2+ letters, or emptied), and the cursor
  //    comes back to the box afterwards, so you can keep typing; Enter still searches at once
  //  - Escape in the box clears the search
  //  - every filter in use is shown under the form as a chip that removes just that filter, with "Clear all"
  var SEARCH_PAUSE = 300, FOCUS_KEY = "filterSearchFocus";

  function filterForms() { return document.querySelectorAll("form[data-autosubmit][method=get]"); }
  function searchBox(form) { return form.querySelector("input[type=search][name=q]"); }

  document.addEventListener("input", function (event) {
    var el = event.target, form = el.form;
    if (!form || !el.matches("input[type=search][name=q]") || !form.matches("form[data-autosubmit]")) return;
    window.clearTimeout(el._pause);
    var len = el.value.trim().length;
    if (len === 1) return;  // one letter would match nearly everything
    el._pause = window.setTimeout(function () { liveSearch(form); }, SEARCH_PAUSE);
  });

  // Fetch the filtered page and swap in everything around the form, leaving the form (and the cursor in it) alone.
  // Pages with charts, and any failure, fall back to a normal reload.
  var liveRequest = null;
  function liveSearch(form) {
    var params = new URLSearchParams(new FormData(form)), url = window.location.pathname + "?" + params.toString();
    var forms = Array.prototype.slice.call(document.querySelectorAll("form[data-autosubmit][method=get]"));
    if (!window.fetch || !window.DOMParser || document.querySelector("[data-chart]") || forms.indexOf(form) < 0) { submitSearch(form); return; }
    if (liveRequest) liveRequest.abort();
    var controller = liveRequest = new AbortController(), parent = form.parentNode;
    loading();
    fetch(url, { credentials: "same-origin", signal: controller.signal, headers: { "X-Requested-With": "fetch" } })
      .then(function (r) {
        if (!r.ok || r.redirected || (r.headers.get("Content-Type") || "").indexOf("text/html") < 0) throw new Error("reload");
        return r.text();
      })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        var fresh = Array.prototype.slice.call(doc.querySelectorAll("form[data-autosubmit][method=get]"))[forms.indexOf(form)];
        if (!fresh || fresh.parentNode.children.length < 2 || fresh.parentNode.querySelector("[data-chart], script")) throw new Error("reload");
        var title = doc.querySelector("title");
        Array.prototype.slice.call(parent.children).forEach(function (child) { if (child !== form) parent.removeChild(child); });
        Array.prototype.slice.call(fresh.parentNode.children).forEach(function (child) {
          parent.appendChild(child === fresh ? form : document.adoptNode(child));
        });
        window.history.replaceState(null, title ? title.textContent : "", url);
        addChips(form);
        root.classList.remove("is-loading");
        markAll();
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") return;
        submitSearch(form);
      });
  }

  function submitSearch(form) {
    try { window.sessionStorage.setItem(FOCUS_KEY, window.location.pathname); } catch (e) { /* private window */ }
    form.submit();
  }

  document.addEventListener("keydown", function (event) {
    var el = event.target;
    if (!el.matches || !el.matches("input[type=search][name=q]") || !el.form || !el.form.matches("form[data-autosubmit]")) return;
    if (event.key === "Enter") { window.clearTimeout(el._pause); }
    else if (event.key === "Escape" && el.value !== "") { event.preventDefault(); el.value = ""; window.clearTimeout(el._pause); submitSearch(el.form); }
  });

  function labelFor(el) {
    var l = el.id && document.querySelector("label[for='" + el.id + "']");
    return (l && l.textContent.trim()) || el.getAttribute("aria-label") || "";
  }

  function paramsWithout(names) {
    var params = new URLSearchParams(window.location.search);
    names.forEach(function (n) { params.delete(n); });
    params.delete("page");
    var s = params.toString();
    return window.location.pathname + (s ? "?" + s : "");
  }

  function addChips(form) {
    if (!searchBox(form)) return;
    var chips = [];
    Array.prototype.forEach.call(form.elements, function (el) {
      if (!el.name || el.name === "page" || el.disabled || el.matches("[type=hidden], [type=submit], [type=button], button")) return;
      if (el.matches("[type=checkbox], [type=radio]")) {
        if (!el.checked) return;
        chips.push({ name: el.name, text: labelFor(el) || el.name });
        return;
      }
      var value = el.value.trim();
      if (!value) return;
      var text;
      if (el.matches("select")) {
        text = el.options[el.selectedIndex] ? el.options[el.selectedIndex].text.trim() : value;
      } else if (el.matches("[type=search], [type=text]")) {
        text = "“" + value + "”";
      } else {
        text = (labelFor(el) ? labelFor(el) + ": " : "") + value;
      }
      if (!chips.some(function (c) { return c.name === el.name && c.text === text; })) chips.push({ name: el.name, text: text });
    });
    if (!chips.length) return;

    var body = document.body.dataset, row = document.createElement("ul");
    row.className = "chip-row filter-chips d-print-none";
    row.setAttribute("aria-label", body.textFilters || "Filters applied");
    chips.forEach(function (c) {
      var li = document.createElement("li"), a = document.createElement("a"), x = document.createElement("i");
      a.className = "chip"; a.href = paramsWithout([c.name]);
      a.title = (body.textRemove || "Remove filter") + ": " + c.text;
      a.appendChild(document.createTextNode(c.text + " "));
      x.className = "bi bi-x-lg"; x.setAttribute("aria-hidden", "true");
      a.appendChild(x);
      li.appendChild(a); row.appendChild(li);
    });
    var kept = Array.prototype.map.call(form.querySelectorAll("input[type=hidden][name]"), function (h) { return h.name; });
    var all = new URLSearchParams(window.location.search), clear = new URLSearchParams();
    kept.forEach(function (n) { all.getAll(n).forEach(function (v) { clear.append(n, v); }); });
    var li = document.createElement("li"), a = document.createElement("a");
    a.className = "chip"; a.href = window.location.pathname + (clear.toString() ? "?" + clear.toString() : "");
    a.textContent = body.textClearAll || "Clear all";
    li.appendChild(a); row.appendChild(li);
    form.parentNode.insertBefore(row, form.nextSibling);
  }

  function setUpFilters() {
    filterForms().forEach(addChips);
    var wanted = null;
    try { wanted = window.sessionStorage.getItem(FOCUS_KEY); window.sessionStorage.removeItem(FOCUS_KEY); } catch (e) { /* private window */ }
    if (wanted !== window.location.pathname) return;
    var box = document.querySelector("form[data-autosubmit] input[type=search][name=q]");
    if (!box) return;
    box.focus({ preventScroll: true });
    var end = box.value.length;
    try { box.setSelectionRange(end, end); } catch (e) { /* search inputs allow it; ignore if not */ }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", setUpFilters); else setUpFilters();

  // D-141. A form that came back with errors: move focus to its summary so a screen reader reads what to fix.
  var summary = document.querySelector("[data-error-summary]");
  if (summary) summary.focus();
})();
