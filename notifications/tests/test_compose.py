"""Messages staff write for one tenant (D-125): invoices, receipts and notes by email, SMS or WhatsApp."""

import datetime
import uuid
from zoneinfo import ZoneInfo

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from audit.models import AuditEvent
from billing.models import Invoice
from billing.tests.test_invoicing import FEB, bill, make_lease
from notifications import catalog, compose, delivery
from notifications.models import ConsentRecord, Message, MessageTemplate
from notifications.providers.sms import MemorySmsSender
from notifications.providers.whatsapp import MemoryWhatsAppSender
from payments.tests.test_services import pay

pytestmark = pytest.mark.django_db

EAT = ZoneInfo("Africa/Nairobi")
NOON = datetime.datetime(2026, 2, 3, 12, 0, tzinfo=EAT)
NIGHT = datetime.datetime(2026, 2, 3, 23, 0, tzinfo=EAT)


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.SITE_URL = "https://pms.example"


@pytest.fixture
def wa(settings):
    settings.WHATSAPP_BACKEND = "notifications.providers.whatsapp.MemoryWhatsAppSender"
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
def prop(org):
    return make_property(org)


@pytest.fixture
def lease(owner, prop):
    return make_lease(owner, prop)


@pytest.fixture
def tenant(lease):
    tenant = lease.primary_tenant
    tenant.email = "wanjiku@example.com"
    tenant.save()
    return tenant


@pytest.fixture
def invoice(lease):
    return bill(lease, FEB)


def send(actor, topic, tenant, channel, **kw):
    kw.setdefault("text", "Hello")
    kw.setdefault("subject", "Hello" if channel == catalog.EMAIL else "")
    kw.setdefault("now", NOON)
    message, created = compose.send(actor, topic, tenant, channel, **kw)
    delivery.send_one(message.pk, NOON)
    message.refresh_from_db()
    return message, created


# ---------------------------------------------------------------------------
# The starting text
# ---------------------------------------------------------------------------


def test_invoice_draft_is_the_invoice_wording_filled_in(org, invoice, tenant):
    topic = compose.for_invoice(invoice)
    subject, text = compose.draft(org, topic, tenant, catalog.EMAIL)
    assert subject == f"Invoice {invoice.number} from Acme"
    assert text.startswith(f"Dear {tenant.name},") and invoice.number in text and "15,000" in text
    _subject, sms = compose.draft(org, topic, tenant, catalog.SMS)
    assert invoice.number in sms and not sms.endswith("Acme")


def test_draft_uses_the_organizations_own_wording(owner, org, invoice, tenant):
    MessageTemplate.objects.create(organization=org, type="invoice_issued", channel=catalog.SMS,
                                   language=catalog.EN, body="Hi {tenant_name}, pay {amount}. {org_name}")
    _subject, text = compose.draft(org, compose.for_invoice(invoice), tenant, catalog.SMS)
    assert text == f"Hi {tenant.name}, pay KES 15,000.00."


def test_receipt_and_note_drafts(owner, org, lease, tenant):
    payment = pay(owner, lease, "5000")
    subject, text = compose.draft(org, compose.for_payment(payment), tenant, catalog.EMAIL)
    assert payment.receipt.number in subject and "5,000" in text
    subject, text = compose.draft(org, compose.for_tenant(owner, tenant), tenant, catalog.EMAIL)
    assert subject == "Message from Acme" and text.startswith(f"Dear {tenant.name},")
    assert compose.draft(org, compose.for_tenant(owner, tenant), tenant, catalog.SMS) == ("", "")


