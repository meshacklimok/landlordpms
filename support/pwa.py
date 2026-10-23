"""The installable app: manifest, service worker and offline page (D-080).

The service worker caches static files and the offline page only, never pages or data.
"""

import hashlib
import json

from django.contrib.staticfiles import finders
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.template.loader import get_template
from django.templatetags.static import static
from django.urls import reverse
from django.views import View

NAME = "NyumbaPMS"
THEME_COLOR = "#ffffff"
BACKGROUND_COLOR = "#f3f5f2"
START_URL = "/?source=app"
# Local static files every page needs, fetched when the app is installed.
PRECACHE_STATIC = ("vendor/bootstrap-5.3.3/bootstrap.min.css", "vendor/bootstrap-5.3.3/bootstrap.bundle.min.js",
                   "vendor/bootstrap-icons-1.11.3/bootstrap-icons.min.css",
                   "vendor/bootstrap-icons-1.11.3/fonts/bootstrap-icons.woff2",
                   "css/app.css", "fonts/inter-latin-wght-normal.woff2", "js/theme.js", "js/pwa.js", "js/offline.js", "js/ui.js",
                   "icons/icon-192.png", "icons/favicon-32.png")
# Bump when the icon files change: the icons are cached for 30 days at the same address, so phones keep the old one.
ICON_VERSION = "2"
ICONS = (
    ("icons/icon-192.png", "192x192", "any"),
    ("icons/icon-512.png", "512x512", "any"),
    ("icons/maskable-512.png", "512x512", "maskable"),
)


def manifest() -> dict:
    return {
        "name": NAME,
        "short_name": NAME,
        "description": "Rent, tenants and payments for Kenyan landlords.",
        "id": "/",
        "start_url": START_URL,
        "scope": "/",
        "display": "standalone",
        "theme_color": THEME_COLOR,
        "background_color": BACKGROUND_COLOR,
        "icons": [{"src": f"{static(path)}?v={ICON_VERSION}", "sizes": sizes, "type": "image/png", "purpose": purpose}
                  for path, sizes, purpose in ICONS],
    }


def precache_urls() -> list[str]:
    return [reverse("support:offline"), *(static(path) for path in PRECACHE_STATIC)]


def _version(source: str, urls: list[str]) -> str:
    """Changes when the worker, the URLs it lists or those files' contents change, so a deploy replaces the cache."""
    digest = hashlib.sha256(source.encode())
    digest.update("\n".join(urls).encode())
    for path in PRECACHE_STATIC:
        found = finders.find(path)
        if found:
            with open(found, "rb") as f:
                digest.update(f.read())
    digest.update(get_template("support/offline.html").template.source.encode())
    return digest.hexdigest()[:12]


def service_worker() -> str:
    template = get_template("support/sw.js")
    urls = precache_urls()
    return template.render({
        "version": _version(template.template.source, urls),
        "precache": json.dumps(urls),
        "offline_url": json.dumps(reverse("support:offline")),
        "static_prefix": json.dumps(static("")),
    })


class ManifestView(View):
    def get(self, request):
        response = JsonResponse(manifest(), content_type="application/manifest+json")
        response["Cache-Control"] = "public, max-age=3600"
        return response


class ServiceWorkerView(View):
    """At the root so its scope is the whole site. Not cached, so a new version is seen on the next visit."""

    def get(self, request):
        response = HttpResponse(service_worker(), content_type="application/javascript; charset=utf-8")
        response["Cache-Control"] = "no-cache"
        response["Service-Worker-Allowed"] = "/"
        return response


class OfflineView(View):
    """Shown by the service worker when a page cannot load. Standalone: no names, balances or menus."""

    def get(self, request):
        response = render(request, "support/offline.html", {"name": NAME, "theme_color": THEME_COLOR})
        response["Cache-Control"] = "no-cache"
        return response
