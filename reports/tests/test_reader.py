"""The report centre reader: a report shown in a frame beside the list, and the one framing exception (D-148)."""

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils.csp import CSP

from accounts.tests.factories import make_org
from accounts.tests.test_isolation import login

pytestmark = pytest.mark.django_db

POLICY = {"default-src": [CSP.SELF], "frame-ancestors": [CSP.NONE]}


@pytest.fixture
def owner(client):
    owner = make_org(name="Acme")
    login(client, owner)
    return owner


def test_the_centre_opens_the_first_report_or_the_one_asked_for(client, owner):
    page = client.get(reverse("reports:centre")).content.decode()
    assert 'class="reader"' in page and f'src="{reverse("reports:dashboard")}?pane=1"' in page

    page = client.get(reverse("reports:centre"), {"r": "arrears"}).content.decode()
    assert f'src="{reverse("reports:arrears")}?pane=1"' in page
    assert 'data-report="arrears" aria-current="page"' in page

    page = client.get(reverse("reports:centre"), {"r": "nonsense"}).content.decode()
    assert f'src="{reverse("reports:dashboard")}?pane=1"' in page


def test_a_report_in_the_pane_has_no_menus(client, owner):
    plain = client.get(reverse("reports:arrears")).content.decode()
    pane = client.get(reverse("reports:arrears"), {"pane": "1"}).content.decode()
    assert "app-topbar" in plain and "app-sidebar" in plain
    assert "app-topbar" not in pane and "app-sidebar" not in pane
    assert 'class="is-pane"' in pane and "report_pane.js" in pane


@override_settings(SECURE_CSP=POLICY, X_FRAME_OPTIONS="DENY")
def test_only_a_report_in_the_pane_may_be_framed_and_only_by_us(client, owner):
    pane = client.get(reverse("reports:arrears"), {"pane": "1"})
    assert pane["X-Frame-Options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in pane["Content-Security-Policy"]

    for response in (client.get(reverse("reports:arrears")),
                     client.get(reverse("reports:centre"), {"pane": "1"}),
                     client.get(reverse("payments:list"), {"pane": "1"})):
        assert response["X-Frame-Options"] == "DENY"
        assert "frame-ancestors 'none'" in response["Content-Security-Policy"]


@override_settings(SECURE_CSP=POLICY, X_FRAME_OPTIONS="DENY")
def test_every_report_in_the_centre_may_be_framed(client, owner):
    import json
    import re
    page = client.get(reverse("reports:centre")).content.decode()
    data = json.loads(re.search(r'id="reader-reports"[^>]*>(.*?)</script>', page, re.S).group(1))
    for r in data:
        response = client.get(r["url"], {"pane": "1"})
        assert (r["key"], response.status_code, response.get("X-Frame-Options")) == (r["key"], 200, "SAMEORIGIN")


def test_a_report_page_steps_to_the_report_before_and_after(client, owner):
    page = client.get(reverse("reports:receivables")).content.decode()
    assert 'class="report-steps' in page
    assert f'href="{reverse("reports:outlook")}" rel="prev"' in page
    assert f'href="{reverse("reports:arrears")}" rel="next"' in page
    assert f'{reverse("reports:centre")}?r=receivables' in page
    # Not inside the reader's frame, which has its own Previous and Next.
    assert 'class="report-steps' not in client.get(reverse("reports:receivables"), {"pane": "1"}).content.decode()


def test_the_centre_page_may_frame_our_own_pages(client, owner):
    """frame-src set on its own drops the default-src fallback, which blanked the reader's frame."""
    csp = client.get(reverse("reports:centre"))["Content-Security-Policy"]
    sources = next(p for p in csp.split("; ") if p.startswith("frame-src")).split()[1:]
    assert "'self'" in sources


def test_print_all_lists_every_report_the_member_may_open(client, owner):
    page = client.get(reverse("reports:print_all")).content.decode()
    assert f'src="{reverse("reports:dashboard")}?pane=1"' in page
    assert f'src="{reverse("reports:arrears")}?pane=1"' in page
    assert "print_all.js" in page


def test_the_failed_box_can_say_why_and_offer_a_sign_in(client, owner):
    page = client.get(reverse("reports:centre"), {"r": "arrears"}).content.decode()
    for hook in ("data-reader-reason", "data-text-login=", "data-text-server=", "data-text-offline=", "data-reader-signin"):
        assert hook in page
    assert f'{reverse("accounts:login")}?next=' in page


def test_every_report_draws_in_the_pane_with_and_without_a_period(client, owner):
    """A report that errors inside the frame shows the failed box, so each one must render with and without a date range."""
    page = client.get(reverse("reports:centre")).content.decode()
    import json
    import re
    data = json.loads(re.search(r'id="reader-reports"[^>]*>(.*?)</script>', page, re.S).group(1))
    assert data
    for r in data:
        for params in ({"pane": "1"}, {"pane": "1", "from": "2020-01", "to": "2030-12"}):
            assert client.get(r["url"], params).status_code == 200, (r["key"], params)