def test_draft_and_void_invoices_cannot_be_sent(invoice):
    Invoice.objects.filter(pk=invoice.pk).update(status=Invoice.Status.VOID)
    invoice.refresh_from_db()
    with pytest.raises(ValidationError):
        compose.for_invoice(invoice)


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def test_email_goes_with_the_staff_words_and_the_invoice_pdf(owner, org, invoice, tenant):
    message, created = send(owner, compose.for_invoice(invoice), tenant, catalog.EMAIL,
                            subject="Your February rent", text="Dear Wanjiku,\n\nPlease see the invoice.")
    assert created and message.status == Message.Status.SENT and message.type == "tenant_message"
    assert message.invoice == invoice and message.created_by == owner.user
    [email] = mail.outbox
    assert email.subject == "Your February rent" and email.to == ["wanjiku@example.com"]
    assert email.body.startswith("Dear Wanjiku,\n\nPlease see the invoice.") and "/e/" in email.body
    [(name, content, _mimetype)] = email.attachments
    assert name == f"{invoice.number}.pdf" and content.startswith(b"%PDF-")
    assert AuditEvent.objects.filter(action="message.composed", organization=org).exists()


def test_receipt_email_carries_the_receipt(owner, lease, tenant):
    payment = pay(owner, lease, "5000")
    mail.outbox.clear()
    send(owner, compose.for_payment(payment), tenant, catalog.EMAIL)
    [(name, _content, _mimetype)] = mail.outbox[0].attachments
    assert name == f"{payment.receipt.number}.pdf"


def test_sms_adds_the_organization_name(owner, invoice, tenant):
    message, _ = send(owner, compose.for_invoice(invoice), tenant, catalog.SMS, text="Rent is due on 5th.")
    assert message.body == "Rent is due on 5th. Acme"
    assert MemorySmsSender.outbox == [(tenant.phone, "Rent is due on 5th. Acme")]


def test_whatsapp_goes_with_the_announcement_template(owner, org, invoice, tenant, wa):
    ConsentRecord.objects.create(organization=org, tenant=tenant, channel=catalog.WHATSAPP, granted=True,
                                 source=ConsentRecord.Source.STAFF)
    message, _ = send(owner, compose.for_invoice(invoice), tenant, catalog.WHATSAPP, text="Line one\nLine two")
    assert message.status == Message.Status.SENT
    [(to, template, language, params)] = wa
    assert to == tenant.phone and template == catalog.whatsapp_template(catalog.get("announcement"))
    assert language == catalog.EN and params == ["Acme", "Line one Line two"]


def test_whatsapp_needs_the_tenants_agreement(owner, invoice, tenant, wa):
    with pytest.raises(ValidationError, match="WhatsApp"):
        send(owner, compose.for_invoice(invoice), tenant, catalog.WHATSAPP)


def test_a_stopped_channel_is_not_used(owner, org, invoice, tenant):
    ConsentRecord.objects.create(organization=org, tenant=tenant, channel=catalog.EMAIL, granted=False,
                                 source=ConsentRecord.Source.EMAIL_LINK)
    with pytest.raises(ValidationError, match="Email"):
        send(owner, compose.for_invoice(invoice), tenant, catalog.EMAIL)
    assert not Message.objects.filter(organization=org, type="tenant_message").exists()


def test_no_address_cannot_be_emailed(owner, invoice, lease):
    tenant = lease.primary_tenant
    assert compose.channel_reasons(lease.organization, tenant)[catalog.EMAIL] == Message.SkipReason.NO_ADDRESS
    with pytest.raises(ValidationError):
        send(owner, compose.for_invoice(invoice), tenant, catalog.EMAIL)


def test_text_checks(owner, invoice, tenant):
    topic = compose.for_invoice(invoice)
    with pytest.raises(ValidationError, match="subject"):
        compose.send(owner, topic, tenant, catalog.EMAIL, text="Hi", subject="")
    with pytest.raises(ValidationError, match="Write the message"):
        compose.send(owner, topic, tenant, catalog.SMS, text="  ")
    with pytest.raises(ValidationError, match="organization name"):
        compose.send(owner, topic, tenant, catalog.SMS, text="x" * 500)


def test_same_form_twice_sends_once(owner, invoice, tenant):
    nonce = uuid.uuid4()
    first, created = send(owner, compose.for_invoice(invoice), tenant, catalog.SMS, nonce=nonce)
    again, created_again = compose.send(owner, compose.for_invoice(invoice), tenant, catalog.SMS, text="Hello",
                                        nonce=nonce)
    assert created and not created_again and again == first and len(MemorySmsSender.outbox) == 1


