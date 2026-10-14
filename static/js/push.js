/* Alerts on this phone (D-089): asks the browser's permission, registers this device and turns it off again. */
(function () {
  "use strict";
  var card = document.querySelector("[data-push]");
  if (!card) return;
  var on = card.querySelector("[data-push-on]");
  var off = card.querySelector("[data-push-off]");
  var note = card.querySelector("[data-push-message]");

  function say(text) { note.textContent = text || ""; note.hidden = !text; }
  function show(state) { on.hidden = state === "on"; off.hidden = state !== "on"; card.dataset.state = state; }
  function cookie(name) {
    var found = document.cookie.split("; ").filter(function (c) { return c.indexOf(name + "=") === 0; })[0];
    return found ? decodeURIComponent(found.slice(name.length + 1)) : "";
  }
  function post(body) {
    return fetch(card.dataset.url, {
      method: "POST", credentials: "same-origin", redirect: "manual",
      headers: { "Content-Type": "application/json", "Accept": "application/json", "X-CSRFToken": cookie("csrftoken") },
      body: JSON.stringify(body)
    }).then(function (r) {
      if (!r.ok) throw new Error("refused");
      return r.json();
    });
  }
  function key() {
    var text = card.dataset.key.replace(/-/g, "+").replace(/_/g, "/");
    var raw = atob(text + "===".slice((text.length + 3) % 4));
    return Uint8Array.from(raw, function (c) { return c.charCodeAt(0); });
  }

  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
    on.hidden = off.hidden = true;
    say(card.dataset.unsupported);
    return;
  }
  var ready = navigator.serviceWorker.ready;
  // The server thinks it is on, but this browser dropped the registration: show it as off.
  ready.then(function (reg) { return reg.pushManager.getSubscription(); }).then(function (sub) {
    if (!sub && card.dataset.state === "on") show("off");
  });

  on.addEventListener("click", function () {
    on.disabled = true;
    say("");
    Notification.requestPermission().then(function (permission) {
      if (permission !== "granted") { say(card.dataset.denied); return; }
      return ready.then(function (reg) {
        return reg.pushManager.getSubscription().then(function (sub) {
          return sub || reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key() });
        });
      }).then(function (sub) {
        return post({ action: "on", subscription: sub.toJSON() });
      }).then(function () { show("on"); });
    }).catch(function () { say(card.dataset.failed); }).finally(function () { on.disabled = false; });
  });

  off.addEventListener("click", function () {
    off.disabled = true;
    ready.then(function (reg) { return reg.pushManager.getSubscription(); }).then(function (sub) {
      return post({ action: "off", endpoint: sub ? sub.endpoint : null }).then(function () {
        return sub ? sub.unsubscribe() : true;
      });
    }).then(function () { show("off"); say(""); }, function () { say(card.dataset.failed); })
      .finally(function () { off.disabled = false; });
  });
})();
