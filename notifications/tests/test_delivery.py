"""The delivery engine: rules, opt-outs, consent, quiet hours, dedupe and sending (doc 11 §27, D-044)."""

import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.core import mail

from accounts.tests.factories import add_member, make_org, make_property
from core.sms import MemorySmsSender, SmsResult
from notifications import catalog, delivery
from notifications.models import (
    ConsentRecord,
    Message,
    MessageTemplate,
    NotificationPreference,
    OrganizationNotificationRule,
)
from tenants import services as tenant_services

pytestmark = pytest.mark.django_db

EAT = ZoneInfo("Africa/Nairobi")
NOON = datetime.datetime(2026, 2, 3, 12, 0, tzinfo=EAT)
LATE = datetime.datetime(2026, 2, 3, 22, 30, tzinfo=EAT)
Status = Message.Status
Skip = Message.SkipReason


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def org(owner):
    return owner.organization


@pytest.fixture
def tenant(owner):
    return tenant_services.create_tenant(owner, name="Wanjiku", phone="0712345678")


def announce(org, tenant, now=NOON, **kw):
    kw.setdefault("context", {"text": "Water is off on Friday."})
    return delivery.notify(org, "announcement", tenant=tenant, now=now, **kw)


# ---------------------------------------------------------------------------
# Channel choice and skips
# ---------------------------------------------------------------------------


