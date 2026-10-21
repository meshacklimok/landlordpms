"""Support replies and speed, phone alerts, organizations sorted by rent, and the team activity figures."""

import datetime

import pytest
from django.utils import timezone

from accounts.tests.factories import make_org, make_property, make_user
from billing.tests.test_invoicing import make_lease
from console import alerts, insights, metrics
from notifications.models import Message
from ops import alerts as ops_alerts
from payments.models import Payment
from support import services as support_services
from support.models import SupportReply, SupportRequest

pytestmark = pytest.mark.django_db


def request_from(user, **kw):
    n = SupportRequest.objects.count() + 1
    return SupportRequest.objects.create(number=f"SUP-{n}", created_by=user, subject="Help", message="Please", **kw)


def age(item, **delta):
    SupportRequest.objects.filter(pk=item.pk).update(created_at=timezone.now() - datetime.timedelta(**delta))
    item.refresh_from_db()


def test_reply_by_email_marks_first_reply_and_waits_on_the_customer(mailoutbox):
    person = make_user(email="a@example.com")
    item = request_from(person)
    row = support_services.reply(make_user(), item, "  Try again now  ")
    item.refresh_from_db()
    assert row.channel == SupportReply.Channel.EMAIL and row.sent
    assert mailoutbox[0].to == ["a@example.com"] and mailoutbox[0].body == "Try again now"
    assert item.first_reply_at and item.status == SupportRequest.Status.WAITING


def test_an_empty_reply_is_refused():
    from django.core.exceptions import ValidationError
    item = request_from(make_user())
    with pytest.raises(ValidationError):
        support_services.reply(make_user(), item, "   ")
    assert not SupportReply.objects.exists()


def test_support_speed_counts_waiting_requests():
    person = make_user()
    old = request_from(person)
    age(old, days=4)
    fresh = request_from(person)
    answered = request_from(person)
    age(answered, hours=30)
    SupportRequest.objects.filter(pk=answered.pk).update(first_reply_at=answered.created_at + datetime.timedelta(hours=2))
    s = insights.support_speed()
    assert s["unanswered_24h"] == 1 and s["older_than_3_days"] == 1 and s["open"] == 3
    assert s["avg_reply_hours"] == 2.0
    assert fresh.pk


def test_alerts_flag_old_support_and_a_pile_of_failed_messages(settings):
    settings.ALERT_FAILED_MESSAGES = 2
    assert alerts.problems() == []
    item = request_from(make_user())
    age(item, hours=30)
    lines = alerts.problems()
    assert any("support requests" in line for line in lines)
    settings.ALERT_SUPPORT_HOURS = 0
    assert alerts.problems() == []
    assert Message.objects.count() == 0


def test_the_phone_alert_is_texted(settings, monkeypatch):
    settings.OPS_ALERT_PHONES = "254700000001, 254700000002"
    settings.OPS_ALERT_EMAILS = ""
    sent = []
    monkeypatch.setattr(ops_alerts, "send_sms", lambda to, body: sent.append(to))
    monkeypatch.setattr(ops_alerts, "current_problems", lambda now=None: ["something broke"])
    from django.core.cache import cache
    cache.delete(ops_alerts.STATE_KEY)
    assert ops_alerts.check_and_alert() == "alert"
    assert sent == ["254700000001", "254700000002"]
    assert ops_alerts.check_and_alert() == "quiet"


def test_organizations_carry_all_time_rent():
    landlord = make_org(name="Acme")
    lease = make_lease(landlord, make_property(landlord.organization))
    p = Payment.objects.create(organization=landlord.organization, lease=lease, amount=1500, method="CASH",
                               paid_at=timezone.localdate())
    Payment.objects.filter(pk=p.pk).update(status=Payment.Status.CONFIRMED)
    rows = {r["org"].name: r for r in metrics.organizations()}
    assert rows["Acme"]["ever"] == 1500
