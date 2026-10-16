"""A landlord's own WhatsApp number: saving it, sending from it, and its own webhook."""

import hashlib
import hmac
import json

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from accounts.tests.factories import add_member, make_org
from accounts.tests.test_isolation import login
from audit.models import AuditEvent
from core.whatsapp import CloudApiWhatsAppSender, MemoryWhatsAppSender, get_whatsapp_sender
from notifications import catalog, delivery, whatsapp_account
from notifications.models import ConsentRecord, Message, WhatsAppAccount
from notifications.tests.test_whatsapp import NOON, announce, change, grant
from tenants import services as tenant_services

pytestmark = pytest.mark.django_db

TOKEN = "EAAB-secret-token"
SECRET = "landlord-app-secret"


@pytest.fixture
def owner():
    return make_org(name="Acme")


@pytest.fixture
def org(owner):
    return owner.organization


@pytest.fixture
def tenant(owner):
    return tenant_services.create_tenant(owner, name="Wanjiku", phone="0712345678")


@pytest.fixture
def account(owner):
    return whatsapp_account.save(owner, phone_number_id="104500", access_token=TOKEN, app_secret=SECRET)


def signed(account_, payload, secret=SECRET):
    body = json.dumps(payload).encode()
    return {"data": body, "content_type": "application/json",
            "HTTP_X_HUB_SIGNATURE_256": "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()}


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------


def test_secrets_are_stored_encrypted_and_never_audited(owner, account):
    assert TOKEN not in account.access_token_encrypted and SECRET not in account.app_secret_encrypted
    assert account.secret("access_token") == TOKEN and account.usable
    for event in AuditEvent.objects.filter(action__startswith="whatsapp."):
        assert TOKEN not in json.dumps(event.changes) and SECRET not in json.dumps(event.changes)


def test_a_blank_token_keeps_the_saved_one(owner, account):
    whatsapp_account.save(owner, phone_number_id="104500", enabled=False)
    account.refresh_from_db()
    assert account.secret("access_token") == TOKEN and account.secret("app_secret") == SECRET
    assert not account.usable


def test_the_first_save_needs_a_token(owner):
    with pytest.raises(ValidationError) as e:
        whatsapp_account.save(owner, phone_number_id="104500")
    assert "access_token" in e.value.message_dict


def test_the_number_id_is_a_number(owner):
    with pytest.raises(ValidationError):
        whatsapp_account.save(owner, phone_number_id="+254 712", access_token=TOKEN)


def test_a_number_belongs_to_one_organization(account):
    other = make_org(name="Other")
    with pytest.raises(ValidationError) as e:
        whatsapp_account.save(other, phone_number_id="104500", access_token="x")
    assert "phone_number_id" in e.value.message_dict


def test_only_the_owner_can_change_it(org, account):
    caretaker = add_member(org, "caretaker", all_properties=True)
    with pytest.raises(PermissionDenied):
        whatsapp_account.save(caretaker, phone_number_id="104501", access_token="x")
    with pytest.raises(PermissionDenied):
        whatsapp_account.remove(caretaker)


def test_remove_deletes_it(owner, org, account):
    whatsapp_account.remove(owner)
    assert whatsapp_account.account_for(org) is None


def test_new_webhook_changes_both_values(owner, account):
    url, verify = account.webhook_token, account.verify_token
    account = whatsapp_account.new_webhook(owner)
    assert account.webhook_token != url and account.verify_token != verify


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def test_the_own_number_makes_whatsapp_available_without_a_platform_number(settings, org, tenant, account):
    settings.WHATSAPP_BACKEND = ""
    grant(org, tenant)
    assert delivery.whatsapp_available(org)
    assert announce(org, tenant).channel == catalog.WHATSAPP


def test_a_switched_off_number_is_not_used(settings, owner, org, tenant, account):
    settings.WHATSAPP_BACKEND = ""
    whatsapp_account.save(owner, phone_number_id="104500", enabled=False)
    grant(org, tenant)
    assert not delivery.whatsapp_available(org)
    assert announce(org, tenant).channel == catalog.SMS


def test_messages_go_out_from_the_landlords_number(settings, monkeypatch, org, tenant, account):
    settings.WHATSAPP_BACKEND = "core.whatsapp.MemoryWhatsAppSender"
    MemoryWhatsAppSender.outbox.clear()
    seen = []

    def post(self, payload):
        seen.append((self.url, self.token, payload["to"]))
        return {"messages": [{"id": "wamid.own"}]}

    monkeypatch.setattr(CloudApiWhatsAppSender, "_post", post)
    grant(org, tenant)
    message = announce(org, tenant)
    delivery.send_one(message.pk, now=NOON)
    message.refresh_from_db()
    assert seen == [("https://graph.facebook.com/v21.0/104500/messages", TOKEN, "254712345678")]
    assert (message.status, message.provider_id) == (Message.Status.SENT, "wamid.own")
    assert MemoryWhatsAppSender.outbox == []  # not the shared number


def test_another_organization_still_uses_the_platform_number(settings, account):
    settings.WHATSAPP_BACKEND = "core.whatsapp.MemoryWhatsAppSender"
    other = make_org(name="Other").organization
    assert isinstance(get_whatsapp_sender(other), MemoryWhatsAppSender)
    assert isinstance(get_whatsapp_sender(account.organization), CloudApiWhatsAppSender)


# ---------------------------------------------------------------------------
# The landlord's own webhook
# ---------------------------------------------------------------------------


def hook_url(account_):
    return reverse("hook_whatsapp_org", args=[account_.webhook_token])


def test_verification_uses_the_landlords_verify_token(client, account):
    ok = {"hub.mode": "subscribe", "hub.verify_token": account.verify_token, "hub.challenge": "42"}
    r = client.get(hook_url(account), ok)
    assert r.status_code == 200 and r.content == b"42"
    assert client.get(hook_url(account), {**ok, "hub.verify_token": "wrong"}).status_code == 404
    assert client.get(reverse("hook_whatsapp_org", args=["nope"]), ok).status_code == 404


def test_post_is_checked_against_the_landlords_app_secret(client, settings, account):
    settings.WA_APP_SECRET = "platform-secret"
    assert client.post(hook_url(account), **signed(account, change(), secret="platform-secret")).status_code == 403
    assert client.post(hook_url(account), **signed(account, change())).status_code == 200


def test_a_number_without_an_app_secret_hears_nothing(client, owner, account):
    account.set_secret("app_secret", "")
    account.save()
    assert client.post(hook_url(account), **signed(account, change())).status_code == 404


def test_a_switched_off_number_has_no_webhook(client, owner, account):
    whatsapp_account.save(owner, phone_number_id="104500", enabled=False)
    assert client.post(hook_url(account), **signed(account, change())).status_code == 404


def test_delivery_reports_update_that_landlords_log(client, settings, org, tenant, account):
    settings.WHATSAPP_BACKEND = ""
    message = Message.objects.create(
        organization=org, type="announcement", channel=catalog.WHATSAPP, tenant=tenant, to="+254712345678",
        status=Message.Status.SENT, provider_id="wamid.own", body="x")
    payload = change(statuses=[{"id": "wamid.own", "status": "read"}])
    assert client.post(hook_url(account), **signed(account, payload)).status_code == 200
    message.refresh_from_db()
    assert message.status == Message.Status.DELIVERED


def test_stop_on_one_landlords_number_does_not_touch_another_landlords_tenant(client, org, tenant, account):
    other_owner = make_org(name="Other")
    same_phone = tenant_services.create_tenant(other_owner, name="Wanjiku", phone="0712345678")
    for t in (tenant, same_phone):
        grant(t.organization, t)
    payload = change(messages=[{"from": "254712345678", "id": "wamid.in1", "type": "text", "text": {"body": "STOP"}}])
    assert client.post(hook_url(account), **signed(account, payload)).status_code == 200
    stopped = ConsentRecord.objects.filter(channel=catalog.WHATSAPP, granted=False)
    assert [c.tenant_id for c in stopped] == [tenant.pk]


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


def test_the_page_saves_details_and_never_shows_them_back(client, owner):
    login(client, owner)
    url = reverse("notifications:whatsapp")
    assert client.get(url).status_code == 200
    r = client.post(url, {"phone_number_id": "104500", "access_token": TOKEN, "app_secret": SECRET, "enabled": "on"})
    assert r.status_code == 302
    page = client.get(url).content.decode()
    assert TOKEN not in page and SECRET not in page
    assert "/hooks/whatsapp/" in page and "announcement_v1" in page


def test_the_page_shows_errors(client, owner):
    login(client, owner)
    r = client.post(reverse("notifications:whatsapp"), {"phone_number_id": "abc", "access_token": TOKEN})
    assert r.status_code == 400 and "phone number ID" in r.content.decode()


def test_check_keeps_what_meta_says(client, monkeypatch, owner, account):
    monkeypatch.setattr(CloudApiWhatsAppSender, "describe",
                        lambda self: {"display_phone_number": "+254 712 345 678", "verified_name": "Acme Homes"})
    login(client, owner)
    client.post(reverse("notifications:whatsapp"), {"action": "check"})
    account.refresh_from_db()
    assert (account.verified_name, account.check_error) == ("Acme Homes", "") and account.checked_at


def test_check_keeps_why_meta_refused(client, monkeypatch, owner, account):
    def refuse(self):
        raise ValueError("190 Invalid OAuth access token")

    monkeypatch.setattr(CloudApiWhatsAppSender, "describe", refuse)
    login(client, owner)
    client.post(reverse("notifications:whatsapp"), {"action": "check"})
    account.refresh_from_db()
    assert account.check_error == "190 Invalid OAuth access token"


def test_a_caretaker_cannot_open_the_page(client, org):
    login(client, add_member(org, "caretaker", all_properties=True))
    assert client.get(reverse("notifications:whatsapp")).status_code in (403, 404)
    assert WhatsAppAccount.objects.count() == 0
