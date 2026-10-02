"""WhatsApp: templates, the Cloud API adapter, sending, fallback, consent and Meta's webhook (D-044 item 16)."""

import datetime
import hashlib
import hmac
import io
import json
import urllib.error
from dataclasses import replace
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.management import call_command
from django.urls import reverse

from accounts.tests.factories import make_org, make_property
from accounts.tests.test_isolation import login
from core.whatsapp import CloudApiWhatsAppSender, MemoryWhatsAppSender
from leases import services as lease_services
from notifications import announcements, catalog, delivery, services, whatsapp_hooks
from notifications.announcements import Audience
from notifications.management.commands.whatsapp_templates import meta_body, templates
from notifications.models import ConsentRecord, Message, NotificationPreference
from properties import services as property_services
from tenants import services as tenant_services

pytestmark = pytest.mark.django_db

EAT = ZoneInfo("Africa/Nairobi")
NOON = datetime.datetime(2026, 2, 3, 12, 0, tzinfo=EAT)
LATE = datetime.datetime(2026, 2, 3, 22, 30, tzinfo=EAT)
Status = Message.Status
Skip = Message.SkipReason
MEMORY = "core.whatsapp.MemoryWhatsAppSender"


@pytest.fixture
def wa(settings):
    settings.WHATSAPP_BACKEND = MEMORY
    MemoryWhatsAppSender.outbox.clear()
    yield MemoryWhatsAppSender.outbox
    MemoryWhatsAppSender.outbox.clear()


@pytest.fixture
def owner():
    return make_org(name="Acme")


@pytest.fixture
def org(owner):
    return owner.organization


@pytest.fixture
def tenant(owner):
    return tenant_services.create_tenant(owner, name="Wanjiku", phone="0712345678")


def grant(org, tenant):
    return ConsentRecord.objects.create(organization=org, tenant=tenant, channel=catalog.WHATSAPP, granted=True,
                                        source=ConsentRecord.Source.STAFF, note="Asked at signing")


def announce(org, tenant, now=NOON, text="Water is off on Friday.", **kw):
    return delivery.notify(org, "announcement", tenant=tenant, now=now, context={"text": text}, **kw)


# ---------------------------------------------------------------------------
# Catalog and templates
# ---------------------------------------------------------------------------


def test_tenant_types_prefer_whatsapp_and_derive_the_wording_from_sms():
    ntype = catalog.get("announcement")
    assert ntype.channels[:2] == (catalog.WHATSAPP, catalog.SMS)
    for language in (catalog.EN, catalog.SW):
        body = ntype.bodies[(catalog.WHATSAPP, language)]
        assert "{org_name}" in body.split("\n")[0]
        assert "STOP" in body.splitlines()[-1]
        assert "{text}" in body
    assert catalog.whatsapp_template(ntype) == "announcement_v1"
    assert all((catalog.WHATSAPP, catalog.EN) in t.bodies for t in catalog.TYPES if catalog.WHATSAPP in t.channels)


def test_meta_body_numbers_the_fields_in_order():
    assert meta_body("Hi {tenant_name}, {amount} for {unit}.") == "Hi {{1}}, {{2}} for {{3}}."


def test_templates_command_lists_every_whatsapp_template(capsys):
    rows = templates()
    names = {(r["name"], r["language"]) for r in rows}
    assert ("announcement_v1", "en") in names and ("announcement_v1", "sw") in names
    assert all(len(r["fields"]) == len(r["examples"]) and "{{" not in r["name"] for r in rows)
    out = io.StringIO()
    call_command("whatsapp_templates", stdout=out)
    assert "== announcement_v1 (en, UTILITY)" in out.getvalue() and "{{1}}" in out.getvalue()
    out = io.StringIO()
    call_command("whatsapp_templates", "--json", stdout=out)
    assert len(json.loads(out.getvalue())) == len(rows)


def test_whatsapp_call_flattens_values_and_fills_empty_ones():
    ntype = catalog.get("announcement")
    call = delivery.whatsapp_call(ntype, "{org_name}: {text} ({unit})", "sw",
                                  {"org_name": "Acme", "text": "Line one\n\n  line two", "unit": ""})
    assert call == {"name": "announcement_v1", "language": "sw", "params": ["Acme", "Line one line two", "-"]}


