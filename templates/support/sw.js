/* NyumbaPMS service worker (D-080). Caches static files and the offline page only:
   pages, data, PDFs and uploads always come from the network and are never stored. */
const CACHE = "lpms-{{ version }}";
const PRECACHE = {{ precache|safe }};
const OFFLINE_URL = {{ offline_url|safe }};
const STATIC_PREFIX = {{ static_prefix|safe }};

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

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  if (request.mode === "navigate") {
    // Pages: the network only. With no connection, the offline page.
    event.respondWith(fetch(request).catch(() => caches.match(OFFLINE_URL)));
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
