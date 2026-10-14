"""A landlord's own mail server and SMS account, and the platform WhatsApp number set in admin (D-154)."""

import json
import smtplib

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from accounts.tests.factories import add_member, make_org
from accounts.tests.test_isolation import login
from audit.models import AuditEvent
from core.sms import AfricasTalkingSmsSender, SmsResult
from core.whatsapp import CloudApiWhatsAppSender, MemoryWhatsAppSender, get_whatsapp_sender
from notifications import catalog, delivery, emails, own_accounts, whatsapp_account
from notifications.models import EmailAccount, Message, PlatformWhatsApp, SmsAccount
from notifications.tests.test_whatsapp import NOON
from subscriptions.models import SmsWallet
from tenants import services as tenant_services

pytestmark = pytest.mark.django_db

PASSWORD = "smtp-pass-123"
KEY = "at-key-abc123"


@pytest.fixture
def owner():
    return make_org(name="Acme")


@pytest.fixture
def org(owner):
    return owner.organization


@pytest.fixture
def tenant(owner):
    return tenant_services.create_tenant(owner, name="Wanjiku", phone="0712345678", email="w@example.com")


def email_account(owner, **kw):
    data = {"host": "smtp.acme.co.ke", "port": 587, "username": "mail@acme.co.ke", "password": PASSWORD,
            "from_email": "rent@acme.co.ke", "from_name": "Acme Homes"}
    return own_accounts.save_email(owner, **{**data, **kw})


def sms_account(owner, **kw):
    return own_accounts.save_sms(owner, **{"username": "acme", "api_key": KEY, "sender_id": "ACME", **kw})


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


def test_email_password_is_encrypted_and_never_audited(owner):
    a = email_account(owner)
    assert PASSWORD not in a.password_encrypted and a.secret("password") == PASSWORD
    for event in AuditEvent.objects.filter(action__startswith="email_account."):
        assert PASSWORD not in json.dumps(event.changes)


def test_blank_password_keeps_the_saved_one(owner):
    email_account(owner)
    a = email_account(owner, password="", from_name="Acme Ltd")
    assert a.secret("password") == PASSWORD and a.from_name == "Acme Ltd"


def test_email_goes_out_from_the_landlords_own_address(settings, owner, org, tenant, monkeypatch):
    email_account(owner)
    seen = {}

    def send_messages(self, msgs):
        seen["from"], seen["host"], seen["password"] = msgs[0].from_email, self.host, self.password
        return 1

    monkeypatch.setattr("django.core.mail.backends.smtp.EmailBackend.send_messages", send_messages)
    message = Message.objects.create(organization=org, type="announcement", channel=catalog.EMAIL, tenant=tenant,
                                     to="w@example.com", subject="Hello", body="Hi")
    emails.send(message)
    assert seen == {"from": "Acme Homes <rent@acme.co.ke>", "host": "smtp.acme.co.ke", "password": PASSWORD}


def test_without_an_own_server_email_uses_the_platform(org, tenant):
    message = Message.objects.create(organization=org, type="announcement", channel=catalog.EMAIL, tenant=tenant,
                                     to="w@example.com", subject="Hello", body="Hi")
    emails.send(message)
    assert len(mail.outbox) == 1 and "Acme" in mail.outbox[0].from_email


def test_a_switched_off_server_is_not_used(owner, org):
    email_account(owner, enabled=False)
    assert own_accounts.usable_email(org) is None


def test_email_test_keeps_why_it_failed(owner, monkeypatch):
    email_account(owner)

    def boom(self, msgs):
        raise smtplib.SMTPAuthenticationError(535, b"bad login")

    monkeypatch.setattr("django.core.mail.backends.smtp.EmailBackend.send_messages", boom)
    owner.user.email = "owner@acme.co.ke"
    a = own_accounts.test_email(owner)
    assert a.checked_at and "535" in a.check_error


def test_only_the_owner_can_change_email(org, owner):
    caretaker = add_member(org, "caretaker", all_properties=True)
    with pytest.raises(PermissionDenied):
        email_account(caretaker)
    email_account(owner)
    with pytest.raises(PermissionDenied):
        own_accounts.remove_email(caretaker)
    own_accounts.remove_email(owner)
    assert not EmailAccount.objects.exists()


