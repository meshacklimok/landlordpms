"""Security batch one (D-107): Content Security Policy, session limits, per-IP limits and password hashing."""

import json
import logging
import re
import time
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse

from accounts import views as account_views
from accounts.models import User
from accounts.tests.factories import PASSWORD, make_org, make_user
from core import sessions

pytestmark = pytest.mark.django_db

TEMPLATES = Path(settings.BASE_DIR) / "templates"
HANDLER = re.compile(r"<[^>]*\son[a-z]+\s*=", re.IGNORECASE)
INLINE_SCRIPT = re.compile(r"<script\b(?![^>]*\bsrc=)(?![^>]*application/(?:ld\+)?json)[^>]*>", re.IGNORECASE)


def _directives(header: str) -> dict[str, list[str]]:
    parts = (d.strip().split() for d in header.split(";") if d.strip())
    return {p[0]: p[1:] for p in parts}


# ---------------------------------------------------------------------------
# Content Security Policy
# ---------------------------------------------------------------------------


def test_every_page_carries_the_policy(client):
    policy = _directives(client.get(reverse("accounts:login"))["Content-Security-Policy"])
    assert policy["default-src"] == ["'self'"]
    assert "'unsafe-inline'" not in policy["script-src"] and "'unsafe-eval'" not in policy["script-src"]
    # Scripts, styles and fonts are our own files: no CDN can break or change a page.
    assert policy["script-src"] == ["'self'"] and policy["font-src"] == ["'self'"]
    assert policy["style-src"] == ["'self'", "'unsafe-inline'"]
    assert policy["frame-ancestors"] == ["'none'"] and policy["object-src"] == ["'none'"]
    assert policy["report-uri"] == [reverse("csp_report")]


def test_templates_have_no_inline_scripts_or_event_handlers():
    """The policy would block them (D-107): behaviour lives in static/js, asked for with data- attributes."""
    found = []
    for path in TEMPLATES.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        found += [f"{path.relative_to(TEMPLATES)}: {m.group(0)[:60]}" for m in HANDLER.finditer(text)]
        found += [f"{path.relative_to(TEMPLATES)}: {m.group(0)}" for m in INLINE_SCRIPT.finditer(text)]
    assert found == []


def test_templates_load_no_scripts_or_styles_from_other_sites():
    """Bootstrap, its icons and Chart.js live in static/vendor: a page never depends on a CDN being reachable."""
    found = []
    for path in TEMPLATES.rglob("*.html"):
        found += [f"{path.relative_to(TEMPLATES)}: {tag[:80]}" for tag in
                  re.findall(r"<(?:script|link)\b[^>]*(?:src|href)=\"(?:https?:)?//[^>]*>", path.read_text(encoding="utf-8"))]
    assert found == []


def test_a_report_is_logged_without_query_strings_or_code(client, caplog):
    report = {"csp-report": {
        "document-uri": "https://app.example.co.ke/p/secret-token/?pin=1234",
        "effective-directive": "script-src-elem", "blocked-uri": "https://evil.example/x.js?k=1",
        "source-file": "https://app.example.co.ke/static/js/ui.js", "line-number": 12, "script-sample": "alert(1)",
    }}
    with caplog.at_level(logging.WARNING, logger="django.security.csp"):
        response = client.post(reverse("csp_report"), json.dumps(report), content_type="application/csp-report")
    assert response.status_code == 204
    [line] = caplog.messages
    assert "https://evil.example/x.js on /p/secret-token/ (rule script-src-elem" in line
    assert "pin=" not in line and "k=1" not in line and "alert" not in line


def test_reporting_api_lists_are_read_too(client, caplog):
    body = [{"type": "csp-violation", "body": {"documentURL": "https://a.example/home/", "blockedURL": "inline",
                                               "effectiveDirective": "script-src-attr"}},
            {"type": "deprecation", "body": {}}]
    with caplog.at_level(logging.WARNING, logger="django.security.csp"):
        client.post(reverse("csp_report"), json.dumps(body), content_type="application/reports+json")
    assert caplog.messages == ["CSP blocked inline on /home/ (rule script-src-attr, from :)"]