def test_whatsapp_language_falls_back_to_english():
    ntype = catalog.get("announcement")
    bodies = {k: v for k, v in ntype.bodies.items() if k != (catalog.WHATSAPP, catalog.SW)}
    assert catalog.whatsapp_language(replace(ntype, bodies=bodies), catalog.SW) == catalog.EN
    assert catalog.whatsapp_language(ntype, catalog.SW) == catalog.SW


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def test_granted_tenant_gets_the_approved_template(org, tenant, wa, django_capture_on_commit_callbacks):
    grant(org, tenant)
    with django_capture_on_commit_callbacks(execute=True):
        m = announce(org, tenant)
    m.refresh_from_db()
    assert (m.channel, m.status, m.provider, m.provider_id) == (catalog.WHATSAPP, Status.SENT, "memory",
                                                                "wamid.test1")
    assert m.provider_template["name"] == "announcement_v1" and m.provider_template["language"] == "en"
    assert "Water is off on Friday." in m.provider_template["params"]
    assert "Water is off on Friday." in m.body and "STOP" in m.body
    assert wa == [(tenant.phone, "announcement_v1", "en", m.provider_template["params"])]


def test_whatsapp_waits_for_quiet_hours(org, tenant, wa):
    grant(org, tenant)
    m = announce(org, tenant, now=LATE)
    assert m.channel == catalog.WHATSAPP and m.send_after is not None and m.send_after > LATE
    assert delivery.send_one(m.pk, now=LATE) is None
    assert wa == []


def test_without_a_grant_the_tenant_gets_sms(org, tenant, wa):
    assert announce(org, tenant).channel == catalog.SMS


def test_without_whatsapp_configured_sms_is_used_even_with_a_grant(settings, org, tenant):
    settings.WHATSAPP_BACKEND = ""
    grant(org, tenant)
    assert announce(org, tenant).channel == catalog.SMS


def test_skip_reports_the_meaningful_reason(org, tenant, wa):
    NotificationPreference.objects.create(organization=org, tenant=tenant, channel=catalog.SMS)
    m = announce(org, tenant)
    assert (m.status, m.channel, m.skip_reason) == (Status.SKIPPED, catalog.WHATSAPP, Skip.NO_CONSENT)


def test_whatsapp_org_wording_is_ignored(org, tenant, wa, owner):
    grant(org, tenant)
    with pytest.raises(ValidationError):
        services.save_template(owner, "announcement", catalog.WHATSAPP, "en", body="{text}")


def test_failed_send_retries_the_same_template(monkeypatch, org, tenant, wa):
    from core.sms import SmsResult
    grant(org, tenant)
    m = announce(org, tenant)
    failed = SmsResult(ok=False, provider="memory", error="boom")
    monkeypatch.setattr(MemoryWhatsAppSender, "send", lambda self, *a: failed)
    delivery.send_one(m.pk, now=NOON)
    m.refresh_from_db()
    assert (m.status, m.attempts, m.error) == (Status.QUEUED, 1, "boom")
    monkeypatch.undo()
    delivery.send_one(m.pk, now=NOON + datetime.timedelta(hours=1))
    assert wa[0][3] == m.provider_template["params"]


# ---------------------------------------------------------------------------
# Cloud API adapter
# ---------------------------------------------------------------------------


@pytest.fixture
def cloud(settings):
    settings.WA_PHONE_NUMBER_ID = "123"
    settings.WA_ACCESS_TOKEN = "tok"
    settings.WA_API_VERSION = "v21.0"
    return CloudApiWhatsAppSender()


def test_cloud_api_needs_settings(settings):
    settings.WA_PHONE_NUMBER_ID = ""
    settings.WA_ACCESS_TOKEN = ""
    with pytest.raises(ImproperlyConfigured):
        CloudApiWhatsAppSender()


def test_cloud_api_payload(cloud):
    assert cloud.url == "https://graph.facebook.com/v21.0/123/messages"
    assert cloud.payload("+254712345678", "announcement_v1", "sw", ["Acme", "Hi"]) == {
        "messaging_product": "whatsapp", "recipient_type": "individual", "to": "254712345678",
        "type": "template", "template": {
            "name": "announcement_v1", "language": {"code": "sw"},
            "components": [{"type": "body", "parameters": [{"type": "text", "text": "Acme"},
                                                           {"type": "text", "text": "Hi"}]}]}}
    assert "components" not in cloud.payload("+254712345678", "x_v1", "en", [])["template"]


