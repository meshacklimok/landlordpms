/* NyumbaPMS service worker (D-080, D-218). Static files and the offline page are cached for everyone.
   A short list of staff pages (the forms that work offline, and the lists and details a caretaker looks things up in)
   is also kept as a read-only copy, so it opens with no signal. Payments, messages, the assistant, PDFs, uploads and
   every other page always come from the network and are never stored. The copies are deleted when the person logs out. */
const CACHE = "lpms-{{ version }}";
const PRECACHE = {{ precache|safe }};
const OFFLINE_URL = {{ offline_url|safe }};
const STATIC_PREFIX = {{ static_prefix|safe }};
const LOGOUT_URL = {{ logout_url|safe }};

// Read-only copies of pages (not named "lpms-", so a new version of this worker does not delete them).
const PAGES = "lpmspages-1";
const PAGE_MAX_AGE = 7 * 24 * 60 * 60 * 1000;
const NETWORK_WAIT = 8000;  // with a copy kept, a slow connection is treated as offline after this long
const ID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const KEPT_PAGES = [
  "^/$",
  "^/meters/round/" + ID + "/$", "^/maintenance/new/$",  // forms that keep what is typed on the phone (offline.js)
  "^/properties/$", "^/properties/" + ID + "/$", "^/properties/units/$", "^/properties/units/" + ID + "/$",
  "^/tenants/$", "^/tenants/" + ID + "/$",
  "^/leases/$", "^/leases/" + ID + "/$",
  "^/meters/$", "^/maintenance/$", "^/maintenance/" + ID + "/$",
].map((pattern) => new RegExp(pattern));

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("lpms-") && k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function isStatic(url) {
  return url.origin === self.location.origin && url.pathname.startsWith(STATIC_PREFIX);
}

function pageKey(url) {
  // The app opens at /?source=app: one copy of the home page.
  return url.origin + (url.pathname === "/" ? "/" : url.pathname + url.search);
}

function isKept(url) {
  return url.origin === self.location.origin && KEPT_PAGES.some((pattern) => pattern.test(url.pathname));
}

// Only a signed-in page is kept: private (the server marks these), not a redirect to the login page, an error or
// anything but HTML. The home page is also the public landing page, which is not kept.
function keepable(response) {
  return response.ok && !response.redirected && response.status === 200
    && (response.headers.get("Cache-Control") || "").includes("private")
    && (response.headers.get("Content-Type") || "").startsWith("text/html");
}

// When the last logout was seen. A page whose request started before it was fetched while signed in as someone
// who has now gone, so it is not kept.
let loggedOutAt = 0;

async function keepPage(url, response, startedAt) {
  if (startedAt <= loggedOutAt) return;
  const body = await response.clone().blob();
  const headers = new Headers({ "Content-Type": response.headers.get("Content-Type"), "X-Kept-At": String(Date.now()) });
  const cache = await caches.open(PAGES);
  await cache.put(pageKey(url), new Response(body, { status: 200, headers }));
}

function escapeHtml(text) {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// The kept copy, with a notice at the top saying so. Null when there is none or it is too old.
async function keptCopy(url) {
  const cache = await caches.open(PAGES);
  const hit = await cache.match(pageKey(url));
  if (!hit) return null;
  const keptAt = Number(hit.headers.get("X-Kept-At")) || 0;
  if (Date.now() - keptAt > PAGE_MAX_AGE) { await cache.delete(pageKey(url)); return null; }
  const when = escapeHtml(new Date(keptAt).toLocaleString());
  const notice = '<div class="alert alert-warning rounded-0 border-0 mb-0 py-2 small text-center" role="status" data-offline-notice>'
    + "<strong>No connection.</strong> This is the copy saved on " + when + ". "
    + "It may be out of date. Faded items need the internet.</div>";
  // data-offline-copy tells the page's own scripts it is a saved copy: they fade what needs a connection.
  const html = (await hit.text()).replace(/<body/i, "<body data-offline-copy").replace(/<body[^>]*>/i, (tag) => tag + notice);
  return new Response(html, { status: 200, headers: { "Content-Type": hit.headers.get("Content-Type") } });
}

async function openPage(event, url) {
  const request = event.request;
  const kept = isKept(url);
  const startedAt = Date.now();
  let timer = null;
  const network = fetch(request);
  // A slow connection counts as offline after a while, but only where there is a copy to show instead.
  const slow = kept ? new Promise((resolve, reject) => { timer = setTimeout(() => reject(new Error("slow")), NETWORK_WAIT); }) : null;
  try {
    const response = await (slow ? Promise.race([network, slow]) : network);
    // Keeping the copy must not hold the page back.
    if (kept && keepable(response)) event.waitUntil(keepPage(url, response, startedAt).catch(() => undefined));
    return response;
  } catch (error) {
    const copy = kept ? await keptCopy(url).catch(() => null) : null;
    if (copy) { network.catch(() => undefined); return copy; }
    return caches.match(OFFLINE_URL);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

// The page asks for the pages it links to (the meter round, the repair form) to be kept before the signal is lost.
self.addEventListener("message", (event) => {
  const data = event.data || {};
  if (data.type !== "keep-pages" || !Array.isArray(data.urls)) return;
  event.waitUntil((async () => {
    // reset: another person signed in on this phone; their predecessor's copies go first.
    if (data.reset) await caches.delete(PAGES);
    await Promise.all(data.urls.map(async (href) => {
      const url = new URL(String(href), self.location.origin);
      if (!isKept(url)) return;
      const startedAt = Date.now();
      const response = await fetch(url.href, { credentials: "same-origin" }).catch(() => null);
      if (response && keepable(response)) await keepPage(url, response, startedAt).catch(() => undefined);
    }));
  })());
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method === "POST" && new URL(request.url).pathname === LOGOUT_URL) {
    // Logging out deletes the kept copies of pages; the request itself goes on to the server as usual.
    loggedOutAt = Date.now();
    event.waitUntil(caches.delete(PAGES));
    return;
  }
  if (request.method !== "GET") return;
  if (request.mode === "navigate") {
    // Pages: the network first. With no connection, a kept copy of a few staff pages, otherwise the offline page.
    event.respondWith(openPage(event, new URL(request.url)));
    return;
  }
  const url = new URL(request.url);
  if (!isStatic(url)) return;
  // Static files: answer from the cache at once and refresh it in the background.
  event.respondWith(caches.open(CACHE).then((cache) => cache.match(request).then((hit) => {
    const fresh = fetch(request).then((response) => {
      if (response.ok || response.type === "opaque") cache.put(request, response.clone());
      return response;
    });
    if (hit) {
      event.waitUntil(fresh.catch(() => undefined));
      return hit;
    }
    return fresh;
  })));
});

// Alerts on staff phones (D-089): the push carries only a title, a short text and a link on this site.
self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = {}; }
  event.waitUntil(self.registration.showNotification(data.title || "NyumbaPMS", {
    body: data.body || "", tag: data.tag || undefined, icon: PRECACHE.find((u) => u.endsWith("icon-192.png")),
    data: { url: data.url || "/" },
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL((event.notification.data && event.notification.data.url) || "/", self.location.origin);
  if (target.origin !== self.location.origin) return;
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
    const open = windows.find((w) => new URL(w.url).origin === self.location.origin && "focus" in w);
    if (open) return open.navigate(target.href).then((w) => (w || open).focus());
    return self.clients.openWindow(target.href);
  }));
});
