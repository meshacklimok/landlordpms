// Small behaviours any page can ask for with a data- attribute. The Content Security Policy runs no inline code
// (D-107), so templates use these instead of onclick= and the like:
//   data-confirm="Question?"  on a form, or a button or link: asks first, and stops if the answer is no
//   data-autosubmit           on a select: submits its form when changed. On a filter form: submits it when any
//                             select, tick box or date/month changes. A search box named q reloads after a typing
//                             pause, clears on Escape, and the filters in use show as removable chips
//   data-print                on a button: prints the page
//   data-reload               on a button: reloads the page
//   data-copy="#id"           on a button: copies that element's value or text and shows "Copied"
//                             (data-copied="..." sets the word; a <span data-copy-label> inside is the part that changes)
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
      copy(el);
    }
  });

  // Copies the element named by data-copy, then shows "Copied" on the button for a moment. A button with a
  // clipboard icon gets the ticked icon; a text button shows data-copied (default "Copied"). Plain-HTTP pages and
  // older browsers have no navigator.clipboard, so they fall back to selecting the text and copying it.
  function copy(button) {
    var source = document.querySelector(button.dataset.copy);
    if (!source) return;
    var text = source.matches("input, textarea") ? source.value : source.innerText;
    var done = function () { flash(button); };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, function () { if (legacyCopy(source)) done(); });
    } else if (legacyCopy(source)) {
      done();
    }
  }

  function legacyCopy(source) {
    try {
      if (source.matches("input, textarea")) source.select();
      else window.getSelection().selectAllChildren(source);
      return document.execCommand("copy");
    } catch (e) {
      return false;
    }
  }

  function flash(button) {
    if (button._copyTimer) return;
    var icon = button.querySelector(".bi-clipboard");
    var label = button.querySelector("[data-copy-label]") || (icon ? null : button);
    var oldText = label ? label.textContent : "";
    if (icon) { icon.classList.replace("bi-clipboard", "bi-clipboard-check"); }
    if (label) { label.textContent = button.dataset.copied || "Copied"; }
    button.classList.add("is-copied");
    button._copyTimer = window.setTimeout(function () {
      if (icon) icon.classList.replace("bi-clipboard-check", "bi-clipboard");
      if (label) label.textContent = oldText;
      button.classList.remove("is-copied");
      button._copyTimer = null;
    }, 1800);
  }

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

  // Phone top bar: slides away when scrolling down, comes back on any scroll up (and at the top of the page).
  var bar = document.querySelector(".app-topbar");
  if (bar) {
    var lastY = window.scrollY;
    var barTicking = false;
    var phone = window.matchMedia("(max-width: 767.98px)");
    var tuck = function () {
      barTicking = false;
      var y = Math.max(0, window.scrollY);
      var delta = y - lastY;
      if (!phone.matches || y < bar.offsetHeight) bar.classList.remove("is-tucked");
      else if (delta > 6 && !bar.contains(document.activeElement)) bar.classList.add("is-tucked");
      else if (delta < -6) bar.classList.remove("is-tucked");
      if (Math.abs(delta) > 6) lastY = y;
    };
    window.addEventListener("scroll", function () {
      if (!barTicking) { barTicking = true; window.requestAnimationFrame(tuck); }
    }, { passive: true });
    bar.addEventListener("focusin", function () { bar.classList.remove("is-tucked"); });
  }

  // D-141. Back to top: shown once the page is scrolled well down; puts keyboard focus back at the start of the content.
  var toTop = document.querySelector("[data-back-to-top]");
  if (toTop) {
    var ticking = false;
    var idle = null;
    var place = function () {
      ticking = false;
      var long = document.documentElement.scrollHeight > window.innerHeight * 2;
      var far = long && window.scrollY > Math.max(600, window.innerHeight * 1.2);
      toTop.classList.toggle("is-visible", far);
      window.clearTimeout(idle);
      // Only while scrolling: fade out shortly after the scrolling stops, unless the button is hovered or focused.
      if (far) idle = window.setTimeout(function () {
        if (!toTop.matches(":hover, :focus-visible")) toTop.classList.remove("is-visible");
      }, 1500);
    };
    window.addEventListener("scroll", function () {
      if (!ticking) { ticking = true; window.requestAnimationFrame(place); }
    }, { passive: true });
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

  // Eye button beside a password field: show or hide what was typed.
  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("[data-toggle-password]");
    if (!btn) return;
    var input = document.getElementById(btn.getAttribute("aria-controls"));
    if (!input) return;
    var show = input.type === "password";
    input.type = show ? "text" : "password";
    btn.setAttribute("aria-pressed", show ? "true" : "false");
    var label = btn.getAttribute(show ? "data-hide-text" : "data-show-text");
    if (label) btn.setAttribute("aria-label", label);
    var icon = btn.querySelector("i");
    if (icon) icon.className = show ? "bi bi-eye-slash" : "bi bi-eye";
  });

  // Whole cards and table rows open their page, not only the name inside them. A box opens by itself when it holds
  // exactly one ordinary link and no buttons or fields; data-href names the page when a box needs it said outright,
  // data-no-click opts a box out. Links and controls inside still do their own job, a selection of text is left
  // alone, and Ctrl/Cmd-click opens a new tab.
  var BOXES = ".card, tbody tr, .list-group-item";
  var CONTROLS = "button, input, select, textarea, label, summary, [data-bs-toggle], [data-copy], [data-confirm]";

  function boxLink(box) {
    if (box.hasAttribute("data-no-click")) return "";
    if (box.dataset.href) return box.dataset.href;
    // A table row may hold an Edit/Open button as long as every link in it goes to the same page (checked below).
    var buttonLink = box.tagName !== "TR" && box.querySelector(".btn[href]");
    if (box.querySelector(CONTROLS) || buttonLink || box.querySelector("tr, .card, .list-group-item")) return "";
    var found = "";
    var links = box.querySelectorAll("a[href]");
    for (var i = 0; i < links.length; i++) {
      var a = links[i], raw = a.getAttribute("href");
      if (!raw || /^(#|mailto:|tel:|javascript:|sms:)/i.test(raw) || a.host !== window.location.host) return "";
      if (a.target || a.hasAttribute("download")) return "";
      if (found && found !== a.href) return "";
      found = a.href;
    }
    return found;
  }

  function markBoxes() {
    document.querySelectorAll(BOXES).forEach(function (box) {
      if (box.closest("[data-no-click], .modal, .offcanvas, nav")) return;
      var href = boxLink(box);
      if (href) { box.dataset.href = href; box.classList.add("is-clickable"); }
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", markBoxes); else markBoxes();

  // A form row with data-show-when="field:A,B" shows only while that field (a select) holds one of those values, or any value when it is "field:*".
  function applyShowWhen(root) {
    (root || document).querySelectorAll("[data-show-when]").forEach(function (row) {
      var parts = row.dataset.showWhen.split(":");
      var form = row.closest("form");
      var control = form && form.elements[parts[0]];
      if (!control) return;
      row.hidden = parts[1] === "*" ? !control.value : parts[1].split(",").indexOf(control.value) === -1;
    });
  }
  document.addEventListener("change", function (e) {
    if (e.target.matches("select") && e.target.form) applyShowWhen(e.target.form);
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { applyShowWhen(); }); else applyShowWhen();

  document.addEventListener("click", function (e) {
    if (e.defaultPrevented || e.button !== 0) return;
    if (e.target.closest("a, " + CONTROLS)) return;
    var box = e.target.closest("[data-href]");
    if (!box) return;
    var sel = window.getSelection && window.getSelection();
    if (sel && String(sel).length) return;
    if (e.ctrlKey || e.metaKey) window.open(box.dataset.href, "_blank", "noopener"); else window.location.href = box.dataset.href;
  });

  // D-141. A form that came back with errors: move focus to its summary so a screen reader reads what to fix.
  var summary = document.querySelector("[data-error-summary]");
  if (summary) summary.focus();
  // Report section menu: marks the section you are in, scrolls its chip into view, fades the edge that still has
  // more chips, and ends each section with a "Next: ..." link so a phone reader can tell there is more below.
  document.querySelectorAll("[data-section-nav]").forEach(function (nav) {
    var links = Array.prototype.slice.call(nav.querySelectorAll('a[href^="#"]'));
    var items = links.map(function (a) { return { a: a, el: document.getElementById(a.getAttribute("href").slice(1)) }; })
      .filter(function (i) { return i.el; });
    if (!items.length) return;
    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var edges = function () {
      nav.classList.toggle("has-more-left", nav.scrollLeft > 4);
      nav.classList.toggle("has-more-right", nav.scrollLeft + nav.clientWidth < nav.scrollWidth - 4);
    };
    nav.addEventListener("scroll", edges, { passive: true });
    window.addEventListener("resize", edges);
    edges();

    // The ≡ button: opens the "Jump to" sheet (this page's sections, then every report), when the page has one.
    var sheet = document.querySelector("[data-report-sheet]");
    // Inside the report centre's frame the centre page draws the button and sheet (a button fixed in the frame would sit
    // at the bottom of the whole, tall report), so the frame makes none.
    var inPane = document.documentElement.classList.contains("is-pane");
    if (!sheet && !inPane) {
      // A page without a list of reports (the M-Pesa overview): the sheet holds only its sections.
      sheet = document.createElement("div");
      sheet.className = "offcanvas offcanvas-start report-sheet d-print-none";
      sheet.tabIndex = -1;
      sheet.id = "report-menu";
      sheet.setAttribute("aria-labelledby", "report-menu-title");
      sheet.innerHTML = '<div class="offcanvas-header"><span class="h6 mb-0" id="report-menu-title"></span>' +
        '<button type="button" class="btn-close" data-bs-dismiss="offcanvas"></button></div>' +
        '<div class="offcanvas-body"><div data-sheet-sections hidden><div class="app-nav-label"></div>' +
        '<ul class="app-nav" data-sheet-list></ul></div></div>';
      sheet.querySelector("#report-menu-title").textContent = nav.dataset.menuLabel || "Jump to";
      sheet.querySelector(".app-nav-label").textContent = nav.dataset.hereLabel || "On this page";
      sheet.querySelector(".btn-close").setAttribute("aria-label", nav.dataset.closeLabel || "Close");
      document.body.appendChild(sheet);
    }
    if (sheet) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "report-nav-menu";
      btn.setAttribute("data-bs-toggle", "offcanvas");
      btn.setAttribute("data-bs-target", "#" + sheet.id);
      btn.setAttribute("aria-controls", sheet.id);
      var label = nav.dataset.menuLabel || "Jump to";
      btn.innerHTML = '<i class="bi bi-list" aria-hidden="true"></i>';
      btn.setAttribute("aria-label", label);
      btn.title = label;
      // A floating button at the bottom left, always in reach like the back-to-top button.
      btn.className = "report-nav-menu report-fab";
      document.body.appendChild(btn);
      var list = sheet.querySelector("[data-sheet-list]");  // only a page without a list of reports has one
      if (list) items.forEach(function (i) {
        var li = document.createElement("li"), a = document.createElement("a");
        a.href = i.a.getAttribute("href");
        a.className = "app-nav-link";
        a.textContent = i.a.textContent.trim();
        // Bootstrap cancels the jump of a link that also closes a sheet, so close first and scroll once it is shut.
        a.addEventListener("click", function (e) {
          e.preventDefault();
          var target = i.el;
          sheet.addEventListener("hidden.bs.offcanvas", function () {
            var quick = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
            target.scrollIntoView({ behavior: quick ? "auto" : "smooth", block: "start" });
            if (history.replaceState) history.replaceState(null, "", "#" + target.id);
          }, { once: true });
          sheet.querySelector(".btn-close").click();
        });
        li.appendChild(a);
        list.appendChild(li);
      });
      var holder = sheet.querySelector("[data-sheet-sections]");
      if (holder) holder.hidden = false;
    }

    var current = null;
    var mark = function () {
      var line = nav.getBoundingClientRect().bottom + 24, pick = items[0];
      items.forEach(function (i) { if (i.el.getBoundingClientRect().top <= line) pick = i; });
      if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) pick = items[items.length - 1];
      if (pick === current) return;
      current = pick;
      items.forEach(function (i) {
        if (i === pick) i.a.setAttribute("aria-current", "true"); else i.a.removeAttribute("aria-current");
      });
      var left = pick.a.offsetLeft - (nav.clientWidth - pick.a.offsetWidth) / 2;
      nav.scrollTo({ left: Math.max(0, left), behavior: reduce ? "auto" : "smooth" });
    };
    var busy = false;
    window.addEventListener("scroll", function () {
      if (!busy) { busy = true; window.requestAnimationFrame(function () { busy = false; mark(); }); }
    }, { passive: true });
    mark();

    var nextLabel = nav.dataset.nextLabel || "Next", topLabel = nav.dataset.topLabel || "Back to top";
    items.forEach(function (i, n) {
      var foot = document.createElement("p");
      foot.className = "section-next d-print-none";
      var next = items[n + 1], link = document.createElement("a");
      if (next) {
        link.href = next.a.getAttribute("href");
        link.textContent = nextLabel + ": " + next.a.textContent.trim() + " ↓";
      } else if (nav.dataset.nextReportUrl) {
        link.href = nav.dataset.nextReportUrl;
        link.textContent = nextLabel + " report: " + (nav.dataset.nextReportName || "") + " →";
      } else {
        link.href = "#main";
        link.textContent = topLabel + " ↑";
      }
      foot.appendChild(link);
      i.el.appendChild(foot);
    });
  });
  // The floating "Jump to" button fades away after six seconds without scrolling, and returns on a scroll or a touch.
  // A button marked data-keep (the console's) stays on screen.
  document.querySelectorAll(".report-fab:not([data-keep])").forEach(function (fab) {
    var timer = null;
    var wake = function () {
      fab.classList.remove("is-hidden");
      window.clearTimeout(timer);
      timer = window.setTimeout(function () {
        if (!fab.contains(document.activeElement)) fab.classList.add("is-hidden");
      }, 6000);
    };
    window.addEventListener("scroll", wake, { passive: true });
    window.addEventListener("touchstart", wake, { passive: true });
    fab.addEventListener("focus", wake);
    wake();
  });
})();

// data-check-all="name" on a checkbox: ticks or clears every checkbox of that name in the same form.
document.addEventListener("change", function (e) {
  var box = e.target;
  if (!box.matches || !box.matches("input[data-check-all]") || !box.form) return;
  box.form.querySelectorAll('input[name="' + box.getAttribute("data-check-all") + '"]').forEach(function (c) {
    c.checked = box.checked;
  });
});
