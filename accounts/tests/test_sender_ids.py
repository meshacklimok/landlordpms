"""An organization's own SMS sender name (D-120): the platform's name by default, the organization's once approved."""

from types import SimpleNamespace

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from accounts import sender_ids
from accounts.models import Organization
from accounts.tests.factories import PASSWORD, add_member, make_org, make_user
from accounts.tests.test_isolation import login
from audit.models import AuditEvent
from core.sms import AfricasTalkingSmsSender, MemorySmsSender, send_sms
from core.tests.test_sms import recipient, reply
from notifications import catalog, delivery
from notifications.models import Message
from portal import services as portal_services

pytestmark = pytest.mark.django_db

Status = Organization.SenderStatus


@pytest.fixture
def owner():
    return make_org("Kamau Homes")


@pytest.fixture
def admin():
    return make_user(is_staff=True, is_superuser=True)


def _approved(owner, admin, name="KAMAUHOMES"):
    sender_ids.request_name(owner, name)
    org = Organization.objects.get(pk=owner.organization.pk)
    sender_ids.decide(admin, org, True)
    return org


def test_platform_name_by_default(owner):
    send_sms("+254712345678", "Rent is due", org=owner.organization)
    assert owner.organization.sms_sender_name == ""
    assert MemorySmsSender.senders == [""]


@pytest.mark.parametrize("name", ["", "AB", "TWELVECHARSX", "KAMAU HOMES", "KAMAU-HOMES", "12345"])
def test_bad_names_are_refused(owner, name):
    with pytest.raises(ValidationError):
        sender_ids.request_name(owner, name)


def test_platform_name_is_not_requestable(owner, settings):
    settings.AT_SENDER_ID = "NYUMBAPMS"
    with pytest.raises(ValidationError):
        sender_ids.request_name(owner, "nyumbapms")


def test_a_request_waits_for_approval(owner, settings):
    settings.OPS_ALERT_EMAILS = "ops@example.com"
    sender_ids.request_name(owner, " KAMAUHOMES ")
    org = Organization.objects.get(pk=owner.organization.pk)
    assert (org.sms_sender_id, org.sms_sender_status) == ("KAMAUHOMES", Status.REQUESTED)
    assert org.sms_sender_name == ""  # still the platform's while it waits
    assert AuditEvent.objects.filter(action="organization.sender_id_requested").exists()
    assert "KAMAUHOMES" in mail.outbox[0].subject


def test_only_subscription_managers_can_ask(owner):
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    with pytest.raises(PermissionDenied):
        sender_ids.request_name(caretaker, "KAMAUHOMES")


def test_approved_name_is_used_for_the_organizations_sms(owner, admin):
    org = _approved(owner, admin)
    assert org.sms_sender_name == "KAMAUHOMES"
    send_sms("+254712345678", "Rent is due", org=org)
    send_sms("+254712345678", "Your NyumbaPMS code is 123456")  # login codes keep the platform's name
    assert MemorySmsSender.senders == ["KAMAUHOMES", ""]
    assert AuditEvent.objects.filter(action="organization.sender_id_approved").exists()


def test_notifications_and_portal_invites_use_it(owner, admin):
    org = _approved(owner, admin)
    delivery._deliver(Message(organization=org, channel=catalog.SMS, to="+254712345678", body="Rent is due"))
    portal_services.send_invitation(SimpleNamespace(phone="+254712345678", organization=org), "https://x/accept")
    assert MemorySmsSender.senders == ["KAMAUHOMES", "KAMAUHOMES"]


def test_rejected_name_keeps_the_platform_name(owner, admin):
    sender_ids.request_name(owner, "KAMAUHOMES")
    org = Organization.objects.get(pk=owner.organization.pk)
    sender_ids.decide(admin, org, False, "Send us a letter on your letterhead.")
    org.refresh_from_db()
    assert org.sms_sender_status == Status.REJECTED and org.sms_sender_name == ""
    assert org.sms_sender_note == "Send us a letter on your letterhead."


def test_only_platform_admins_decide(owner):
    sender_ids.request_name(owner, "KAMAUHOMES")
    with pytest.raises(PermissionDenied):
        sender_ids.decide(owner.user, owner.organization, True)


