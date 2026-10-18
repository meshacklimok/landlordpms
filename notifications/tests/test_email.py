"""Email to tenants (D-076): the copy beside the phone message, what it holds, stopping it, and invoice PDFs."""

import datetime
import smtplib
from zoneinfo import ZoneInfo

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from audit.models import AuditEvent
from billing.models import Invoice
from billing.tests.test_invoicing import FEB, bill, make_lease
from notifications import announcements, catalog, delivery, emails, rendering, services, triggers
from notifications.announcements import Audience
from notifications.models import ConsentRecord, Message, OrganizationNotificationRule
from payments.tests.test_services import pay

pytestmark = pytest.mark.django_db

EAT = ZoneInfo("Africa/Nairobi")
NOON = datetime.datetime(2026, 2, 3, 12, 0, tzinfo=EAT)
Status = Message.Status
Skip = Message.SkipReason


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.SITE_URL = "https://pms.example"


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def org(owner):
    return owner.organization


@pytest.fixture
def lease(owner, org):
    return make_lease(owner, make_property(org))


@pytest.fixture
def tenant(lease):
    tenant = lease.primary_tenant
    tenant.email = "wanjiku@example.com"
    tenant.save()
    return tenant


def announce(org, tenant, text="Water is off on Friday.\n\nSorry for the trouble.", key="a:1"):
    return delivery.notify(org, "announcement", tenant=tenant, context={"text": text, "tenant_name": tenant.name},
                           now=NOON, dedupe_key=key)


def copies(org):
    return Message.objects.filter(organization=org, channel=catalog.EMAIL)


# ---------------------------------------------------------------------------
# The copy
# ---------------------------------------------------------------------------


def test_catalog_gives_tenant_types_an_email_copy():
    assert catalog.get("invoice_issued").channels[-1] == catalog.EMAIL
    assert catalog.email_copy(catalog.get("announcement"))
    assert not catalog.email_copy(catalog.get("payment_pending_review"))
    assert catalog.get("rent_overdue").subject(catalog.SW)


def test_copy_is_written_beside_the_sms(org, tenant):
    sms = announce(org, tenant)
    assert sms.channel == catalog.SMS
    email = copies(org).get()
    assert email.dedupe_key == f"{sms.dedupe_key}|email" and email.to == "wanjiku@example.com"
    assert email.subject and email.status == Status.QUEUED
    assert email.body.startswith(f"Dear {tenant.name},\n\nWater is off on Friday.\n\nSorry for the trouble.")
    assert "https://pms.example/e/" in email.body
    assert announce(org, tenant) is None and copies(org).count() == 1  # dedupe holds for both


def test_messages_without_a_key_each_get_their_copy(org, tenant):
    announce(org, tenant, key="")
    announce(org, tenant, key="")
    assert list(copies(org).values_list("dedupe_key", flat=True)) == ["", ""]


def test_no_address_means_no_row(org, lease):
    announce(org, lease.primary_tenant)
    assert not copies(org).exists()


def test_switched_off_for_the_type(org, tenant):
    OrganizationNotificationRule.objects.create(organization=org, type="announcement",
                                                channels=[catalog.SMS])
    announce(org, tenant)
    assert not copies(org).exists()


def test_stopped_email_writes_a_skipped_copy_and_the_sms_still_goes(org, tenant):
    ConsentRecord.objects.create(organization=org, tenant=tenant, channel=catalog.EMAIL, granted=False,
                                 source=ConsentRecord.Source.EMAIL_LINK)
    assert announce(org, tenant).status == Status.QUEUED
    email = copies(org).get()
    assert (email.status, email.skip_reason) == (Status.SKIPPED, Skip.NO_CONSENT)


def test_keep_lines_rendering():
    text = "Hello {name},\n\n\n\nRent   is {amount}.\n{missing}\nBye"
    assert rendering.render(text, {"name": "W", "amount": "5"}, keep_lines=True) == \
        "Hello W,\n\nRent is 5.\n\nBye"
    assert rendering.render(text, {"name": "W", "amount": "5"}) == "Hello W, Rent is 5. Bye"


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def test_sent_email_has_sender_name_reply_to_and_unsubscribe(org, tenant):
    org.reply_email = "office@acacia.example"
    org.save()
    announce(org, tenant)
    email = copies(org).get()
    assert delivery.send_one(email.pk, NOON) == Status.SENT
    [sent] = mail.outbox
    assert sent.to == ["wanjiku@example.com"] and sent.subject == email.subject
    assert org.display_name.split()[0] in sent.from_email
    assert sent.reply_to == ["office@acacia.example"]
    link = sent.extra_headers["List-Unsubscribe"].strip("<>")
    assert link.startswith("https://pms.example/e/")
    # The footer is signed when the message is queued and the header when it is sent: two valid tokens for one tenant.
    body_link = next(w for w in sent.body.split() if w.startswith("https://pms.example/e/"))
    for url in (link, body_link):
        assert emails.tenant_for_token(url.rstrip("/").rsplit("/", 1)[1]) == tenant
    assert sent.extra_headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert not sent.attachments