def test_a_bad_server_name_is_refused(owner):
    with pytest.raises(ValidationError):
        email_account(owner, host="smtp acme/co")


# ---------------------------------------------------------------------------
# SMS
# ---------------------------------------------------------------------------


def test_sms_key_is_encrypted_and_never_audited(owner):
    a = sms_account(owner)
    assert KEY not in a.api_key_encrypted and a.secret("api_key") == KEY
    for event in AuditEvent.objects.filter(action__startswith="sms_account."):
        assert KEY not in json.dumps(event.changes)


def test_first_sms_save_needs_a_key(owner):
    with pytest.raises(ValidationError) as e:
        own_accounts.save_sms(owner, username="acme")
    assert "api_key" in e.value.message_dict


def test_sender_name_is_checked(owner):
    with pytest.raises(ValidationError):
        sms_account(owner, sender_id="no spaces!")


def test_sms_uses_the_own_account_and_skips_the_wallet(settings, monkeypatch, owner, org, tenant):
    settings.SMS_WALLET_ENFORCED = True  # wallet empty: the platform route would be refused
    SmsWallet.objects.update_or_create(organization=org, defaults={"mode": SmsWallet.Mode.PREPAID})
    assert not delivery.sms_available(org)
    sms_account(owner)
    used = []

    def send(self, to, body, sender_id=""):
        used.append((self.username, self.api_key, self.sender_id, to))
        return SmsResult(ok=True, provider="at", provider_id="at-1")

    monkeypatch.setattr(AfricasTalkingSmsSender, "send", send)
    assert delivery.sms_available(org)
    charged = []
    monkeypatch.setattr("subscriptions.services.charge_message", lambda m: charged.append(m))
    message = Message.objects.create(organization=org, type="announcement", channel=catalog.SMS, tenant=tenant,
                                     to="+254712345678", body="Hi")
    delivery.send_one(message.pk, now=NOON)
    message.refresh_from_db()
    assert message.status == Message.Status.SENT
    assert used == [("acme", KEY, "ACME", "+254712345678")] and charged == []


def test_without_an_own_account_the_wallet_still_applies(settings, org):
    settings.SMS_WALLET_ENFORCED = True
    SmsWallet.objects.update_or_create(organization=org, defaults={"mode": SmsWallet.Mode.PREPAID})
    assert not delivery.sms_available(org)


def test_sms_test_goes_to_the_members_phone(monkeypatch, owner):
    sms_account(owner)
    sent = []
    monkeypatch.setattr(
        AfricasTalkingSmsSender, "send",
        lambda self, to, body, sender_id="": sent.append(to) or SmsResult(ok=False, provider="at", error="Invalid key"))
    owner.user.phone = "+254700000001"
    a = own_accounts.test_sms(owner)
    assert sent == ["+254700000001"] and a.check_error == "Invalid key"


# ---------------------------------------------------------------------------
# Platform WhatsApp number from admin
# ---------------------------------------------------------------------------


def platform(**kw):
    row = PlatformWhatsApp.get()
    row.phone_number_id = kw.get("phone_number_id", "9001")
    row.set_secret("access_token", kw.get("token", "PLAT-TOKEN"))
    row.set_secret("app_secret", kw.get("secret", "PLAT-SECRET"))
    row.enabled = kw.get("enabled", True)
    row.save()
    return row


def test_platform_number_from_admin_serves_organizations_without_their_own(settings, org):
    settings.WHATSAPP_BACKEND = ""
    assert not delivery.whatsapp_available(org)
    platform()
    assert delivery.whatsapp_available(org)
    sender = get_whatsapp_sender(org)
    assert isinstance(sender, CloudApiWhatsAppSender) and sender.token == "PLAT-TOKEN"


def test_switched_off_platform_number_falls_back_to_settings(settings, org):
    settings.WHATSAPP_BACKEND = "core.whatsapp.MemoryWhatsAppSender"
    platform(enabled=False)
    assert isinstance(get_whatsapp_sender(org), MemoryWhatsAppSender)


