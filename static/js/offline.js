/* Forms that work without a connection: meter rounds (D-088) and repair reports (D-090).
   A form that cannot reach the server is kept on this phone and sent when back online.
   Only what was typed is kept (ids, labels and text), never names, balances or photos.
   Kept forms belong to the person who typed them and are sent only while they are logged in. */
(function () {
  "use strict";
  var script = document.currentScript;
  var user = script && script.dataset.user;
  if (!user) return;
  var KEY = "lpms.offline.rounds";  // the name from D-088; it now holds repair reports too
  var KEEP_USER = "lpms.pages.user";
  var PING_TIMEOUT = 6000;
  var REPORT_FIELDS = ["property", "unit", "title", "description", "kind", "priority", "from_tenant"];

  function load() {
    try {
      var list = JSON.parse(localStorage.getItem(KEY) || "[]");
      return Array.isArray(list) ? list : [];
    } catch (e) { return []; }
  }
  function save(list) {
    try { localStorage.setItem(KEY, JSON.stringify(list)); return true; } catch (e) { return false; }
  }
  function mine() { return load().filter(function (r) { return r.user === user; }); }
  function add(item) {
    var all = load();
    all.push(item);
    return save(all);
  }
  function change(id, patch) {
    var kept = [];
    load().forEach(function (r) {
      if (r.id !== id) { kept.push(r); return; }
      if (patch) kept.push(Object.assign(r, patch));
    });
    save(kept);
  }
  function newId() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    var bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    return Array.prototype.map.call(bytes, function (b) { return ("0" + b.toString(16)).slice(-2); }).join("");
  }
  function cookie(name) {
    var found = document.cookie.split("; ").filter(function (c) { return c.indexOf(name + "=") === 0; })[0];
    return found ? decodeURIComponent(found.slice(name.length + 1)) : "";
  }
  function changed() {
    var n = mine().length;
    document.querySelectorAll("[data-offline-badge]").forEach(function (el) {
      el.textContent = (el.dataset.text || "{n}").replace("{n}", n);
      el.hidden = !n;
    });
    document.dispatchEvent(new CustomEvent("lpms:offline-changed"));
  }

  // ---- Sending ---------------------------------------------------------------------------------------------
  function payload(item) {
    if (item.type === "repair") return { id: item.id, typed_at: item.typed_at, fields: item.fields };
    return { id: item.id, read_on: item.read_on, typed_at: item.typed_at, entries: item.entries };
  }
  var sending = false;
  function sendOne(item) {
    return fetch(item.url, {
      method: "POST", credentials: "same-origin", redirect: "manual",
      headers: { "Content-Type": "application/json", "Accept": "application/json", "X-CSRFToken": cookie("csrftoken") },
      body: JSON.stringify(payload(item))
    }).then(function (response) {
      if (response.status === 200 || response.status === 201) { change(item.id, null); return; }
      if (response.status === 422) {
        return response.json().then(function (body) {
          change(item.id, { state: "fix", message: body.message || "", errors: body.errors || {} });
        });
      }
      // Logged out, another organization, an old security token or a server error: try again later.
    }).catch(function () { /* still offline */ });
  }
  function send() {
    if (sending || !navigator.onLine) return Promise.resolve();
    var waiting = mine().filter(function (r) { return r.state === "waiting"; });
    if (!waiting.length) return Promise.resolve();
    sending = true;
    return waiting.reduce(function (chain, item) {
      return chain.then(function () { return sendOne(item); });
    }, Promise.resolve()).finally(function () { sending = false; changed(); });
  }

  // Logging out with forms still kept: they are only sent when this person logs in again here.
  document.querySelectorAll("form[data-logout]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (mine().length && !window.confirm(script.dataset.logoutText || "")) event.preventDefault();
    });
  });

  // ---- Shared by the pages with a form ------------------------------------------------------------------
  function reachable(pingUrl) {
    if (!navigator.onLine) return Promise.resolve(false);
    var controller = window.AbortController ? new AbortController() : null;
    var timer = setTimeout(function () { if (controller) controller.abort(); }, PING_TIMEOUT);
    return fetch(pingUrl, { method: "HEAD", cache: "no-store", credentials: "same-origin",
                            signal: controller ? controller.signal : undefined })
      .then(function (r) { return r.ok; }, function () { return false; })
      .finally(function () { clearTimeout(timer); });
  }

  /* On submit: if the server answers, submit as usual; if not, keep(). keep() returns true, false (nothing to
     keep) or null (no room). */
  function offlineSubmit(form, status, t, keep) {
    form.addEventListener("submit", function (event) {
      if (form.dataset.online === "go") return;
      event.preventDefault();
      var button = form.querySelector("button[type=submit], button:not([type])");
      if (button) button.disabled = true;
      reachable(form.dataset.pingUrl).then(function (ok) {
        if (ok) { form.dataset.online = "go"; form.submit(); return; }
        if (button) button.disabled = false;
        var kept = keep();
        if (kept === false) { status.textContent = t.empty; status.hidden = false; }
        if (kept === null) { status.textContent = t.full; status.hidden = false; }
      });
    });
  }

  /* The list of what this page's form kept: `describe` gives each item's line, `putBack` refills the form. */
  function keptList(list, which, describe, putBack) {
    if (!list) return;
    var t = list.dataset;
    var render = function () {
      var items = mine().filter(which);
      list.hidden = !items.length;
      var ul = list.querySelector("ul");
      ul.textContent = "";
      items.forEach(function (item) {
        var li = document.createElement("li");
        li.className = "list-group-item small";
        var head = document.createElement("div");
        head.textContent = describe(item, t) + " · " + (item.state === "fix" ? t.fix : t.waiting);
        li.appendChild(head);
        if (item.state === "fix") {
          var why = document.createElement("div");
          why.className = "text-danger";
          why.textContent = [item.message].concat(reasons(item)).filter(Boolean).join(" ");
          li.appendChild(why);
          var back = document.createElement("button");
          back.type = "button";
          back.className = "btn btn-sm btn-outline-primary mt-1 me-2";
          back.textContent = t.putBack;
          back.addEventListener("click", function () {
            putBack(item);
            change(item.id, null);
            changed();
          });
          li.appendChild(back);
        }
        var discard = document.createElement("button");
        discard.type = "button";
        discard.className = "btn btn-sm btn-link text-danger mt-1 p-0";
        discard.textContent = t.discard;
        discard.addEventListener("click", function () {
          if (window.confirm(t.discardConfirm)) { change(item.id, null); changed(); }
        });
        li.appendChild(discard);
        ul.appendChild(li);
      });
    };
    document.addEventListener("lpms:offline-changed", render);
  }

  function reasons(item) {
    var labels = {};
    (item.entries || []).forEach(function (e) { labels[e.meter] = e.label; });
    return Object.keys(item.errors || {}).map(function (k) {
      var label = labels[k] || "";  // meters are named; a report's reasons speak for themselves
      return (label ? label + ": " : "") + item.errors[k].join(" ");
    });
  }

  // ---- The meter round page (D-088) ---------------------------------------------------------------------
  var round = document.querySelector("form[data-offline-round]");
  if (round) {
    var roundList = document.querySelector("[data-offline-list]");
    var roundStatus = document.querySelector("[data-offline-status]");
    var rt = roundList ? roundList.dataset : {};
    var cards = function () { return round.querySelectorAll("[data-meter]"); };
    var field = function (card, prefix) { return card.querySelector("[name^='" + prefix + "-']"); };

    offlineSubmit(round, roundStatus, rt, function () {
      var entries = [];
      var photos = false;
      cards().forEach(function (card) {
        var value = field(card, "value").value.trim();
        var photo = field(card, "photo");
        // The reading sheet (D-143): the units used, when that box was typed last, rather than the reading.
        var units = field(card, "units"), byUnits = field(card, "byunits");
        var unitsUsed = byUnits && byUnits.value === "1" && units ? units.value.trim() : "";
        if (!value && !unitsUsed) return;
        if (photo && photo.files && photo.files.length) photos = true;
        var entry = { meter: card.dataset.meter, label: card.dataset.label, value: value,
                      note: field(card, "note").value.trim(), replaced: field(card, "replaced").checked };
        if (unitsUsed) entry.units_used = unitsUsed;
        entries.push(entry);
      });
      if (!entries.length) return false;
      var item = { id: newId(), user: user, property: round.dataset.property, url: round.dataset.syncUrl,
                   read_on: round.querySelector("[name=read_on]").value, typed_at: new Date().toISOString(),
                   entries: entries, state: "waiting", message: "", errors: {} };
      if (!add(item)) return null;
      round.reset();
      round.querySelector("[name=read_on]").value = item.read_on;
      roundStatus.textContent = rt.saved + (photos ? " " + rt.photos : "");
      roundStatus.hidden = false;
      changed();
      return true;
    });

    keptList(roundList, function (r) { return r.type !== "repair" && r.property === round.dataset.property; },
      function (r, t) {
        return t.typed.replace("{when}", new Date(r.typed_at).toLocaleString()).replace("{n}", r.entries.length);
      },
      function (r) {
        round.querySelector("[name=read_on]").value = r.read_on;
        cards().forEach(function (card) {
          var entry = r.entries.filter(function (e) { return e.meter === card.dataset.meter; })[0];
          var why = (r.errors || {})[card.dataset.meter];
          var holder = card.querySelector("[data-offline-error]");
          if (holder) holder.textContent = why ? why.join(" ") : "";
          if (!entry) return;
          field(card, "value").value = entry.value;
          field(card, "note").value = entry.note || "";
          field(card, "replaced").checked = !!entry.replaced;
          if (entry.units_used && field(card, "units")) {
            field(card, "units").value = entry.units_used;
            field(card, "byunits").value = "1";
          }
        });
        round.scrollIntoView();
      });
  }

  // ---- The repair report page (D-090) -------------------------------------------------------------------
  var report = document.querySelector("form[data-offline-report]");
  if (report) {
    var reportList = document.querySelector("[data-offline-list]");
    var reportStatus = document.querySelector("[data-offline-status]");
    var pt = reportList ? reportList.dataset : {};
    var input = function (name) { return report.querySelector("[name=" + name + "]"); };
    var shown = function (name) {
      var el = input(name);
      return el && el.selectedIndex >= 0 && el.options ? el.options[el.selectedIndex].text : "";
    };

    offlineSubmit(report, reportStatus, pt, function () {
      var fields = {};
      REPORT_FIELDS.forEach(function (name) {
        var el = input(name);
        if (!el) return;
        fields[name] = el.type === "checkbox" ? (el.checked ? "on" : "") : el.value.trim();
      });
      if (!fields.title || !fields.property) return false;
      var photos = input("photos");
      var item = { id: newId(), user: user, type: "repair", url: report.dataset.syncUrl,
                   typed_at: new Date().toISOString(), fields: fields, state: "waiting", message: "", errors: {},
                   labels: { property: shown("property"), unit: shown("unit"), title: fields.title } };
      if (!add(item)) return null;
      report.reset();
      reportStatus.textContent = pt.saved + (photos && photos.files && photos.files.length ? " " + pt.photos : "");
      reportStatus.hidden = false;
      changed();
      return true;
    });

    keptList(reportList, function (r) { return r.type === "repair"; },
      function (r, t) {
        return t.typed.replace("{when}", new Date(r.typed_at).toLocaleString())
          .replace("{title}", r.fields.title).replace("{where}", r.fields.unit ? r.labels.unit : r.labels.property);
      },
      function (r) {
        REPORT_FIELDS.forEach(function (name) {
          var el = input(name);
          if (!el) return;
          if (el.type === "checkbox") el.checked = !!r.fields[name];
          else el.value = r.fields[name] || "";
        });
        reportStatus.textContent = [r.message].concat(reasons(r)).filter(Boolean).join(" ");
        reportStatus.hidden = !reportStatus.textContent;
        report.scrollIntoView();
      });
  }

  // ---- Pages kept for reading and filling in without a connection (D-218) --------------------------------
  /* The service worker keeps a copy of each staff page that is opened. Links marked data-offline-keep (the meter
     round, the repair form) are fetched ahead, so those forms open even if the page was never visited before the
     signal went. Copies belong to the person who was signed in: a different person signing in here clears them. */
  function keepPages() {
    if (!("serviceWorker" in navigator) || !navigator.onLine) return;
    var owner = null;
    try { owner = localStorage.getItem(KEEP_USER); } catch (e) { owner = null; }
    var reset = owner !== user;
    var urls = [];
    document.querySelectorAll("a[data-offline-keep]").forEach(function (a) {
      if (a.origin === location.origin && urls.indexOf(a.pathname + a.search) === -1) urls.push(a.pathname + a.search);
    });
    if (!reset && !urls.length) return;
    navigator.serviceWorker.ready.then(function (registration) {
      if (!registration.active) return;
      registration.active.postMessage({ type: "keep-pages", urls: urls, reset: reset });
      try { localStorage.setItem(KEEP_USER, user); } catch (e) { /* the copies are then cleared on each visit */ }
    }).catch(function () { /* the site works without it */ });
  }

  // ---- A saved copy shown without a connection: fade what needs the internet (D-219) -------------------------
  /* The worker marks a saved copy with data-offline-copy on <body>. Links to pages that are not saved, forms that
     cannot be kept (every one except the meter round and the repair report) and the assistant are faded and, when
     tapped, say they need the internet. Everything that works offline stays as it is. */
  function fadeWhatNeedsTheInternet() {
    if (!document.body.hasAttribute("data-offline-copy") || !("caches" in window)) return;
    var toast = null, timer = null;
    function say() {
      if (!toast) {
        toast = document.createElement("div");
        toast.className = "offline-toast";
        toast.setAttribute("role", "status");
        toast.textContent = script.dataset.needsText || "";
        document.body.appendChild(toast);
      }
      toast.hidden = false;
      clearTimeout(timer);
      timer = setTimeout(function () { toast.hidden = true; }, 3000);
    }
    function mark(el) {
      el.classList.add("offline-unavailable");
      el.setAttribute("aria-disabled", "true");
    }
    caches.open("lpmspages-1").then(function (cache) { return cache.keys(); }).catch(function () { return []; })
      .then(function (requests) {
        var kept = {};
        requests.forEach(function (r) { kept[r.url] = true; });
        function isKept(a) {  // the same address the worker keeps a page under
          return kept[a.origin + (a.pathname === "/" ? "/" : a.pathname + a.search)] === true;
        }
        document.querySelectorAll("a[href]").forEach(function (a) {
          var href = a.getAttribute("href");
          if (!href || href.charAt(0) === "#" || /^(javascript|mailto|tel):/i.test(href)) return;
          if (a.origin !== location.origin) { mark(a); return; }
          if (a.pathname === location.pathname && a.search === location.search) return;
          if (a.pathname.indexOf("/static/") === 0 || isKept(a)) return;
          mark(a);
        });
        document.querySelectorAll("form").forEach(function (form) {
          if (form.hasAttribute("data-offline-round") || form.hasAttribute("data-offline-report")) return;
          mark(form);
          form.querySelectorAll("button:not([type=button]), input[type=submit]").forEach(mark);
        });
        document.querySelectorAll(".ask-fab, [data-ask-fab]").forEach(mark);
      });
    // Tapping something faded: say why, and do nothing.
    function block(event) {
      var target = event.target.closest && event.target.closest(".offline-unavailable");
      if (!target) return;
      event.preventDefault();
      event.stopPropagation();
      say();
    }
    document.addEventListener("click", block, true);
    document.addEventListener("submit", block, true);
    // The connection is back: say so and offer the live page.
    window.addEventListener("online", function () {
      var notice = document.querySelector("[data-offline-notice]");
      if (!notice) return;
      notice.className = "alert alert-success rounded-0 border-0 mb-0 py-2 small text-center";
      notice.textContent = (script.dataset.onlineText || "") + " ";
      var reload = document.createElement("button");
      reload.type = "button";
      reload.className = "btn btn-sm btn-success ms-2";
      reload.textContent = script.dataset.reloadText || "";
      reload.addEventListener("click", function () { location.reload(); });
      notice.appendChild(reload);
    });
  }

  window.addEventListener("online", send);
  changed();
  send();
  keepPages();
  fadeWhatNeedsTheInternet();
})();