def test_invoice_email_carries_the_pdf(org, lease, tenant):
    invoice = bill(lease, FEB)
    triggers.invoice_issued(invoice)
    email = copies(org).get(invoice=invoice)
    delivery.send_one(email.pk, NOON)
    [(name, content, mimetype)] = mail.outbox[0].attachments
    assert name == f"{invoice.number}.pdf" and mimetype == "application/pdf" and content.startswith(b"%PDF-")


def test_payment_email_carries_the_receipt(owner, org, lease, tenant):
    payment = pay(owner, lease, "5000")
    email = copies(org).get(payment=payment)
    delivery.send_one(email.pk, NOON)
    [(name, content, _mimetype)] = mail.outbox[0].attachments
    assert name == f"{payment.receipt.number}.pdf" and content.startswith(b"%PDF-")


def test_refused_address_fails_for_good(monkeypatch, org, tenant):
    def refuse(self, *a, **kw):
        raise smtplib.SMTPRecipientsRefused({"wanjiku@example.com": (550, b"No such user")})

    monkeypatch.setattr("django.core.mail.EmailMessage.send", refuse)
    announce(org, tenant)
    email = copies(org).get()
    assert delivery.send_one(email.pk, NOON) == Status.FAILED
    email.refresh_from_db()
    assert email.attempts == 1 and email.provider == "email"


# ---------------------------------------------------------------------------
# Stopping
# ---------------------------------------------------------------------------


def test_stop_link(client, org, tenant):
    url = reverse("email_stop", args=[emails.stop_token(tenant)])
    r = client.get(url)
    assert r.status_code == 200 and r["X-Robots-Tag"].startswith("noindex")
    assert services.channel_allowed(tenant, catalog.EMAIL)  # GET changes nothing
    assert client.post(url).status_code == 200
    assert not services.channel_allowed(tenant, catalog.EMAIL)
    assert ConsentRecord.objects.get(tenant=tenant, channel=catalog.EMAIL).source == ConsentRecord.Source.EMAIL_LINK
    assert AuditEvent.objects.filter(action="tenant.consent", object_id=str(tenant.public_id)).exists()
    assert client.post(url).status_code == 200 and not services.stop_email_by_link(tenant)
    assert ConsentRecord.objects.filter(tenant=tenant, channel=catalog.EMAIL).count() == 1


def test_bad_token_is_404(client, tenant):
    assert client.get(reverse("email_stop", args=["nope"])).status_code == 404
    forged = emails.stop_token(tenant)[:-2] + "xx"
    assert client.post(reverse("email_stop", args=[forged])).status_code == 404
    assert services.channel_allowed(tenant, catalog.EMAIL)


def test_staff_stop_and_resume_email(client, owner, tenant):
    login(client, owner)
    url = reverse("notifications:tenant_channel", args=[tenant.public_id, "email"])
    assert client.post(url).status_code == 302
    assert not services.channel_allowed(tenant, catalog.EMAIL)
    assert "Resume emails" in client.get(reverse("tenants:detail", args=[tenant.public_id])).content.decode()
    client.post(url, {"allowed": "on"})
    assert services.channel_allowed(tenant, catalog.EMAIL)


# ---------------------------------------------------------------------------
# Settings and announcements
# ---------------------------------------------------------------------------


def test_email_alone_is_refused(owner):
    with pytest.raises(ValidationError):
        services.save_rule(owner, "announcement", enabled=True, channels=[catalog.EMAIL])


