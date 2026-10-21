/* The floating NyumbaPMS AI panel (base.html): the Ask conversation in a small window on any page.
   Talks to /ask/web-api/ (ai_assistant.web_api). Text is always set with textContent, never as HTML. */
(function () {
  "use strict";
  var fab = document.querySelector("[data-ask-fab]");
  var panel = document.querySelector("[data-ask-panel]");
  if (!fab || !panel) return;

  var api = fab.getAttribute("data-api");
  var body = panel.querySelector("[data-ask-body]");
  var state = null;
  var busy = false;
  var draft = "";

  function cookie(name) {
    var found = document.cookie.split("; ").filter(function (c) { return c.indexOf(name + "=") === 0; })[0];
    return found ? decodeURIComponent(found.slice(name.length + 1)) : "";
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function request(method, payload) {
    var opts = { method: method, credentials: "same-origin", headers: { "Accept": "application/json" } };
    if (payload) {
      opts.headers["Content-Type"] = "application/json";
      opts.headers["X-CSRFToken"] = cookie("csrftoken");
      opts.body = JSON.stringify(payload);
    }
    return fetch(api, opts).then(function (r) {
      return r.json().then(function (data) { return { ok: r.ok, data: data }; }, function () { return { ok: false, data: {} }; });
    });
  }

  function open(on, quiet) {
    panel.hidden = !on;
    fab.setAttribute("aria-expanded", on ? "true" : "false");
    if (on) {
      if (!state) load();
      var box = panel.querySelector("textarea");
      if (box) box.focus();
    } else {
      if (!quiet) fab.focus();
      showFab();
    }
  }
  function load() {
    body.replaceChildren(el("p", "text-body-secondary small", panel.getAttribute("data-loading")));
    request("GET").then(function (res) {
      if (!res.ok) { body.replaceChildren(el("p", "text-danger small", panel.getAttribute("data-failed"))); return; }
      state = res.data;
      render();
    }, function () { body.replaceChildren(el("p", "text-danger small", panel.getAttribute("data-failed"))); });
  }
  function send(payload, done) {
    if (busy) return;
    busy = true;
    render();
    request("POST", payload).then(function (res) {
      busy = false;
      var note = null;
      if (res.ok) {
        note = res.data.message ? { ok: true, text: res.data.message } : null;
        state = res.data;
        if (done) done();
      } else {
        note = { ok: false, text: res.data.message || panel.getAttribute("data-failed") };
      }
      render(note);
    }, function () { busy = false; render({ ok: false, text: panel.getAttribute("data-failed") }); });
  }

  function card(c, texts) {
    var box = el("div", "ask-w-card");
    box.appendChild(el("div", "fw-semibold mb-1", c.title));
    var dl = el("dl", "ask-w-lines");
    c.lines.forEach(function (l) { dl.appendChild(el("dt", null, l[0])); dl.appendChild(el("dd", null, l[1])); });
    box.appendChild(dl);
    if (c.state === "done") {
      if (c.text) box.appendChild(el("p", "ask-w-text", c.text));
      var ok = el("div", "text-success", c.result + " ");
      if (c.url) { var a = el("a", null, panel.getAttribute("data-open")); a.href = c.url; ok.appendChild(a); }
      box.appendChild(ok);
    } else if (c.state === "dismissed") {
      box.appendChild(el("div", "text-body-secondary", panel.getAttribute("data-dismissed")));
    } else if (c.expired) {
      box.appendChild(el("div", "text-body-secondary", panel.getAttribute("data-expired")));
    } else {
      var area = null;
      if (c.kind === "message") {
        area = el("textarea", "form-control form-control-sm mb-2");
        area.rows = 3; area.maxLength = 459; area.value = c.text;
        area.setAttribute("aria-label", panel.getAttribute("data-message"));
        box.appendChild(area);
      }
      box.appendChild(el("div", "text-body-secondary mb-2", panel.getAttribute("data-careful")));
      var yes = el("button", "btn btn-primary btn-sm me-2", panel.getAttribute("data-t-confirm"));
      yes.type = "button"; yes.disabled = busy;
      yes.addEventListener("click", function () {
        send({ action: "confirm", id: c.id, text: area ? area.value : undefined });
      });
      var no = el("button", "btn btn-outline-secondary btn-sm", panel.getAttribute("data-t-dismiss"));
      no.type = "button"; no.disabled = busy;
      no.addEventListener("click", function () { send({ action: "dismiss", id: c.id }); });
      box.appendChild(yes); box.appendChild(no);
    }
    return box;
  }

  function render(note) {
    var out = [];
    if (note) out.push(el("p", note.ok ? "text-success small" : "text-danger small", note.text));
    if (!state.platform_on) {
      out.push(el("p", "text-body-secondary small", panel.getAttribute("data-not-set-up")));
    } else if (!state.is_on) {
      out.push(el("p", "text-body-secondary small", state.can_switch ? panel.getAttribute("data-off-owner") : panel.getAttribute("data-off")));
    } else {
      var thread = el("div", "ask-w-thread");
      state.turns.forEach(function (t) {
        thread.appendChild(el("div", "ask-w-q", t.q));
        var a = el("div", "ask-w-a" + (t.ok ? "" : " ask-w-warn"));
        t.a.split("\n").forEach(function (line) { a.appendChild(el("p", "mb-1", line)); });
        if (t.links.length) {
          var links = el("div", "text-body-secondary mt-1", panel.getAttribute("data-pages") + " ");
          t.links.forEach(function (l) { var x = el("a", "me-2", l.label); x.href = l.url; links.appendChild(x); });
          a.appendChild(links);
        }
        thread.appendChild(a);
        t.cards.forEach(function (c) { thread.appendChild(card(c)); });
      });
      out.push(thread);
      if (!state.turns.length && state.starters && state.starters.length) {
        var starters = el("div", "ask-w-starters");
        starters.appendChild(el("p", "ask-w-hint", panel.getAttribute("data-try")));
        state.starters.forEach(function (text) {
          var chip = el("button", "ask-w-chip", text);
          chip.type = "button"; chip.disabled = busy;
          chip.addEventListener("click", function () { send({ action: "ask", question: text }); });
          starters.appendChild(chip);
        });
        out.push(starters);
      }

      var form = el("form", "ask-w-form");
      var box = el("textarea", "ask-w-input");
      box.rows = 1; box.maxLength = state.max_length; box.required = true; box.disabled = busy;
      box.value = draft;
      box.placeholder = panel.getAttribute("data-placeholder");
      box.setAttribute("aria-label", panel.getAttribute("data-question"));
      box.setAttribute("enterkeyhint", "send");
      var grow = function () { box.style.height = "auto"; box.style.height = Math.min(box.scrollHeight, 120) + "px"; };
      box.addEventListener("input", function () { draft = box.value; grow(); });
      // Enter sends, Shift+Enter adds a line. Phone keyboards often report Enter only through beforeinput.
      box.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); }
      });
      box.addEventListener("beforeinput", function (e) {
        if (e.inputType === "insertLineBreak") { e.preventDefault(); form.requestSubmit(); }
      });
      var go = el("button", "ask-send");
      go.type = "submit"; go.disabled = busy;
      go.setAttribute("aria-label", panel.getAttribute("data-ask"));
      go.title = panel.getAttribute("data-ask");
      go.appendChild(el("i", busy ? "bi bi-three-dots" : "bi bi-send-fill"));
      go.firstChild.setAttribute("aria-hidden", "true");
      form.appendChild(box); form.appendChild(go);
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        var q = box.value.trim();
        if (q) send({ action: "ask", question: q }, function () { draft = ""; });
      });
      out.push(form);
    }
    fresh.hidden = !(state.platform_on && state.is_on && state.turns.length);
    fresh.disabled = busy;
    body.replaceChildren.apply(body, out);
    var last = body.querySelector(".ask-w-thread");
    if (last) body.scrollTop = body.scrollHeight;
    if (!busy) { var t = body.querySelector(".ask-w-input"); if (t && !panel.hidden && !note) t.focus({ preventScroll: true }); }
  }

  var fresh = panel.querySelector("[data-ask-new]");
  fresh.addEventListener("click", function () { send({ action: "new" }); });

  // The button shows while the page is being scrolled and for 4 seconds after; it stays while the panel is open.
  var hideTimer = null;
  function showFab() {
    fab.classList.remove("is-away");
    window.clearTimeout(hideTimer);
    hideTimer = window.setTimeout(function () {
      if (panel.hidden && !fab.matches(":hover, :focus-visible")) fab.classList.add("is-away");
    }, 4000);
  }
  window.addEventListener("scroll", showFab, { passive: true });
  // On a page too short to scroll, moving toward the bottom right corner brings it back.
  document.addEventListener("mousemove", function (e) {
    if (e.clientX > window.innerWidth - 220 && e.clientY > window.innerHeight - 160) showFab();
  }, { passive: true });
  fab.addEventListener("focus", showFab);
  showFab();

  fab.addEventListener("click", function () { open(panel.hidden); });
  panel.querySelector("[data-ask-close]").addEventListener("click", function () { open(false); });
  // Pressing anywhere else on the page closes the panel.
  document.addEventListener("click", function (e) {
    if (panel.hidden || panel.contains(e.target) || fab.contains(e.target)) return;
    open(false, true);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !panel.hidden) open(false);
  });
})();