def test_sms_is_queued_and_sent_after_commit(org, tenant, outbox, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        m = announce(org, tenant)
    m.refresh_from_db()
    assert m.channel == catalog.SMS and m.to == "+254712345678"
    assert m.status == Status.SENT and m.attempts == 1 and m.provider == "memory"
    assert outbox == [("+254712345678", f"Water is off on Friday. {org.name}")]


def test_rule_switched_off_writes_a_skipped_row(org, tenant, outbox):
    OrganizationNotificationRule.objects.create(organization=org, type="announcement", enabled=False)
    m = announce(org, tenant)
    assert (m.status, m.skip_reason, m.body) == (Status.SKIPPED, Skip.RULE_DISABLED, "")
    assert delivery.send_due() == {"sent": 0, "retrying": 0, "failed": 0}


def test_mandatory_type_ignores_the_rule_and_opt_outs(monkeypatch, org, tenant):
    ntype = catalog.get("announcement")
    monkeypatch.setitem(catalog.BY_CODENAME, "announcement",
                        catalog.NotificationType(**{**ntype.__dict__, "mandatory": True}))
    OrganizationNotificationRule.objects.create(organization=org, type="announcement", enabled=False)
    NotificationPreference.objects.create(organization=org, tenant=tenant, channel=catalog.SMS)
    ConsentRecord.objects.create(organization=org, tenant=tenant, channel=catalog.SMS, granted=False,
                                 source=ConsentRecord.Source.SMS_REPLY)
    assert announce(org, tenant).status == Status.QUEUED


def test_opt_out_for_all_types_and_back_in_for_one(org, tenant):
    NotificationPreference.objects.create(organization=org, tenant=tenant, channel=catalog.SMS)
    assert announce(org, tenant).skip_reason == Skip.OPTED_OUT
    NotificationPreference.objects.create(organization=org, tenant=tenant, channel=catalog.SMS,
                                          type="announcement", enabled=True)
    assert announce(org, tenant).status == Status.QUEUED


def test_latest_consent_record_wins(org, tenant):
    def record(granted):
        ConsentRecord.objects.create(organization=org, tenant=tenant, channel=catalog.SMS, granted=granted,
                                     source=ConsentRecord.Source.STAFF)

    record(False)
    assert announce(org, tenant).skip_reason == Skip.NO_CONSENT
    record(True)
    assert announce(org, tenant).status == Status.QUEUED


def test_whatsapp_needs_an_explicit_grant_then_falls_back_to_sms(settings, org, tenant):
    settings.WHATSAPP_BACKEND = "x"
    OrganizationNotificationRule.objects.create(organization=org, type="announcement",
                                                channels=[catalog.WHATSAPP, catalog.SMS])
    MessageTemplate.objects.create(organization=org, type="announcement", channel=catalog.WHATSAPP,
                                   language="en", body="{text}")
    assert announce(org, tenant).channel == catalog.SMS
    ConsentRecord.objects.create(organization=org, tenant=tenant, channel=catalog.WHATSAPP, granted=True,
                                 source=ConsentRecord.Source.STAFF)
    assert announce(org, tenant).channel == catalog.WHATSAPP


def test_no_channel_left_records_the_first_reason(org, tenant):
    OrganizationNotificationRule.objects.create(organization=org, type="announcement",
                                                channels=[catalog.WHATSAPP, catalog.IN_APP])
    m = announce(org, tenant)
    assert (m.status, m.channel, m.skip_reason) == (Status.SKIPPED, catalog.WHATSAPP, Skip.CHANNEL_UNAVAILABLE)


def test_channel_without_text_is_skipped(org, tenant):
    OrganizationNotificationRule.objects.create(organization=org, type="announcement", channels=[catalog.EMAIL])
    tenant.email = "w@example.com"
    tenant.save()
    assert announce(org, tenant).skip_reason == Skip.NO_TEMPLATE


def test_staff_in_app_is_delivered_at_once(owner, org):
    m = delivery.notify(org, "payment_pending_review", user=owner.user, now=NOON,
                        context={"recorded_by": "Juma", "amount": "KES 5,000.00", "tenant_name": "W", "unit": "A1"})
    assert (m.status, m.channel, m.delivered_at) == (Status.DELIVERED, catalog.IN_APP, NOON)
    assert m.body == "Juma recorded KES 5,000.00 from W (A1). It needs review."


def test_notify_needs_exactly_one_recipient(owner, org, tenant):
    with pytest.raises(ValueError):
        delivery.notify(org, "announcement")
    with pytest.raises(ValueError):
        delivery.notify(org, "announcement", tenant=tenant, user=owner.user)
    with pytest.raises(ValueError):
        delivery.notify(org, "no_such_type", tenant=tenant)


# ---------------------------------------------------------------------------
# Language and templates
# ---------------------------------------------------------------------------


def test_swahili_tenant_gets_the_swahili_default(org, tenant):
    tenant.language = "sw"
    tenant.save()
    m = delivery.notify(org, "payment_received", tenant=tenant, now=NOON, context={
        "tenant_name": "Wanjiku", "amount": "KES 5,000.00", "unit": "A1", "paid_on": "03/02/2026",
        "receipt_number": "RCT-2026-000001", "balance": "KES 0.00"})
    assert m.language == "sw"
    assert m.body.startswith("Mpendwa Wanjiku, tumepokea KES 5,000.00 ya A1")


def test_organization_template_overrides_the_default(org, tenant):
    MessageTemplate.objects.create(organization=org, type="announcement", channel=catalog.SMS, language="en",
                                   body="NOTICE: {text} – {org_name}")
    assert announce(org, tenant).body == f"NOTICE: Water is off on Friday. – {org.name}"


# ---------------------------------------------------------------------------
# Quiet hours
# ---------------------------------------------------------------------------


def test_sms_in_quiet_hours_waits_until_they_end(org, tenant, outbox, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        m = announce(org, tenant, now=LATE)
    assert m.send_after == datetime.datetime(2026, 2, 4, 7, 0, tzinfo=EAT)
    assert outbox == []
    assert delivery.send_due(now=datetime.datetime(2026, 2, 4, 6, 59, tzinfo=EAT))["sent"] == 0
    assert delivery.send_due(now=datetime.datetime(2026, 2, 4, 7, 0, tzinfo=EAT))["sent"] == 1
    assert len(outbox) == 1


@pytest.mark.parametrize(("start", "end", "at", "expected"), [
    ((21, 0), (7, 0), (6, 0), (2026, 2, 3, 7)),     # early morning, same day
    ((21, 0), (7, 0), (21, 0), (2026, 2, 4, 7)),    # start is inside
    ((21, 0), (7, 0), (7, 0), None),                # end is outside
    ((13, 0), (14, 0), (13, 30), (2026, 2, 3, 14)), # a daytime window
    ((0, 0), (0, 0), (3, 0), None),                 # start == end: no quiet hours
])
def test_quiet_until(org, start, end, at, expected):
    org.quiet_hours_start, org.quiet_hours_end = datetime.time(*start), datetime.time(*end)
    got = delivery.quiet_until(org, datetime.datetime(2026, 2, 3, *at, tzinfo=EAT))
    assert got == (datetime.datetime(*expected, 0, tzinfo=EAT) if expected else None)


# ---------------------------------------------------------------------------
# Dedupe
# ---------------------------------------------------------------------------


def test_same_dedupe_key_sends_once_per_organization(org, tenant):
    assert announce(org, tenant, dedupe_key="evt-1") is not None
    assert announce(org, tenant, dedupe_key="evt-1") is None
    assert Message.objects.filter(dedupe_key="evt-1").count() == 1
    other = make_org()
    other_tenant = tenant_services.create_tenant(other, name="X", phone="0700000001")
    assert announce(other.organization, other_tenant, dedupe_key="evt-1") is not None


def test_skipped_event_is_also_deduped(org, tenant):
    OrganizationNotificationRule.objects.create(organization=org, type="announcement", enabled=False)
    assert announce(org, tenant, dedupe_key="evt-2").status == Status.SKIPPED
    assert announce(org, tenant, dedupe_key="evt-2") is None


# ---------------------------------------------------------------------------
# Sending, retries and failures
# ---------------------------------------------------------------------------


class _Flaky:
    name = "flaky"
    calls = 0

    def send(self, to, body):
        _Flaky.calls += 1
        raise ConnectionError("provider down")


def test_failures_retry_with_backoff_then_fail(settings, org, tenant):
    settings.SMS_BACKEND = "notifications.tests.test_delivery._Flaky"
    m = announce(org, tenant)
    assert delivery.send_one(m.pk, NOON) == Status.QUEUED
    m.refresh_from_db()
    assert (m.attempts, m.error, m.send_after) == (1, "provider down", NOON + datetime.timedelta(minutes=5))
    assert delivery.send_one(m.pk, NOON) is None  # not due yet
    assert delivery.send_one(m.pk, NOON + datetime.timedelta(minutes=5)) == Status.QUEUED
    m.refresh_from_db()
    assert m.send_after == NOON + datetime.timedelta(minutes=15)
    assert delivery.send_due(now=NOON + datetime.timedelta(minutes=15)) == {"sent": 0, "retrying": 0, "failed": 1}
    m.refresh_from_db()
    assert (m.status, m.attempts) == (Status.FAILED, 3)


class _Priced:
    name = "priced"

    def send(self, to, body):
        return SmsResult(ok=True, provider="priced", provider_id="ATX-1", cost=Decimal("0.8000"))


def test_provider_id_and_cost_are_kept(settings, org, tenant):
    settings.SMS_BACKEND = "notifications.tests.test_delivery._Priced"
    m = announce(org, tenant)
    delivery.send_one(m.pk, NOON)
    m.refresh_from_db()
    assert (m.status, m.provider_id, m.cost, m.sent_at) == (Status.SENT, "ATX-1", Decimal("0.8000"), NOON)


def test_email_channel_sends_mail(org, tenant):
    OrganizationNotificationRule.objects.create(organization=org, type="announcement", channels=[catalog.EMAIL])
    MessageTemplate.objects.create(organization=org, type="announcement", channel=catalog.EMAIL, language="en",
                                   body="{text}")
    tenant.email = "w@example.com"
    tenant.save()
    m = announce(org, tenant)
    assert delivery.send_one(m.pk, NOON) == Status.SENT
    assert mail.outbox[0].to == ["w@example.com"] and mail.outbox[0].body == "Water is off on Friday."


def test_a_sent_message_is_never_sent_again(org, tenant, outbox):
    m = announce(org, tenant)
    delivery.send_one(m.pk, NOON)
    assert delivery.send_one(m.pk, NOON) is None
    assert len(MemorySmsSender.outbox) == 1


# ---------------------------------------------------------------------------
# Staff recipients
# ---------------------------------------------------------------------------


def test_staff_with_follows_capability_and_property_scope(owner, org):
    prop, other = make_property(org), make_property(org)
    manager = add_member(org, "manager", properties=[prop])
    add_member(org, "caretaker", all_properties=True)
    assert set(delivery.staff_with(org, "payments.confirm", prop)) == {owner.user, manager.user}
    assert delivery.staff_with(org, "payments.confirm", other) == [owner.user]