def test_settings_email_box_and_reply_to(client, owner, org):
    login(client, owner)
    url = reverse("notifications:settings")
    client.post(url, {"type": "announcement", "enabled": "on", "action": "save"})
    rule = OrganizationNotificationRule.objects.get(organization=org, type="announcement")
    assert catalog.EMAIL not in rule.channels and catalog.SMS in rule.channels
    client.post(url, {"type": "announcement", "enabled": "on", "email": "on", "action": "save"})
    assert delivery.effective_rule(org, catalog.get("announcement")).channels[-1] == catalog.EMAIL

    assert client.post(url, {"action": "reply_email", "reply_email": "Office@Acacia.Example"}).status_code == 302
    org.refresh_from_db()
    assert org.reply_email == "office@acacia.example"
    assert client.post(url, {"action": "reply_email", "reply_email": "not an email"}).status_code == 400


def test_reply_email_needs_manage(org):
    accountant = add_member(org, "accountant", all_properties=True)
    with pytest.raises(Exception):  # noqa: B017 - PermissionDenied
        services.set_reply_email(accountant, "x@example.com")


def test_announcement_preview_counts_emails(owner, org, lease, tenant):
    other = make_lease(owner, lease.unit.property, code="B2")
    assert other.primary_tenant.email == ""
    result = announcements.preview(owner, "Water is off.", Audience(), datetime.date(2026, 2, 3))
    assert result.email == 1


# ---------------------------------------------------------------------------
# Invoice PDFs
# ---------------------------------------------------------------------------


def test_staff_invoice_pdf(client, owner, org, lease):
    invoice = bill(lease, FEB)
    url = reverse("billing:invoice_pdf", args=[invoice.public_id])
    login(client, owner)
    r = client.get(url)
    assert r.status_code == 200 and r["Content-Type"] == "application/pdf" and r.content.startswith(b"%PDF-")
    login(client, make_org())
    assert client.get(url).status_code == 404


def test_void_invoice_pdf_renders(client, owner, lease):
    invoice = bill(lease, FEB)
    Invoice.objects.filter(pk=invoice.pk).update(status=Invoice.Status.VOID)
    login(client, owner)
    assert client.get(reverse("billing:invoice_pdf", args=[invoice.public_id])).content.startswith(b"%PDF-")


def test_email_does_not_wait_for_quiet_hours(org, tenant):
    late = datetime.datetime(2026, 2, 3, 22, 30, tzinfo=EAT)
    sms = delivery.notify(org, "announcement", tenant=tenant, context={"text": "Hi", "tenant_name": tenant.name},
                          now=late, dedupe_key="late")
    assert sms.send_after is not None
    assert copies(org).get().send_after is None


def test_invoice_pdf_with_logo_and_extra_details(client, owner, org, lease, tmp_path, settings):
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image as PILImage

    from accounts import services as account_services

    buf = io.BytesIO()
    PILImage.new("RGB", (200, 80), "#1D4E89").save(buf, "PNG")
    assert account_services.set_logo(owner, upload=SimpleUploadedFile("logo.png", buf.getvalue(), "image/png"))
    account_services.update_organization(
        owner, address="Kimathi House, Nairobi", brand_color="#0B7A5A", invoice_payment_info="Paybill 123456\nBank: Equity",
        invoice_terms="Rent is due by the 5th.", invoice_show_tenant_contact=True)
    invoice = bill(lease, FEB)
    login(client, owner)
    r = client.get(reverse("billing:invoice_pdf", args=[invoice.public_id]))
    assert r.status_code == 200 and r.content.startswith(b"%PDF-")
    assert account_services.set_logo(owner, remove=True)
    org.refresh_from_db()
    assert not org.logo


def test_invoice_pdf_shows_earlier_unpaid_and_stamp(client, owner, org, lease):
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image as PILImage

    from accounts import services as account_services
    from billing.pdf import _earlier_unpaid

    buf = io.BytesIO()
    PILImage.new("RGB", (120, 60), "#222222").save(buf, "PNG")
    assert account_services.set_stamp(owner, upload=SimpleUploadedFile("s.png", buf.getvalue(), "image/png"))
    jan = bill(lease, FEB - datetime.timedelta(days=31))
    feb = bill(lease, FEB)
    assert [i.pk for i, _o in _earlier_unpaid(feb)] == [jan.pk]
    assert _earlier_unpaid(jan) == []
    login(client, owner)
    r = client.get(reverse("billing:invoice_pdf", args=[feb.public_id]))
    assert r.status_code == 200 and r.content.startswith(b"%PDF-")
    assert account_services.set_stamp(owner, remove=True)
