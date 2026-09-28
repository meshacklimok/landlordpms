"""Africa's Talking callbacks: delivery reports, STOP/START replies and network opt-outs (D-044 item 14)."""

import pytest
from django.urls import reverse

from accounts.tests.factories import make_org
from audit.models import AuditEvent
from core.sms import SmsResult
from notifications import catalog, delivery, sms_hooks
from notifications.models import ConsentRecord, Message
from notifications.services import channel_allowed
from tenants import services as tenant_services

pytestmark = pytest.mark.django_db

Status = Message.Status
TOKEN = "t" * 40
PHONE = "+254712345678"


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def tenant(owner):
    return tenant_services.create_tenant(owner, name="Wanjiku", phone="0712345678")


@pytest.fixture
def hook(settings, client):
    settings.AT_CALLBACK_TOKEN = TOKEN

    def post(kind, token=TOKEN, **data):
        return client.post(reverse("hook_africastalking", args=[token, kind]), data)

    return post


def sent_message(owner, tenant, provider_id="ATXid_1"):
    return Message.objects.create(organization=owner.organization, type="announcement", tenant=tenant,
                                  channel=catalog.SMS, to=tenant.phone, body="x", status=Status.SENT,
                                  provider=sms_hooks.PROVIDER, provider_id=provider_id)


# ---------------------------------------------------------------------------
# Permanent failures
# ---------------------------------------------------------------------------


def test_permanent_failure_is_not_retried(owner, tenant, monkeypatch):
    m = delivery.notify(owner.organization, "announcement", tenant=tenant, context={"text": "Hi"}, send_now=False)

    class Rejecting:
        def send(self, to, body):
            return SmsResult(ok=False, provider="africastalking", error="406 UserInBlacklist", permanent=True)

    monkeypatch.setattr(delivery, "get_sms_sender", Rejecting)
    assert delivery.send_one(m.pk) == Status.FAILED
    m.refresh_from_db()
    assert m.attempts == 1 and m.error == "406 UserInBlacklist"


# ---------------------------------------------------------------------------
# Delivery reports
# ---------------------------------------------------------------------------


def test_success_report_marks_delivered(owner, tenant):
    m = sent_message(owner, tenant)
    sms_hooks.delivery_report("ATXid_1", "Success")
    m.refresh_from_db()
    assert m.status == Status.DELIVERED and m.delivered_at is not None


def test_failed_report_marks_failed_with_reason(owner, tenant):
    m = sent_message(owner, tenant)
    sms_hooks.delivery_report("ATXid_1", "Failed", "InsufficientCredit")
    m.refresh_from_db()
    assert m.status == Status.FAILED and m.error == "Failed: InsufficientCredit"


def test_in_flight_and_unknown_reports_change_nothing(owner, tenant):
    m = sent_message(owner, tenant)
    sms_hooks.delivery_report("ATXid_1", "Buffered")
    m.refresh_from_db()
    assert m.status == Status.SENT
    assert sms_hooks.delivery_report("nope", "Success") is None
    assert sms_hooks.delivery_report("", "Success") is None


def test_report_only_matches_our_provider(owner, tenant):
    m = sent_message(owner, tenant)
    Message.objects.filter(pk=m.pk).update(provider="other")
    assert sms_hooks.delivery_report("ATXid_1", "Success") is None


# ---------------------------------------------------------------------------
# STOP and START
# ---------------------------------------------------------------------------


def test_stop_revokes_sms_in_every_organization(owner, tenant):
    other = make_org()
    twin = tenant_services.create_tenant(other, name="Wanjiku K", phone=PHONE)
    assert sms_hooks.inbound(PHONE, " stop. ") == 2
    for t in (tenant, twin):
        assert not channel_allowed(t, catalog.SMS)
    record = ConsentRecord.objects.filter(tenant=tenant).latest("created_at")
    assert record.source == ConsentRecord.Source.SMS_REPLY and not record.granted
    assert AuditEvent.objects.filter(action="tenant.consent", organization=owner.organization).exists()
    # A second STOP changes nothing and writes nothing.
    assert sms_hooks.inbound("0712345678", "STOP") == 0
    assert ConsentRecord.objects.count() == 2


def test_stopped_tenant_gets_no_sms(owner, tenant):
    sms_hooks.inbound(PHONE, "ACHA")
    m = delivery.notify(owner.organization, "announcement", tenant=tenant, context={"text": "Hi"})
    assert m.status == Status.SKIPPED and m.skip_reason == Message.SkipReason.NO_CONSENT


def test_start_restores_sms(tenant):
    sms_hooks.inbound(PHONE, "STOP")
    assert sms_hooks.inbound(PHONE, "Anza tafadhali") == 1
    assert channel_allowed(tenant, catalog.SMS)


def test_other_replies_and_bad_numbers_are_ignored(tenant):
    assert sms_hooks.inbound(PHONE, "Thanks, will pay Friday") is None
    assert sms_hooks.inbound("not a phone", "STOP") == 0
    assert channel_allowed(tenant, catalog.SMS)


def test_network_opt_out(tenant):
    assert sms_hooks.network_opt_out(PHONE) == 1
    assert not channel_allowed(tenant, catalog.SMS)


# ---------------------------------------------------------------------------
# The callback URL
# ---------------------------------------------------------------------------


def test_callbacks_dispatch(owner, tenant, hook):
    m = sent_message(owner, tenant)
    assert hook("delivery", id="ATXid_1", status="Success").status_code == 200
    m.refresh_from_db()
    assert m.status == Status.DELIVERED
    assert hook("inbound", **{"from": PHONE, "text": "STOP"}).status_code == 200
    assert not channel_allowed(tenant, catalog.SMS)
    assert hook("inbound", **{"from": PHONE, "text": "START"}).status_code == 200
    assert channel_allowed(tenant, catalog.SMS)
    assert hook("optout", phoneNumber=PHONE).status_code == 200
    assert not channel_allowed(tenant, catalog.SMS)


def test_wrong_or_unset_token_is_404(settings, tenant, hook):
    assert hook("inbound", token="x" * 40, **{"from": PHONE, "text": "STOP"}).status_code == 404
    settings.AT_CALLBACK_TOKEN = ""
    assert hook("inbound", token="anything", **{"from": PHONE, "text": "STOP"}).status_code == 404
    assert channel_allowed(tenant, catalog.SMS)


def test_unknown_kind_and_get_are_refused(hook, client):
    assert hook("other").status_code == 404
    assert client.get(reverse("hook_africastalking", args=[TOKEN, "delivery"])).status_code == 405
