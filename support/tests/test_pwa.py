"""The installable app: manifest, service worker and offline page (D-080)."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from django.contrib.staticfiles import finders
from django.templatetags.static import static
from django.urls import reverse

from accounts.tests.factories import make_org, make_property
from accounts.tests.test_isolation import login
from support import pwa

pytestmark = pytest.mark.django_db


def worker_constants(text: str) -> dict:
    """The JSON constants at the top of sw.js."""
    found = dict(re.findall(r'^const (PRECACHE|OFFLINE_URL|STATIC_PREFIX) = (.+);$', text, re.M))
    return {name: json.loads(value) for name, value in found.items()}


def test_the_manifest_is_public_and_its_icons_exist(client):
    response = client.get("/manifest.webmanifest")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/manifest+json"
    data = json.loads(response.content)
    assert data["display"] == "standalone" and data["scope"] == "/" and data["start_url"] == "/?source=app"
    assert {icon["sizes"] for icon in data["icons"]} == {"192x192", "512x512"}
    assert any(icon["purpose"] == "maskable" for icon in data["icons"])
    for path, _sizes, _purpose in pwa.ICONS:
        assert finders.find(path), path
    assert finders.find("icons/apple-touch-icon.png")


def test_the_service_worker_is_at_the_root_and_never_cached(client):
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/javascript")
    assert response["Cache-Control"] == "no-cache" and response["Service-Worker-Allowed"] == "/"
    text = response.content.decode()
    constants = worker_constants(text)
    assert constants["OFFLINE_URL"] == reverse("support:offline")
    assert constants["STATIC_PREFIX"] == static("")
    assert constants["PRECACHE"][0] == reverse("support:offline")
    for path in pwa.PRECACHE_STATIC:
        assert static(path) in constants["PRECACHE"] and finders.find(path), path
    assert re.search(r'const CACHE = "lpms-[0-9a-f]{12}";', text)
    assert "{{" not in text and "{%" not in text


def test_pages_are_kept_only_from_a_short_list_and_never_payments_messages_or_the_assistant():
    """Static files and the offline page for everyone; read-only copies only of the listed signed-in staff pages."""
    text = pwa.service_worker()
    assert 'request.method !== "GET"' in text
    assert "url.pathname.startsWith(STATIC_PREFIX)" in text
    assert text.count("cache.put(") == 2  # static files, and the kept pages
    assert 'const LOGOUT_URL = "/logout/";' in text
    # The list of kept pages names no money, message, assistant, portal or report page.
    kept = text[text.index("const KEPT_PAGES"):text.index("].map(")]
    for word in ("payments", "billing", "mpesa", "messages", "ask", "/my/", "owner", "reports", "esign", "letters"):
        assert word not in kept, word


def test_the_service_worker_keeps_and_forgets_pages_as_it_should():
    """Runs the worker against a fake cache and network: what is kept, shown offline, and deleted at logout."""
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    result = subprocess.run(["node", str(Path(__file__).with_name("sw_behaviour.js"))], input=pwa.service_worker(),
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    outcomes = json.loads(result.stdout)
    assert len(outcomes) > 30
    assert {name: why for name, why in outcomes.items() if why is not True} == {}


def test_the_links_to_the_offline_forms_ask_for_them_to_be_kept_ahead(client):
    owner = make_org()
    make_property(owner.organization)
    login(client, owner)
    assert b"data-offline-keep" in client.get(reverse("maintenance:list")).content


def test_the_cache_version_follows_the_files(monkeypatch):
    before = pwa.service_worker()
    monkeypatch.setattr(pwa, "PRECACHE_STATIC", (*pwa.PRECACHE_STATIC, "icons/icon-512.png"))
    after = pwa.service_worker()
    version = re.compile(r'lpms-([0-9a-f]{12})')
    assert version.search(before).group(1) != version.search(after).group(1)
    assert version.search(pwa.service_worker()).group(1) == version.search(after).group(1)


def test_the_offline_page_holds_no_personal_data(client):
    owner = make_org(name="Acme Lettings")
    login(client, owner)
    response = client.get(reverse("support:offline"))
    assert response.status_code == 200
    assert b"You are offline" in response.content
    assert b"Acme Lettings" not in response.content
    assert owner.user.phone.encode() not in response.content
    assert b"Log out" not in response.content
    client.logout()
    assert client.get(reverse("support:offline")).status_code == 200


def test_every_page_links_the_manifest_and_registers_the_worker(client):
    owner = make_org()
    login(client, owner)
    page = client.get(reverse("support:help")).content.decode()
    assert f'rel="manifest" href="{reverse("support:manifest")}"' in page
    assert f'data-sw="{reverse("support:sw")}"' in page
    assert "data-install-app hidden" in page
    assert static("icons/apple-touch-icon.png") in page
    help_page = client.get(reverse("support:help_topic", args=["getting-started"])).content
    assert b"Add to Home Screen" in help_page