def test_junk_large_and_too_many_reports_are_dropped_quietly(client, caplog):
    one = json.dumps({"csp-report": {"document-uri": "https://a.example/", "blocked-uri": "eval"}})
    with caplog.at_level(logging.WARNING, logger="django.security.csp"):
        assert client.post(reverse("csp_report"), "not json", content_type="application/json").status_code == 204
        big = json.dumps({"csp-report": {"document-uri": "x" * 20000}})
        assert client.post(reverse("csp_report"), big, content_type="application/json").status_code == 204
        for _ in range(25):
            client.post(reverse("csp_report"), one, content_type="application/json")
    assert len(caplog.messages) == 19  # the junk post counted towards the 20 a minute too
    assert client.get(reverse("csp_report")).status_code == 405


# ---------------------------------------------------------------------------
# Session limits
# ---------------------------------------------------------------------------


def _logged_in(client, user, *, idle=0, age=0) -> Client:
    client.force_login(user)
    session = client.session
    now = int(time.time())
    session[sessions.STARTED] = now - age
    session[sessions.SEEN] = now - idle
    session.save()
    return client


def _still_in(client) -> bool:
    return "_auth_user_id" in client.session


def test_staff_are_logged_out_after_an_hour_idle(client):
    owner = make_org().user
    response = _logged_in(client, owner, idle=61 * 60).get(reverse("accounts:staff"))
    assert response.status_code == 302 and "/login/" in response["Location"]
    assert not _still_in(client)


def test_staff_logins_end_after_twelve_hours_however_busy(client):
    owner = make_org().user
    response = _logged_in(client, owner, age=12 * 3600 + 60).get(reverse("accounts:staff"))
    assert response.status_code == 302 and not _still_in(client)


def test_an_active_staff_session_carries_on_and_is_touched(client):
    owner = make_org().user
    assert _logged_in(client, owner, idle=5 * 60, age=3600).get(reverse("accounts:home")).status_code == 200
    assert _still_in(client)
    assert int(time.time()) - client.session[sessions.SEEN] < 5


def test_tenants_stay_logged_in_for_days(client):
    tenant = make_user()
    response = _logged_in(client, tenant, idle=3 * 24 * 3600, age=10 * 24 * 3600).get(reverse("accounts:security"))
    assert response.status_code == 200
    assert _still_in(client)
    _logged_in(client, tenant, idle=15 * 24 * 3600).get(reverse("accounts:security"))
    assert not _still_in(client)


def test_a_session_from_before_the_limits_counts_from_now(client):
    owner = make_org().user
    client.force_login(owner)
    session = client.session
    del session[sessions.STARTED], session[sessions.SEEN]
    session.save()
    assert client.get(reverse("accounts:home")).status_code == 200
    assert sessions.STARTED in client.session


def test_session_limits_come_from_settings(settings, client):
    settings.SESSION_IDLE_MINUTES = 5
    owner = make_org().user
    _logged_in(client, owner, idle=6 * 60).get(reverse("accounts:home"))
    assert not _still_in(client)


# ---------------------------------------------------------------------------
# Per-IP limits on sign-up and password reset
# ---------------------------------------------------------------------------


def _sign_up(n):
    return Client().post(reverse("accounts:register"), {
        "full_name": f"Wanjiku {n}", "phone": f"07120000{n:02d}",
        "email": f"wanjiku{n}@example.com", "password": "Strong-Pass-472", "accept_terms": "on"})


def test_sign_ups_are_limited_per_ip(monkeypatch):
    monkeypatch.setattr(account_views, "SIGNUP_PER_IP_HOUR", 2)
    assert [_sign_up(n).status_code for n in (1, 2)] == [302, 302]
    response = _sign_up(3)
    assert response.status_code == 200 and "Too many tries from this network" in response.content.decode()
    assert not User.objects.filter(full_name="Wanjiku 3").exists()


def test_reset_codes_are_limited_per_ip(monkeypatch, client, outbox):
    monkeypatch.setattr(account_views, "RESET_PER_IP_HOUR", 1)
    user = make_user(phone="+254712000077")
    assert client.post(reverse("accounts:password_reset"), {"identifier": "0712000077"}).status_code == 302
    sent = len(outbox)
    response = Client().post(reverse("accounts:password_reset"), {"identifier": user.phone})
    assert "Too many tries from this network" in response.content.decode()
    assert len(outbox) == sent


