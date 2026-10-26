// Runs the generated service worker (source on stdin) against fake caches and a fake network.
// Prints one JSON object {name: true|false-with-reason}; the pytest wrapper fails on any false.
const vm = require("vm");
const fs = require("fs");

const source = fs.readFileSync(0, "utf8");
const ORIGIN = "https://app.test";
const ID = "11111111-2222-3333-4444-555555555555";
const OFFLINE = "OFFLINE-PAGE";

function makeWorld() {
  const stores = new Map();
  const listeners = {};
  const state = { online: true, response: null, requests: [], now: Date.now() };
  const keyOf = (r) => (typeof r === "string" ? new URL(r, ORIGIN).href : r.url);
  const caches = {
    async open(name) {
      if (!stores.has(name)) stores.set(name, new Map());
      const store = stores.get(name);
      return {
        async match(r) { return store.get(keyOf(r)); },
        async put(r, res) { store.set(keyOf(r), res); },
        async delete(r) { return store.delete(keyOf(r)); },
        async addAll() {},
      };
    },
    async match(r) {
      for (const store of stores.values()) if (store.has(keyOf(r))) return store.get(keyOf(r));
      return undefined;
    },
    async keys() { return [...stores.keys()]; },
    async delete(name) { return stores.delete(name); },
  };
  stores.set("lpms-old", new Map());
  stores.get("lpms-old").set(ORIGIN + "/offline/", new Response(OFFLINE, { headers: { "Content-Type": "text/html" } }));
  const self = {
    location: { origin: ORIGIN },
    addEventListener(type, fn) { listeners[type] = fn; },
    skipWaiting() {}, clients: { claim() {}, matchAll: async () => [], openWindow() {} },
    registration: { showNotification() {} },
  };
  const context = {
    self, caches, URL, Response, Headers, Promise, Date: { now: () => state.now }, String, Number, Array, RegExp,
    setTimeout, clearTimeout, Error,
    fetch: async (target) => {
      state.requests.push(typeof target === "string" ? target : target.url);
      if (!state.online) throw new Error("offline");
      return state.response();
    },
  };
  // Date needs to be constructible too (new Date(ms)).
  context.Date = Object.assign(function (...args) { return new Date(...args); }, { now: () => state.now });
  vm.createContext(context);
  vm.runInContext(source, context);
  const dispatch = async (type, event) => {
    let waited = [], responded = null;
    event.waitUntil = (p) => waited.push(Promise.resolve(p));
    event.respondWith = (p) => { responded = Promise.resolve(p); };
    listeners[type](event);
    await Promise.all(waited);
    return responded ? await responded : undefined;
  };
  return { state, stores, dispatch };
}

function page(body, { priv = true, redirected = false, status = 200 } = {}) {
  const headers = { "Content-Type": "text/html; charset=utf-8" };
  if (priv) headers["Cache-Control"] = "private, no-store";
  const res = new Response(body, { status, headers });
  if (redirected) Object.defineProperty(res, "redirected", { value: true });
  return res;
}
const nav = (path, method = "GET") => ({ request: { method, url: ORIGIN + path, mode: "navigate" } });