def test_cloud_api_success(monkeypatch, cloud):
    sent = []
    monkeypatch.setattr(cloud, "_post", lambda payload: sent.append(payload) or {"messages": [{"id": "wamid.1"}]})
    result = cloud.send("+254712345678", "announcement_v1", "en", ["Acme"])
    assert (result.ok, result.provider, result.provider_id) == (True, "whatsapp", "wamid.1")
    assert sent[0]["to"] == "254712345678"


def test_cloud_api_without_an_id_fails(monkeypatch, cloud):
    monkeypatch.setattr(cloud, "_post", lambda payload: {})
    assert not cloud.send("+254712345678", "x_v1", "en", []).ok


def http_error(status, body):
    return urllib.error.HTTPError("https://x", status, "err", {}, io.BytesIO(json.dumps(body).encode()))


@pytest.mark.parametrize(("status", "code", "permanent"), [
    (400, 132001, True),       # template does not exist
    (400, 131026, True),       # not a WhatsApp number
    (400, 131056, False),      # pair rate limit
    (400, 130429, False),      # throughput limit
    (429, None, False),
    (503, None, False),
])
def test_cloud_api_errors(monkeypatch, cloud, status, code, permanent):
    body = {"error": {"code": code, "message": "Nope", "error_data": {"details": "Details here"}}} if code else {}

    def fail(payload):
        raise http_error(status, body)

    monkeypatch.setattr(cloud, "_post", fail)
    result = cloud.send("+254712345678", "x_v1", "en", [])
    assert not result.ok and result.permanent is permanent
    assert result.error == (f"{code} Details here" if code else f"HTTP {status}")


def test_cloud_api_network_error_is_retried(monkeypatch, cloud):
    def fail(payload):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(cloud, "_post", fail)
    result = cloud.send("+254712345678", "x_v1", "en", [])
    assert not result.ok and not result.permanent


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------


def test_staff_grant_needs_a_note(owner, tenant):
    with pytest.raises(ValidationError):
        services.set_tenant_channel(owner, tenant, catalog.WHATSAPP, allowed=True, note="  ")
    record = services.set_tenant_channel(owner, tenant, catalog.WHATSAPP, allowed=True, note="Signed form")
    assert record.granted and record.note == "Signed form" and services.channel_allowed(tenant, catalog.WHATSAPP)
    assert services.set_tenant_channel(owner, tenant, catalog.WHATSAPP, allowed=False) is not None
    assert not services.channel_allowed(tenant, catalog.WHATSAPP)


def test_tenant_page_offers_whatsapp_only_when_configured(client, settings, owner, tenant):
    login(client, owner)
    detail = reverse("tenants:detail", args=[tenant.public_id])
    settings.WHATSAPP_BACKEND = ""
    assert not client.get(detail).context["whatsapp_offered"]
    settings.WHATSAPP_BACKEND = MEMORY
    r = client.get(detail)
    assert r.context["whatsapp_offered"] and not r.context["whatsapp_allowed"]
    assert "Tenant agreed" in r.content.decode()

    url = reverse("notifications:tenant_channel", args=[tenant.public_id, "whatsapp"])
    client.post(url, {"allowed": "on", "note": ""})
    assert not services.channel_allowed(tenant, catalog.WHATSAPP)
    assert client.post(url, {"allowed": "on", "note": "Asked in person"}).status_code == 302
    assert services.channel_allowed(tenant, catalog.WHATSAPP)
    assert client.get(detail).context["whatsapp_allowed"]


def test_templates_page_shows_whatsapp_as_fixed(client, owner, wa):
    login(client, owner)
    r = client.get(reverse("notifications:templates"))
    assert "Fixed by WhatsApp" in r.content.decode()
    assert client.get(reverse("notifications:template_edit",
                              args=["announcement", "WHATSAPP", "en"])).status_code == 404


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------


SECRET = "app-secret"


@pytest.fixture
def hook(settings):
    settings.WA_APP_SECRET = SECRET
    settings.WA_VERIFY_TOKEN = "v" * 32
    return reverse("hook_whatsapp")


def post(client, url, payload, signature=None):
    body = json.dumps(payload).encode()
    signature = signature or "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return client.post(url, body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=signature)


def change(statuses=(), messages=()):
    return {"object": "whatsapp_business_account", "entry": [{"changes": [
        {"field": "messages", "value": {"statuses": list(statuses), "messages": list(messages)}}]}]}


