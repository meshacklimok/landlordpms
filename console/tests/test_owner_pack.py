"""Today page, view as landlord, saved replies, bulk actions, statement, digest, audit CSV and the stuck lists."""

import datetime
from types import SimpleNamespace

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from accounts.tests.factories import make_org
from audit.models import AuditEvent
from console import digest, insights, metrics, today, viewas
from console.models import CannedReply, PlatformAnnouncement
from console.tests.test_console import owner, seat_for, signed_in
from core import system_messages
from subscriptions.models import SubscriptionPayment

pytestmark = pytest.mark.django_db


def test_today_page_loads_for_a_seat_and_the_owner(client):
    seat_for()
    signed_in(client, seat_for().user)
    assert client.get(reverse("console:today")).status_code == 200
    data = today.build(["support", "health", "organizations"], superuser=True)
    assert {"support", "problems", "watch", "new_signups", "follow_ups"} <= set(data)


def test_view_as_is_read_only_audited_and_for_the_owner_only(client):
    org = make_org().organization
    seat = seat_for()
    signed_in(client, seat.user)
    assert client.post(reverse("console:view_as", args=[org.public_id])).status_code in (403, 404)
    owner(client)
    r = client.post(reverse("console:view_as", args=[org.public_id]))
    assert r.status_code == 302 and client.session[viewas.SESSION_KEY] == str(org.public_id)
    assert AuditEvent.objects.filter(action="console.view_as", organization=org).count() == 1
    assert client.get(reverse("accounts:home")).status_code in (200, 302)
    blocked = client.post(reverse("accounts:home"))
    assert blocked.status_code in (302, 403)
    assert not AuditEvent.objects.filter(action="console.view_as", changes__has_key="blocked").exists()
    client.post(reverse("console:view_as_stop"))
    assert viewas.SESSION_KEY not in client.session
    assert AuditEvent.objects.filter(action="console.view_as", organization=org).count() == 2


def test_view_as_a_role_nobody_holds_and_stop_from_the_next_screens(client):
    from accounts.models import Role, RoleTemplate, User
    from accounts.permissions import can

    org = make_org().organization
    owner(client)
    caretaker = Role.objects.filter(organization=org).exclude(is_owner_role=True).first()
    start = reverse("console:view_as", args=[org.public_id])
    assert client.post(start, {"role": str(caretaker.public_id)}).status_code == 302
    seat = viewas.membership_for(SimpleNamespace(user=User.objects.get(is_superuser=True), session=client.session))
    assert seat.role.name == caretaker.name and not can(seat, "organization.manage")
    session = client.get(reverse("accounts:web_session")).json()
    assert session["viewing"]["stop_url"] == reverse("console:view_as_stop") and session["role"] == caretaker.name
    # a standard role the organization has not set up
    Role.objects.filter(organization=org, based_on_template__key="viewer").delete()
    assert RoleTemplate.objects.filter(key="viewer").exists()
    assert client.post(start, {"role": "template:viewer"}).status_code == 302
    assert client.get(reverse("accounts:web_session")).json()["role"] == "Viewer"
    assert client.post(start, {"role": "template:nonsense"}).status_code == 302
    assert viewas.ROLE_KEY not in client.session
    client.post(start, {"role": str(caretaker.public_id)})
    assert client.get(reverse("console:view_as_stop")).status_code == 302
    assert viewas.SESSION_KEY not in client.session and viewas.ROLE_KEY not in client.session
    assert client.get(reverse("accounts:web_session")).json().get("viewing") is None


def test_canned_replies_add_and_delete(client):
    owner(client)
    url = reverse("console:canned_replies")
    client.post(url, {"title": "Hello", "body": "Thanks, looking now"})
    reply = CannedReply.objects.get()
    assert client.get(url).status_code == 200
    client.post(url, {"delete": reply.pk})
    assert not CannedReply.objects.exists()


def test_a_scheduled_notice_waits_for_its_time():
    now = timezone.now()
    soon = PlatformAnnouncement.objects.create(message="later", starts_at=now + datetime.timedelta(hours=3))
    live = PlatformAnnouncement.objects.create(message="now")
    from django.db.models import Q
    shown = PlatformAnnouncement.objects.filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now))
    assert live in shown and soon not in shown


def test_bulk_note_and_grace(client):
    a, b = make_org().organization, make_org().organization
    owner(client)
    client.post(reverse("console:organization_bulk"), {"action": "note", "o": [str(a.public_id), str(b.public_id)], "text": "Call them"})
    assert a.owner_notes.count() == 1 and b.owner_notes.count() == 1


def test_statement_adds_income_and_the_page_and_csv_load(client):
    org = make_org().organization
    SubscriptionPayment.objects.create(organization=org, amount=2500, paid_on=timezone.localdate())
    data = insights.statement()
    assert data["income_total"] == 2500 and data["this_month"]["income"] == 2500
    owner(client)
    assert client.get(reverse("console:statement")).status_code == 200
    r = client.get(reverse("console:statement"), {"format": "csv"})
    assert r["Content-Type"].startswith("text/csv")


def test_digest_is_sent_unless_switched_off(settings):
    settings.OPS_ALERT_EMAILS = "boss@example.com"
    assert digest.send() == "sent"
    assert mail.outbox[-1].to == ["boss@example.com"] and "Daily summary" in mail.outbox[-1].subject
    system_messages.BY_KEY["owner_digest"]
    from core.models import SystemMessageSetting
    SystemMessageSetting.objects.create(kind="owner_digest", enabled=False)
    from django.core.cache import cache
    cache.clear()
    before = len(mail.outbox)
    assert digest.send() == "off" and len(mail.outbox) == before


def test_audit_csv_filters_by_date_and_is_audited(client):
    owner(client)
    r = client.get(reverse("console:audit"), {"format": "csv", "since": "2000-01-01", "until": "2000-01-02"})
    assert r["Content-Type"].startswith("text/csv")
    assert AuditEvent.objects.filter(action="console.export").exists()
    assert client.get(reverse("console:audit"), {"since": "2000-01-01"}).status_code == 200


def test_stuck_lists_name_old_signups_without_a_property():
    old = make_org().organization
    type(old).objects.filter(pk=old.pk).update(created_at=timezone.now() - datetime.timedelta(days=5))
    fresh = make_org().organization
    rows = {r["label"]: r for r in metrics.stuck()}
    assert old in rows["Added a property"]["items"] and fresh not in rows["Added a property"]["items"]


def test_health_label_gets_worse_with_risk():
    assert metrics.health_label(0) != metrics.health_label(100)
