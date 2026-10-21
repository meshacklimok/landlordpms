/* Installable app (D-080): registers the service worker and offers "Install the app" where the browser allows it. */
(function () {
  "use strict";
  var script = document.currentScript;
  var workerUrl = script && script.dataset.sw;
  if (workerUrl && "serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register(workerUrl, { scope: "/" }).catch(function () { /* the site works without it */ });
    });
  }

  var deferred = null;
  function buttons() { return document.querySelectorAll("[data-install-app]"); }
  function show(on) { buttons().forEach(function (el) { el.hidden = !on; }); }

  window.addEventListener("beforeinstallprompt", function (event) {
    event.preventDefault();
    deferred = event;
    show(true);
  });
  window.addEventListener("appinstalled", function () { deferred = null; show(false); });
  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-install-app]");
    if (!button || !deferred) return;
    event.preventDefault();
    deferred.prompt();
    deferred.userChoice.finally(function () { deferred = null; show(false); });
  });
})();