def test_verification(client, hook):
    ok = {"hub.mode": "subscribe", "hub.verify_token": "v" * 32, "hub.challenge": "42"}
    r = client.get(hook, ok)
    assert r.status_code == 200 and r.content == b"42"
    assert client.get(hook, {**ok, "hub.verify_token": "wrong"}).status_code == 404
    assert client.get(hook, {**ok, "hub.mode": "unsubscribe"}).status_code == 404


def test_post_needs_a_valid_signature(client, settings, hook):
    assert post(client, hook, change(), signature="sha256=bad").status_code == 403
    assert client.post(hook, b"{", content_type="application/json",
                       HTTP_X_HUB_SIGNATURE_256="sha256=" + hmac.new(SECRET.encode(), b"{",
                                                                     hashlib.sha256).hexdigest()).status_code == 400
    settings.WA_APP_SECRET = ""
    assert post(client, hook, change()).status_code == 404


def sent_message(org, tenant, wa):
    grant(org, tenant)
    m = announce(org, tenant)
    delivery.send_one(m.pk, now=NOON)
    m.refresh_from_db()
    return m


def test_statuses_update_the_log(client, org, tenant, wa, hook):
    m = sent_message(org, tenant, wa)
    assert post(client, hook, change(statuses=[{"id": m.provider_id, "status": "sent"}])).status_code == 200
    m.refresh_from_db()
    assert m.status == Status.SENT
    post(client, hook, change(statuses=[{"id": m.provider_id, "status": "read"}]))
    m.refresh_from_db()
    assert m.status == Status.DELIVERED and m.delivered_at
    post(client, hook, change(statuses=[{"id": m.provider_id, "status": "failed",
                                         "errors": [{"code": 131026, "title": "Message undeliverable"}]}]))
    m.refresh_from_db()
    assert (m.status, m.error) == (Status.FAILED, "131026 Message undeliverable")


def test_unknown_status_ids_are_ignored(org):
    assert whatsapp_hooks.status_update("wamid.nope", "delivered") is None
    assert whatsapp_hooks.status_update("", "delivered") is None


def test_stop_reply_stops_whatsapp_everywhere_and_start_restores_only_grants(client, owner, org, tenant, hook):
    other_owner = make_org(name="Other")
    other = tenant_services.create_tenant(other_owner, name="Wanjiku", phone="0712345678")
    grant(org, tenant)
    msg = {"from": "254712345678", "type": "text", "text": {"body": "stop."}}
    post(client, hook, change(messages=[msg]))
    assert not services.channel_allowed(tenant, catalog.WHATSAPP)
    assert ConsentRecord.objects.filter(tenant=tenant).first().source == ConsentRecord.Source.WA_REPLY

    post(client, hook, change(messages=[{**msg, "type": "button", "button": {"text": "START"}}]))
    assert services.channel_allowed(tenant, catalog.WHATSAPP)
    assert not services.channel_allowed(other, catalog.WHATSAPP)
    assert services.channel_allowed(tenant, catalog.SMS)


def test_other_replies_change_nothing(org, tenant):
    grant(org, tenant)
    assert whatsapp_hooks.reply("+254712345678", "Thanks") is None
    assert whatsapp_hooks.reply("+254712345678", "") is None
    assert services.channel_allowed(tenant, catalog.WHATSAPP)


# ---------------------------------------------------------------------------
# Announcements
# ---------------------------------------------------------------------------


def test_announcement_preview_counts_whatsapp(owner, org, wa):
    prop = make_property(org, "Riverside")
    people = []
    for code, phone in (("A1", "0700000011"), ("A2", "0700000012")):
        unit = property_services.create_unit(owner, prop, code=code)
        person = tenant_services.create_tenant(owner, name=code, phone=phone)
        lease = lease_services.create_lease(owner, unit=unit, tenants=[person], start_date=datetime.date(2026, 1, 1),
                                            rent=10000, due_day=5, grace_days=3)
        lease_services.activate_lease(owner, lease)
        people.append(person)
    grant(org, people[0])
    result = announcements.preview(owner, "Water is off.", Audience(), datetime.date(2026, 2, 20))
    assert result.whatsapp == 1
    assert {r.channel for r in result.recipients} == {catalog.WHATSAPP, catalog.SMS}
