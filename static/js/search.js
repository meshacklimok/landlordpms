// Global search suggestions (D-054). The box is a plain GET form, so it works without this script;
// this adds suggestions as you type, arrow keys, Enter to open, Escape to close and "/" to focus.
(function () {
  var form = document.querySelector("form.global-search");
  if (!form || !window.fetch) return;
  var input = form.querySelector("input[name=q]");
  var list = document.getElementById("global-search-list");
  var status = form.querySelector("[data-role=status]");
  var minLength = parseInt(form.dataset.minLength, 10) || 2;
  var DELAY = 180;
  var cache = {};
  var timer = null;
  var pending = null;
  var items = [];
  var active = -1;
  var lastQuery = "";

  function norm(q) { return q.replace(/\s+/g, " ").trim().slice(0, 60); }

  // Wraps the first match of the query in <mark>, using text nodes only.
  function highlight(el, text, q) {
    var i = q ? text.toLowerCase().indexOf(q.toLowerCase()) : -1;
    if (i < 0) { el.textContent = text; return; }
    el.appendChild(document.createTextNode(text.slice(0, i)));
    var mark = document.createElement("mark");
    mark.textContent = text.slice(i, i + q.length);
    el.appendChild(mark);
    el.appendChild(document.createTextNode(text.slice(i + q.length)));
  }

  function option(id, href, label, detail, q) {
    var a = document.createElement("a");
    a.className = "gs-item";
    a.id = id;
    a.href = href;
    a.setAttribute("role", "option");
    a.setAttribute("aria-selected", "false");
    a.tabIndex = -1;
    var top = document.createElement("div");
    highlight(top, label, q);
    a.appendChild(top);
    if (detail) {
      var sub = document.createElement("div");
      sub.className = "small text-muted";
      highlight(sub, detail, q);
      a.appendChild(sub);
    }
    return a;
  }

  function open() {
    list.classList.add("show");
    input.setAttribute("aria-expanded", "true");
  }

  function close() {
    list.classList.remove("show");
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    active = -1;
  }

  function select(i) {
    if (active >= 0 && items[active]) items[active].setAttribute("aria-selected", "false");
    active = i;
    if (i < 0) { input.removeAttribute("aria-activedescendant"); return; }
    items[i].setAttribute("aria-selected", "true");
    input.setAttribute("aria-activedescendant", items[i].id);
    items[i].scrollIntoView({ block: "nearest" });
  }

  function render(data, q) {
    list.textContent = "";
    items = [];
    active = -1;
    var n = 0;
    data.groups.forEach(function (g) {
      var head = document.createElement("div");
      head.className = "gs-head px-3 pt-2 pb-1 text-muted fw-semibold";
      head.setAttribute("role", "presentation");
      head.textContent = g.title;
      list.appendChild(head);
      g.hits.forEach(function (h) {
        var a = option("gs-opt-" + n++, h.url, h.label, h.detail, q);
        list.appendChild(a);
        items.push(a);
      });
    });
    if (!items.length) {
      var none = document.createElement("div");
      none.className = "px-3 py-2 small text-muted";
      none.textContent = form.dataset.textNone;
      list.appendChild(none);
    }
    var all = option("gs-opt-all", form.action + "?q=" + encodeURIComponent(q), form.dataset.textAll, "", "");
    all.classList.add("border-top", "small", "fw-semibold");
    list.appendChild(all);
    items.push(all);
    status.textContent = items.length > 1 ? form.dataset.textFound.replace("%s", items.length - 1) : form.dataset.textNone;
    open();
  }

  function fail() {
    list.textContent = "";
    items = [];
    var msg = document.createElement("div");
    msg.className = "px-3 py-2 small text-muted";
    msg.textContent = form.dataset.textError;
    list.appendChild(msg);
    open();
  }

  function lookup(q) {
    if (cache[q]) { render(cache[q], q); return; }
    if (pending) pending.abort();
    pending = window.AbortController ? new AbortController() : null;
    fetch(form.dataset.suggestUrl + "?q=" + encodeURIComponent(q), {
      credentials: "same-origin",
      headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" },
      signal: pending ? pending.signal : undefined
    }).then(function (r) {
      var type = r.headers.get("Content-Type") || "";
      if (!r.ok || type.indexOf("application/json") < 0) throw new Error("search " + r.status);
      return r.json();
    }).then(function (data) {
      cache[q] = data;
      if (norm(input.value) === q) render(data, q);  // drop answers to a query no longer in the box
    }).catch(function (e) {
      if (e.name !== "AbortError") fail();
    });
  }

  input.addEventListener("input", function () {
    var q = norm(input.value);
    clearTimeout(timer);
    if (q === lastQuery && list.classList.contains("show")) return;
    lastQuery = q;
    if (q.length < minLength) {
      if (pending) pending.abort();
      close();
      return;
    }
    timer = setTimeout(function () { lookup(q); }, DELAY);
  });

  input.addEventListener("keydown", function (e) {
    var shown = list.classList.contains("show");
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!shown && norm(input.value).length >= minLength) { lookup(norm(input.value)); return; }
      if (items.length) select(active < items.length - 1 ? active + 1 : 0);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (items.length) select(active > 0 ? active - 1 : items.length - 1);
    } else if (e.key === "Enter") {
      if (shown && active >= 0 && items[active]) {
        e.preventDefault();
        window.location.assign(items[active].href);
      }
    } else if (e.key === "Escape") {
      if (shown) { e.preventDefault(); close(); } else { input.value = ""; lastQuery = ""; }
    } else if (e.key === "Tab") {
      close();
    }
  });

  input.addEventListener("focus", function () {
    if (items.length && norm(input.value) === lastQuery && lastQuery.length >= minLength) open();
  });

  // Keep focus in the box when a suggestion is clicked, so the click lands on the link.
  list.addEventListener("mousedown", function (e) { e.preventDefault(); });
  list.addEventListener("mousemove", function (e) {
    var i = items.indexOf(e.target.closest ? e.target.closest(".gs-item") : null);
    if (i >= 0 && i !== active) select(i);
  });

  document.addEventListener("click", function (e) {
    if (!form.contains(e.target)) close();
  });

  // "/" focuses the box from anywhere except a field being typed in.
  document.addEventListener("keydown", function (e) {
    if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
    var t = e.target;
    if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    e.preventDefault();
    var nav = document.getElementById("nav");
    if (nav && !nav.classList.contains("show") && input.offsetParent === null && window.bootstrap) {
      window.bootstrap.Collapse.getOrCreateInstance(nav).show();
    }
    input.focus();
    input.select();
  });
})();
