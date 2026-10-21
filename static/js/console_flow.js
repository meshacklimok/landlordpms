// Console topics flow into each other (D-178). Scrolling to the bottom of a topic loads the next one underneath it,
// the pill for the topic in view lights up and the address follows. The pills still work: a topic already on the page
// is scrolled to, any other opens as usual.
// The floating menu button's sheet lists the same pages as the pills.
(function () {
  var nav = document.getElementById("console-nav");
  var list = document.querySelector("[data-console-sheet-list]");
  if (!nav || !list) return;
  nav.querySelectorAll("a.nav-link").forEach(function (a) {
    var li = document.createElement("li"), link = document.createElement("a");
    link.href = a.getAttribute("href");
    link.className = "app-nav-link" + (a.classList.contains("active") ? " active" : "");
    link.textContent = a.textContent.trim();
    if (a.classList.contains("active")) link.setAttribute("aria-current", "page");
    if (a.target) { link.target = a.target; link.rel = a.rel; }
    li.appendChild(link);
    list.appendChild(li);
  });
})();

(function () {
  var nav = document.getElementById("console-nav");
  var flow = document.getElementById("console-flow");
  if (!nav || !flow || !window.IntersectionObserver || !window.DOMParser) return;
  var links = Array.prototype.slice.call(nav.querySelectorAll("a[data-flow]"));
  var index = links.findIndex(function (a) { return a.classList.contains("active"); });
  if (index < 0) return;

  var body = document.getElementById("console-body");
  var sections = [];   // {link, el, url, title}
  var count = 0;
  var loading = false;
  var status = document.createElement("p");
  status.className = "small text-muted text-center my-4";
  status.setAttribute("role", "status");
  flow.appendChild(status);
  var end = document.createElement("div");
  end.setAttribute("aria-hidden", "true");
  flow.appendChild(end);

  var first = document.createElement("section");
  first.setAttribute("data-flow-section", "");
  body.parentNode.insertBefore(first, body);
  first.appendChild(body);
  sections.push({ link: links[index], el: first, url: window.location.pathname + window.location.search, title: document.title });

  function setActive(link) {
    links.forEach(function (a) {
      var on = a === link;
      a.classList.toggle("active", on);
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
  }

  // Ids repeat from one topic to the next (c-mrr, filters...), so every loaded topic gets its own.
  function isolate(root, prefix) {
    root.querySelectorAll("[id]").forEach(function (el) { el.id = prefix + el.id; });
    ["data-chart", "for", "aria-controls", "aria-labelledby", "aria-describedby", "data-bs-target"].forEach(function (name) {
      root.querySelectorAll("[" + name + "]").forEach(function (el) {
        var v = el.getAttribute(name);
        if (name === "data-bs-target") el.setAttribute(name, v.charAt(0) === "#" ? "#" + prefix + v.slice(1) : v);
        else el.setAttribute(name, v.split(/\s+/).map(function (id) { return prefix + id; }).join(" "));
      });
    });
    root.querySelectorAll('a[href^="#"]').forEach(function (a) {
      if (a.getAttribute("href").length > 1) a.setAttribute("href", "#" + prefix + a.getAttribute("href").slice(1));
    });
  }

  var next = index + 1;

  function loadNext() {
    if (loading || next >= links.length) return;
    loading = true;
    var link = links[next];
    var name = link.textContent.trim();
    status.textContent = name + "…";
    fetch(link.href, { credentials: "same-origin", headers: { "X-Requested-With": "console-flow" } })
      .then(function (r) {
        if (!r.ok || r.redirected && r.url.indexOf(link.pathname) < 0) throw new Error("status " + r.status);
        return r.text();
      })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        var content = doc.getElementById("console-body");
        if (!content) throw new Error("no content");
        var heading = doc.querySelector(".page-head h1");
        var section = document.createElement("section");
        section.setAttribute("data-flow-section", "");
        section.className = "border-top pt-4 mt-4";
        var h = document.createElement("h2");
        h.className = "h4 mb-3";
        h.textContent = heading ? heading.textContent.trim() : name;
        section.appendChild(h);
        var wrap = document.createElement("div");
        wrap.innerHTML = content.innerHTML;
        wrap.querySelectorAll("script").forEach(function (s) {
          // json_script data stays (charts read it); code is never run from a fetched page.
          if (s.type !== "application/json") s.remove();
        });
        isolate(wrap, "f" + (++count) + "-");
        section.appendChild(wrap);
        flow.insertBefore(section, status);
        sections.push({ link: link, el: section, url: link.pathname + link.search, title: doc.title });
        spy.observe(section);
        if (window.drawCharts) window.drawCharts();
        next += 1;
        status.textContent = next >= links.length ? nav.getAttribute("data-end") || "" : "";
      })
      .catch(function () {
        status.innerHTML = "";
        var a = document.createElement("a");
        a.href = link.href;
        a.textContent = name;
        status.appendChild(a);
        links.splice(next, links.length);   // stop; the link is the way on
      })
      .then(function () { loading = false; });
  }

  var more = new IntersectionObserver(function (entries) {
    if (entries.some(function (e) { return e.isIntersecting; })) loadNext();
  }, { rootMargin: "0px 0px 600px 0px" });
  more.observe(end);

  // The topic crossing the upper part of the screen is the one the person is reading.
  var current = sections[0];
  var spy = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (!e.isIntersecting) return;
      var s = sections.find(function (x) { return x.el === e.target; });
      if (!s || s === current) return;
      current = s;
      setActive(s.link);
      document.title = s.title;
      try { history.replaceState(null, "", s.url); } catch (_) { /* address left as it was */ }
    });
  }, { rootMargin: "-15% 0px -80% 0px" });
  sections.forEach(function (s) { spy.observe(s.el); });

  nav.addEventListener("click", function (e) {
    var a = e.target.closest("a[data-flow]");
    if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.button) return;
    var s = sections.find(function (x) { return x.link === a; });
    if (!s) return;
    e.preventDefault();
    s.el.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  });
})();