def test_sms_waits_for_quiet_hours_unless_urgent(owner, org, invoice, tenant):
    org.quiet_hours_start, org.quiet_hours_end = datetime.time(21), datetime.time(7)
    org.save()
    waiting, _ = compose.send(owner, compose.for_invoice(invoice), tenant, catalog.SMS, text="Hi", now=NIGHT)
    assert waiting.send_after is not None
    now, _ = compose.send(owner, compose.for_invoice(invoice), tenant, catalog.SMS, text="Hi", now=NIGHT,
                          urgent=True)
    assert now.send_after is None


def test_only_tenants_on_the_lease(owner, org, invoice, prop):
    other = make_lease(owner, prop, code="B2").primary_tenant
    with pytest.raises(ValidationError, match="not on this lease"):
        compose.send(owner, compose.for_invoice(invoice), other, catalog.SMS, text="Hi")


def test_staff_need_messages_send_for_the_property(org, invoice, tenant, prop):
    accountant = add_member(org, "accountant", all_properties=True)
    with pytest.raises(PermissionDenied):
        compose.send(accountant, compose.for_invoice(invoice), tenant, catalog.SMS, text="Hi")
    elsewhere = add_member(org, "manager", properties=[make_property(org)])
    with pytest.raises(PermissionDenied):
        compose.send(elsewhere, compose.for_invoice(invoice), tenant, catalog.SMS, text="Hi")
    manager = add_member(org, "manager", properties=[prop])
    message, created = compose.send(manager, compose.for_invoice(invoice), tenant, catalog.SMS, text="Hi")
    assert created and message.created_by == manager.user


def test_staff_messages_are_not_on_the_settings_or_wording_pages():
    ntype = catalog.get("tenant_message")
    assert ntype.composed
    from notifications import services

    assert services.editable_channels(ntype) == []


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


def test_page_starts_with_the_invoice_email_and_sends(client, owner, org, invoice, tenant):
    login(client, owner)
    url = reverse("notifications:compose") + f"?invoice={invoice.public_id}"
    r = client.get(url)
    assert r.status_code == 200
    body = r.content.decode()
    assert f"Invoice {invoice.number} from Acme" in body and "the invoice PDF is attached" in body
    r = client.post(url, {"nonce": str(uuid.uuid4()), "to": str(tenant.public_id), "channel": catalog.EMAIL,
                          "subject": "February", "text": "See attached."})
    message = Message.objects.get(organization=org, type="tenant_message")
    assert r.status_code == 302 and r["Location"] == reverse("notifications:detail", args=[message.public_id])
    assert message.subject == "February" and message.invoice == invoice


def test_page_shows_why_a_channel_cannot_be_used(client, owner, invoice, tenant):
    login(client, owner)
    r = client.get(reverse("notifications:compose") + f"?invoice={invoice.public_id}&channel=WHATSAPP")
    assert r.status_code == 200 and "cannot reach this tenant" in r.content.decode()


def test_page_is_hidden_without_the_capability(client, org, invoice):
    accountant = add_member(org, "accountant", all_properties=True)
    login(client, accountant)
    assert client.get(reverse("notifications:compose") + f"?invoice={invoice.public_id}").status_code in (403, 404)


def test_buttons_on_the_invoice_payment_and_tenant_pages(client, owner, lease, invoice, tenant):
    login(client, owner)
    compose_url = reverse("notifications:compose")
    page = client.get(reverse("billing:invoice", args=[invoice.public_id])).content.decode()
    assert f"{compose_url}?invoice={invoice.public_id}" in page
    payment = pay(owner, lease, "5000")
    page = client.get(reverse("payments:detail", args=[payment.public_id])).content.decode()
    assert f"{compose_url}?payment={payment.public_id}" in page
    page = client.get(reverse("tenants:detail", args=[tenant.public_id])).content.decode()
    assert f"{compose_url}?tenant={tenant.public_id}" in page
