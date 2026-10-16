/* Reading pages: builds a contents list from the headings in .doc, marks the one being read, gives each heading a link
   you can copy, and shows a thin reading bar. Everything here is extra: the page reads fine without it. */
(function () {
  "use strict";
  var doc = document.querySelector(".doc");
  if (!doc) return;
  var layout = doc.closest(".doc-layout");
  var heads = Array.prototype.slice.call(doc.querySelectorAll("h2"));
  var tocLabel = (layout && layout.getAttribute("data-toc-label")) || "On this page";
  var linkLabel = (layout && layout.getAttribute("data-link-label")) || "Link to this section";

  function slug(text, used) {
    var base = text.toLowerCase().replace(/^\s*\d+[.)]\s*/, "").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "section";
    var id = base, n = 2;
    while (used[id] || document.getElementById(id)) { id = base + "-" + n++; }
    used[id] = true;
    return id;
  }

  var used = {};
  heads.forEach(function (h) {
    if (!h.id) h.id = slug(h.textContent, used);
    var a = document.createElement("a");
    a.className = "doc-anchor";
    a.href = "#" + h.id;
    a.setAttribute("aria-label", linkLabel);
    a.innerHTML = '<i class="bi bi-link-45deg" aria-hidden="true"></i>';
    h.appendChild(a);
  });

  var links = [];
  if (layout && heads.length >= 4) {
    var build = function (track) {
      var ol = document.createElement("ol");
      heads.forEach(function (h) {
        var li = document.createElement("li");
        var a = document.createElement("a");
        a.href = "#" + h.id;
        a.textContent = h.firstChild.textContent.trim();
        li.appendChild(a);
        ol.appendChild(li);
        if (track) links.push({ a: a, h: h });
      });
      return ol;
    };
    var side = document.createElement("nav");
    side.className = "doc-toc";
    side.setAttribute("aria-label", tocLabel);
    var title = document.createElement("p");
    title.className = "doc-toc-h";
    title.textContent = tocLabel;
    side.appendChild(title);
    side.appendChild(build(true));
    var small = document.createElement("details");
    small.className = "doc-toc-mobile";
    var summary = document.createElement("summary");
    summary.textContent = tocLabel;
    small.appendChild(summary);
    small.appendChild(build(false));
    layout.insertBefore(side, layout.firstChild);
    layout.classList.add("has-toc");
    layout.parentNode.insertBefore(small, layout);
    small.addEventListener("click", function (e) { if (e.target.closest("a")) small.open = false; });
  }

  var bar = document.createElement("div");
  bar.className = "doc-read-bar";
  bar.setAttribute("aria-hidden", "true");
  document.body.appendChild(bar);

  var ticking = false;
  function update() {
    ticking = false;
    var max = document.documentElement.scrollHeight - window.innerHeight;
    bar.style.transform = "scaleX(" + (max > 0 ? Math.min(1, Math.max(0, window.scrollY / max)) : 0) + ")";
    if (!links.length) return;
    var current = null, line = window.innerHeight * 0.3;
    links.forEach(function (l) { if (l.h.getBoundingClientRect().top <= line) current = l; });
    links.forEach(function (l) {
      var on = l === current;
      l.a.classList.toggle("is-current", on);
      if (on) l.a.setAttribute("aria-current", "location"); else l.a.removeAttribute("aria-current");
    });
  }
  window.addEventListener("scroll", function () { if (!ticking) { ticking = true; window.requestAnimationFrame(update); } }, { passive: true });
  window.addEventListener("resize", update);
  update();

  // Pressing a heading's link icon also copies its address.
  doc.addEventListener("click", function (e) {
    var a = e.target.closest(".doc-anchor");
    if (!a || !navigator.clipboard) return;
    navigator.clipboard.writeText(location.origin + location.pathname + a.getAttribute("href")).catch(function () {});
  });
})();