def test_the_landlords_own_number_beats_the_platform_number(owner, org):
    platform()
    whatsapp_account.save(owner, phone_number_id="104500", access_token="OWN")
    assert get_whatsapp_sender(org).token == "OWN"


def test_shared_webhook_uses_the_admin_verify_token(client, settings):
    settings.WA_VERIFY_TOKEN = "old"
    row = platform()
    ok = {"hub.mode": "subscribe", "hub.verify_token": row.verify_token, "hub.challenge": "7"}
    url = reverse("hook_whatsapp")
    assert client.get(url, ok).status_code == 200
    assert client.get(url, {**ok, "hub.verify_token": "old"}).status_code == 404


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def test_email_page_saves_and_hides_the_password(client, owner):
    login(client, owner)
    url = reverse("notifications:email")
    assert client.get(url).status_code == 200
    data = {"host": "smtp.acme.co.ke", "port": "587", "username": "u", "password": PASSWORD,
            "use_tls": "on", "from_email": "rent@acme.co.ke", "enabled": "on"}
    assert client.post(url, data).status_code == 302
    assert PASSWORD not in client.get(url).content.decode()
    assert client.post(url, {"action": "remove"}).status_code == 302
    assert not EmailAccount.objects.exists()


def test_sms_page_saves_and_hides_the_key(client, owner):
    login(client, owner)
    url = reverse("notifications:sms")
    data = {"username": "acme", "api_key": KEY, "sender_id": "ACME", "enabled": "on"}
    assert client.post(url, data).status_code == 302
    assert KEY not in client.get(url).content.decode()
    assert SmsAccount.objects.get().sender_id == "ACME"


def test_sms_page_shows_errors(client, owner):
    login(client, owner)
    r = client.post(reverse("notifications:sms"), {"username": "acme", "sender_id": "ACME"})
    assert r.status_code == 400 and "API key" in r.content.decode()


def test_a_caretaker_cannot_open_the_pages(client, org):
    login(client, add_member(org, "caretaker", all_properties=True))
    for name in ("email", "sms"):
        assert client.get(reverse(f"notifications:{name}")).status_code in (403, 404)


# ---------------------------------------------------------------------------
# Django admin
# ---------------------------------------------------------------------------


@pytest.fixture
def superuser(client, django_user_model, monkeypatch):
    from accounts import mfa

    monkeypatch.setattr(mfa, "is_enabled", lambda user: True)
    monkeypatch.setattr(mfa, "session_verified", lambda request: True)
    user = django_user_model.objects.create_superuser("0700999000", password="x-Pass-12345", email="root@example.com")
    client.force_login(user)
    return user


def test_admin_sets_the_platform_number_without_showing_secrets(client, superuser):
    url = reverse("admin:notifications_platformwhatsapp_changelist")
    assert client.get(url).status_code == 200
    row = PlatformWhatsApp.get()
    change = reverse("admin:notifications_platformwhatsapp_change", args=[row.pk])
    page = client.get(change).content.decode()
    assert 'type="password"' in page
    r = client.post(change, {"phone_number_id": "9001", "access_token": "ADM-TOKEN", "app_secret": "",
                             "verify_token": row.verify_token, "enabled": "on"})
    assert r.status_code == 302
    row.refresh_from_db()
    assert row.secret("access_token") == "ADM-TOKEN" and row.usable
    assert "ADM-TOKEN" not in client.get(change).content.decode()
    events = AuditEvent.objects.filter(action="platform.whatsapp")
    assert events.exists() and "ADM-TOKEN" not in json.dumps([e.changes for e in events])


def test_admin_can_edit_a_landlords_sms_account(client, superuser, owner):
    a = sms_account(owner)
    change = reverse("admin:notifications_smsaccount_change", args=[a.pk])
    assert client.get(change).status_code == 200
    r = client.post(change, {"organization": owner.organization.pk, "username": "acme2", "api_key": "",
                             "sender_id": "ACME", "enabled": "on"})
    assert r.status_code == 302
    a.refresh_from_db()
    assert a.username == "acme2" and a.secret("api_key") == KEY