(async () => {
  const out = {};
  const check = (name, ok, why = "") => { out[name] = ok ? true : "failed " + why; };

  // 1. A signed-in staff page is kept, then shown with a notice when the network is gone.
  {
    const w = makeWorld();
    w.state.response = () => page("<html><body class='x'><h1>Tenants</h1></body></html>");
    const online = await w.dispatch("fetch", nav("/tenants/"));
    check("online page is passed through", (await online.text()).includes("<h1>Tenants</h1>"));
    await new Promise((r) => setTimeout(r, 20));
    w.state.online = false;
    const off = await w.dispatch("fetch", nav("/tenants/"));
    const text = await off.text();
    check("offline shows kept copy", text.includes("<h1>Tenants</h1>"), text);
    check("notice follows the body tag", /<body data-offline-copy class='x'><div class="alert alert-warning[^>]*role="status"[^>]*><strong>No connection/.test(text), text);
    // 2. Logging out deletes the copies and lets the request through.
    const logoutEvent = nav("/logout/", "POST");
    const handled = await w.dispatch("fetch", logoutEvent);
    check("logout is not intercepted", handled === undefined);
    const after = await w.dispatch("fetch", nav("/tenants/"));
    check("after logout the offline page is shown", (await after.text()) === OFFLINE);
  }

  // 3. A redirect to the login page, a public page or an error is never kept.
  for (const [name, make] of [
    ["redirect", () => page("login", { redirected: true })],
    ["public page", () => page("landing", { priv: false })],
    ["error page", () => page("boom", { status: 500 })],
  ]) {
    const w = makeWorld();
    w.state.response = make;
    await w.dispatch("fetch", nav("/"));
    await new Promise((r) => setTimeout(r, 20));
    w.state.online = false;
    const off = await w.dispatch("fetch", nav("/"));
    check(name + " is not kept", (await off.text()) === OFFLINE);
  }

  // 4. Pages off the list are never kept: payments, edit forms, the assistant, other people's portals.
  for (const path of ["/payments/", "/payments/" + ID + "/", "/tenants/" + ID + "/edit/", "/tenants/new/", "/ask/",
                      "/my/", "/messages/", "/billing/", "/mpesa/", "/leases/" + ID + "/agreement/", "/reports/"]) {
    const w = makeWorld();
    w.state.response = () => page("<body>secret</body>");
    await w.dispatch("fetch", nav(path));
    await new Promise((r) => setTimeout(r, 20));
    w.state.online = false;
    const off = await w.dispatch("fetch", nav(path));
    check(path + " is not kept", (await off.text()) === OFFLINE);
  }

  // 5. Pages on the list are kept.
  for (const path of ["/", "/?source=app", "/properties/", "/properties/" + ID + "/", "/properties/units/",
                      "/properties/units/" + ID + "/", "/tenants/" + ID + "/", "/leases/", "/leases/" + ID + "/",
                      "/meters/", "/meters/round/" + ID + "/", "/meters/round/" + ID + "/?utility=WATER",
                      "/maintenance/", "/maintenance/new/", "/maintenance/" + ID + "/"]) {
    const w = makeWorld();
    w.state.response = () => page("<body>ok</body>");
    await w.dispatch("fetch", nav(path));
    await new Promise((r) => setTimeout(r, 20));
    w.state.online = false;
    const off = await w.dispatch("fetch", nav(path));
    check(path + " is kept", (await off.text()).includes("ok</body>") || (await off.text()).includes("ok"));
  }

  // 6. A copy older than a week is not shown.
  {
    const w = makeWorld();
    w.state.response = () => page("<body>old</body>");
    await w.dispatch("fetch", nav("/tenants/"));
    await new Promise((r) => setTimeout(r, 20));
    w.state.now += 8 * 24 * 60 * 60 * 1000;
    w.state.online = false;
    const off = await w.dispatch("fetch", nav("/tenants/"));
    check("a copy older than 7 days is dropped", (await off.text()) === OFFLINE);
  }

  // 7. The page can ask for forms to be kept ahead; reset clears the previous person's copies first.
  {
    const w = makeWorld();
    w.state.response = () => page("<body>tenants</body>");
    await w.dispatch("fetch", nav("/tenants/"));
    await new Promise((r) => setTimeout(r, 20));
    w.state.response = () => page("<body>form</body>");
    await w.dispatch("message", { data: { type: "keep-pages", reset: true,
      urls: ["/meters/round/" + ID + "/", "/payments/", "https://evil.test/meters/", "/tenants/new/"] } });
    check("only listed same-origin pages are fetched ahead",
      JSON.stringify(w.state.requests.slice(-1)) === JSON.stringify([ORIGIN + "/meters/round/" + ID + "/"]), JSON.stringify(w.state.requests));
    w.state.online = false;
    check("the form was kept ahead", (await (await w.dispatch("fetch", nav("/meters/round/" + ID + "/"))).text()).includes("form"));
    check("reset cleared the earlier copy", (await (await w.dispatch("fetch", nav("/tenants/"))).text()) === OFFLINE);
  }

  // 7b. A page that was being fetched when the person logged out is not kept afterwards.
  {
    const w = makeWorld();
    let release;
    w.state.response = () => new Promise((resolve) => { release = () => resolve(page("<body>late</body>")); });
    const pending = w.dispatch("message", { data: { type: "keep-pages", urls: ["/maintenance/new/"] } });
    await new Promise((r) => setTimeout(r, 5));
    await new Promise((r) => setTimeout(r, 5));
    await w.dispatch("fetch", nav("/logout/", "POST"));
    release();
    await pending;
    w.state.online = false;
    check("a page fetched before logout is not kept after it", (await (await w.dispatch("fetch", nav("/maintenance/new/"))).text()) === OFFLINE);
  }

  // 8. Offline with nothing kept, and a page that never loads: the offline page, not an error.
  {
    const w = makeWorld();
    w.state.online = false;
    check("nothing kept gives the offline page", (await (await w.dispatch("fetch", nav("/tenants/"))).text()) === OFFLINE);
  }

  console.log(JSON.stringify(out));
})().catch((e) => { console.log(JSON.stringify({ harness: String(e && e.stack || e) })); });