def test_cancel_goes_back_to_the_platform_name(owner, admin):
    org = _approved(owner, admin)
    owner.organization = org
    sender_ids.cancel(owner)
    org.refresh_from_db()
    assert (org.sms_sender_id, org.sms_sender_status, org.sms_sender_name) == ("", Status.NONE, "")


# Africa's Talking --------------------------------------------------------------------------------------------------


@pytest.fixture
def at(settings):
    settings.AT_USERNAME, settings.AT_API_KEY, settings.AT_SENDER_ID = "sandbox", "key", "NYUMBAPMS"
    return AfricasTalkingSmsSender()


def test_at_sends_from_the_given_name_or_the_platforms(at, monkeypatch):
    sent = reply(at, monkeypatch, recipient(101))
    assert at.send("+254712345678", "Hi", sender_id="KAMAUHOMES").sender_id == "KAMAUHOMES"
    at.send("+254712345678", "Hi")
    assert [f["from"] for f in sent] == ["KAMAUHOMES", "NYUMBAPMS"]


def test_at_falls_back_when_a_network_refuses_the_name(at, monkeypatch):
    sent = []
    replies = [recipient(402, "InvalidSenderId"), recipient(101)]
    monkeypatch.setattr(at, "_post", lambda fields: sent.append(fields) or replies.pop(0))
    result = at.send("+254733000000", "Hi", sender_id="KAMAUHOMES")
    assert result.ok and result.sender_id == "NYUMBAPMS"
    assert [f["from"] for f in sent] == ["KAMAUHOMES", "NYUMBAPMS"]


def test_at_falls_back_when_the_whole_request_refuses_the_name(at, monkeypatch):
    sent = []
    replies = [{"SMSMessageData": {"Message": "InvalidSenderId", "Recipients": []}}, recipient(101)]
    monkeypatch.setattr(at, "_post", lambda fields: sent.append(fields) or replies.pop(0))
    result = at.send("+254733000000", "Hi", sender_id="KAMAUHOMES")
    assert result.ok and [f["from"] for f in sent] == ["KAMAUHOMES", "NYUMBAPMS"]


def test_at_does_not_resend_other_failures(at, monkeypatch):
    sent = reply(at, monkeypatch, recipient(403, "InvalidPhoneNumber"))
    assert not at.send("+254700", "Hi", sender_id="KAMAUHOMES").ok
    assert len(sent) == 1


# Pages -------------------------------------------------------------------------------------------------------------


def test_the_subscription_page_asks_and_cancels(client, owner):
    login(client, owner)
    page = client.get(reverse("subscriptions:page")).content.decode()
    assert "SMS sender name" in page and "Ask for this name" in page
    response = client.post(reverse("subscriptions:sender_name"), {"name": "KAMAUHOMES"}, follow=True)
    assert "You asked for <strong>KAMAUHOMES</strong>" in response.content.decode()
    response = client.post(reverse("subscriptions:sender_name"), {"name": "bad name!"}, follow=True)
    assert "3 to 11 letters" in response.content.decode()
    client.post(reverse("subscriptions:sender_name"), {"action": "cancel"})
    assert Organization.objects.get(pk=owner.organization.pk).sms_sender_status == Status.NONE


def test_caretakers_cannot_ask(client, owner):
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    client.login(username=caretaker.user.phone, password=PASSWORD)
    assert client.post(reverse("subscriptions:sender_name"), {"name": "KAMAUHOMES"}).status_code == 403


def test_admin_action_approves(client, owner, admin, monkeypatch):
    from accounts import mfa

    monkeypatch.setattr(mfa, "is_enabled", lambda user: True)
    monkeypatch.setattr(mfa, "session_verified", lambda request: True)
    sender_ids.request_name(owner, "KAMAUHOMES")
    client.login(username=admin.phone, password=PASSWORD)
    client.post(reverse("admin:accounts_organization_changelist"),
                {"action": "approve_sender_ids", "_selected_action": [owner.organization.pk]})
    assert Organization.objects.get(pk=owner.organization.pk).sms_sender_name == "KAMAUHOMES"