def test_a_wrong_form_does_not_use_up_the_limit(monkeypatch):
    monkeypatch.setattr(account_views, "SIGNUP_PER_IP_HOUR", 1)
    Client().post(reverse("accounts:register"), {"full_name": "No phone", "password": PASSWORD})
    assert _sign_up(4).status_code == 302


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def test_new_passwords_are_hashed_with_argon2_and_old_ones_still_work():
    from config.settings import base

    assert base.PASSWORD_HASHERS[0] == "django.contrib.auth.hashers.Argon2PasswordHasher"
    assert "django.contrib.auth.hashers.PBKDF2PasswordHasher" in base.PASSWORD_HASHERS
    import argon2  # noqa: F401 - installed from requirements.txt


def test_an_old_hash_is_upgraded_at_login(settings):
    from django.contrib.auth.hashers import make_password

    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.Argon2PasswordHasher",
                                 "django.contrib.auth.hashers.PBKDF2PasswordHasher"]
    user = make_user()
    user.password = make_password(PASSWORD, hasher="pbkdf2_sha256")
    user.save(update_fields=["password"])
    assert user.check_password(PASSWORD)
    user.refresh_from_db()
    assert user.password.startswith("argon2")


# ---------------------------------------------------------------------------
# Security batch three (D-139): response headers and request size limits
# ---------------------------------------------------------------------------


def test_pages_switch_off_browser_features_we_do_not_use(client):
    response = client.get(reverse("accounts:login"))
    policy = response["Permissions-Policy"]
    assert "camera=()" in policy and "microphone=()" in policy and "geolocation=()" in policy
    assert response["Cross-Origin-Resource-Policy"] == "same-origin"
    assert "no-store" not in response.get("Cache-Control", "")  # a visitor's page may be kept


def test_signed_in_pages_are_not_stored_by_the_browser(client):
    client.force_login(make_org().user)
    response = client.get(reverse("accounts:staff"))
    assert response["Cache-Control"] == "private, no-store"


def test_a_page_that_chose_its_own_cache_rule_keeps_it():
    from django.http import HttpResponse
    from django.test import RequestFactory

    from core.headers import ExtraSecurityHeadersMiddleware

    def view(request):
        response = HttpResponse("x")
        response["Cache-Control"] = "max-age=60"
        return response

    request = RequestFactory().get("/")
    request.user = make_user()
    response = ExtraSecurityHeadersMiddleware(view)(request)
    assert response["Cache-Control"] == "max-age=60" and "Permissions-Policy" in response


def test_a_huge_form_is_refused(client):
    response = client.post(reverse("accounts:login"), {f"f{i}": "x" for i in range(1500)})
    assert response.status_code == 400


def test_deploy_files_carry_the_edge_defences():
    root = Path(settings.BASE_DIR) / "deploy"
    nginx = (root / "nginx.conf").read_text()
    assert "limit_req_zone" in nginx and "client_header_timeout" in nginx and "server_tokens off" in nginx
    service = (root / "landlordpms.service").read_text()
    assert "--limit-request-line" in service and "CapabilityBoundingSet=" in service
    setup = (root / "setup.sh").read_text()
    assert "nginx-limit-req" in setup and "MaxAuthTries" in setup


# ---------------------------------------------------------------------------
# CSRF: the test client skips the check unless asked, so these turn it on
# ---------------------------------------------------------------------------


def test_posts_without_a_csrf_token_are_refused():
    user = make_user()
    strict = Client(enforce_csrf_checks=True)
    # Login form, signed out.
    assert strict.post(reverse("accounts:login"), {"phone": user.phone, "password": PASSWORD}).status_code == 403
    # A signed-in session does not make a token-less POST acceptable.
    strict.force_login(user)
    assert strict.post(reverse("accounts:logout")).status_code == 403


def test_the_login_page_sets_a_csrf_cookie_and_form_token(client):
    response = client.get(reverse("accounts:login"))
    assert "csrftoken" in response.cookies
    assert b"csrfmiddlewaretoken" in response.content
