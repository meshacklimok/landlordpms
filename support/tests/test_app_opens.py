"""Counting opens of the installed app (D-102): counts only, never a failed page."""

import datetime
import io

import pytest
from django.core.management import call_command
from django.db import DatabaseError

from accounts.tests.factories import make_org
from ops import pilot
from support import app_opens
from support.models import AppOpen

pytestmark = pytest.mark.django_db

ANDROID = "Mozilla/5.0 (Linux; Android 13; SM-A145F) AppleWebKit/537.36 Chrome/128.0 Mobile Safari/537.36"
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Version/17.5 Mobile/15E148"


def test_opens_are_counted_per_day_organization_audience_and_platform(client):
    owner = make_org(name="Acme")
    client.get("/?source=app", HTTP_USER_AGENT=ANDROID)
    client.force_login(owner.user)
    client.get("/?source=app", HTTP_USER_AGENT=ANDROID)
    client.get("/?source=app", HTTP_USER_AGENT=ANDROID)
    client.get("/?source=app", HTTP_USER_AGENT=IPHONE)
    client.get("/", HTTP_USER_AGENT=ANDROID)  # not from the app
    client.get("/?source=web", HTTP_USER_AGENT=ANDROID)
    rows = {(r.organization_id, r.audience, r.platform): r.count for r in AppOpen.objects.all()}
    org = owner.organization.pk
    assert rows == {(None, "SIGNED_OUT", "ANDROID"): 1, (org, "STAFF", "ANDROID"): 2, (org, "STAFF", "IOS"): 1}


def test_a_signed_in_user_without_an_organization_counts_as_a_tenant(client):
    from accounts.models import User

    user = User.objects.create_user(phone="+254712000999", password="x-long-password-1")
    client.force_login(user)
    client.get("/?source=app", HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0)")
    assert AppOpen.objects.get().audience == "TENANT" and AppOpen.objects.get().platform == "OTHER"


def test_a_failed_count_does_not_fail_the_page(client, monkeypatch):
    def broken(**kw):
        raise DatabaseError("down")

    monkeypatch.setattr(AppOpen.objects, "get_or_create", broken)
    assert client.get("/?source=app").status_code == 200
    assert not AppOpen.objects.exists()


def test_the_pilot_report_shows_the_opens(client):
    owner = make_org(name="Acme")
    today = datetime.date.today()
    AppOpen.objects.create(day=today, organization=owner.organization, audience="STAFF", platform="ANDROID", count=5)
    AppOpen.objects.create(day=today, audience="TENANT", platform="IOS", count=2)
    score = pilot.scores(today, organizations=type(owner.organization).objects.filter(pk=owner.organization.pk))[0]
    assert (score.app_opens, score.app_android, score.app_ios) == (5, 5, 0)
    out = io.StringIO()
    call_command("pilot_report", "--month", f"{today:%Y-%m}", stdout=out)
    assert "installed app opened 5 times (Android 5, iPhone 0)" in out.getvalue()
    assert "Tenant opened the installed app 2 times (Android 0, iPhone 2)." in out.getvalue()
    assert app_opens.platform(IPHONE) == "IOS"
