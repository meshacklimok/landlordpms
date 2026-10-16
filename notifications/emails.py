"""Emails to tenants (D-076): the stop link, the footer, attachments and sending.

Email is a copy of the phone message, written by `delivery.notify` when the tenant has an address.
The link in the footer, and the List-Unsubscribe header, stop emails from that organization.
"""

import logging
import smtplib
from email.utils import formataddr, parseaddr

from django.conf import settings
from django.core import signing
from django.core.mail import EmailMultiAlternatives
from django.urls import reverse

from core.mail import MESSAGES, mailer_for
from core.sms import SmsResult

from . import catalog
from .email_html import render_html

logger = logging.getLogger(__name__)

SALT = "notifications.email-stop"

FOOTER = {
    catalog.EN: "You get this email because you rent from {org}. To stop these emails, open: {url}",
    catalog.SW: "Unapokea barua pepe hii kwa sababu unapangisha kutoka {org}. Kusitisha barua pepe hizi, "
                "fungua: {url}",
}


# ---------------------------------------------------------------------------
# The stop link
# ---------------------------------------------------------------------------


def stop_token(tenant) -> str:
    """Signed, without expiry: an old email's link must still work."""
    return signing.dumps(str(tenant.public_id), salt=SALT)


def tenant_for_token(token: str):
    """The tenant the link was made for, or None if the token is not ours."""
    from tenants.models import Tenant

    try:
        public_id = signing.loads(token, salt=SALT)
    except signing.BadSignature:
        return None
    return Tenant.all_objects.select_related("organization").filter(public_id=public_id).first()


def stop_url(tenant) -> str:
    return settings.SITE_URL + reverse("email_stop", args=[stop_token(tenant)])


# What estates, water vendors and garbage collectors say instead (D-130). English only: the Swahili stays as it is
# until a native speaker has checked words for each kind of biller. [VERIFY]
FOOTER_BILLED = "You get this email because {org} bills you. To stop these emails, open: {url}"


def footer(tenant, language: str) -> str:
    org = tenant.organization
    text = FOOTER.get(language) or FOOTER[catalog.EN]
    if language == catalog.EN and not org.bills_rent:
        text = FOOTER_BILLED
    return "-- \n" + text.format(org=org.display_name, url=stop_url(tenant))


def subject(org, ntype, language: str) -> str:
    """The subject line of a type, with "Rent" swapped for the organization's word for it (D-130)."""
    text = ntype.subject(language)
    if language == catalog.EN and not org.bills_rent and text.startswith("Rent "):
        text = f"{org.vocab['rent']} {text[5:]}"
    return text


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def from_address(org, own=None) -> str:
    """The organization's own address if it set up a mail server (`own`), else the platform's under its name."""
    if own is not None:
        return formataddr((" ".join((own.from_name or org.display_name).split()), own.from_email))
    address = parseaddr(settings.DEFAULT_FROM_EMAIL)[1] or settings.DEFAULT_FROM_EMAIL
    return formataddr((" ".join(org.display_name.split()), address))


def attachments(message) -> list[tuple[str, bytes, str]]:
    """The invoice PDF for an invoice, the stored receipt for a payment. A PDF that cannot be made is left out.

    A message staff wrote about an invoice or payment carries it too (D-125).
    """
    try:
        if message.type in ("invoice_issued", "tenant_message") and message.invoice_id:
            from billing.pdf import render_invoice

            invoice = message.invoice
            return [(f"{invoice.number or 'invoice'}.pdf", render_invoice(invoice), "application/pdf")]
        if message.type in ("payment_received", "tenant_message") and message.payment_id:
            from payments.models import Receipt

            receipt = Receipt.objects.filter(payment_id=message.payment_id).first()
            if receipt is not None and receipt.pdf:
                with receipt.pdf.open("rb") as f:
                    return [(f"{receipt.number}.pdf", f.read(), "application/pdf")]
    except Exception:  # the email still goes, without the PDF
        logger.exception("Message %s: attachment could not be made", message.pk)
    return []


def send(message) -> SmsResult:
    org = message.organization
    headers = {}
    if message.tenant_id:
        url = stop_url(message.tenant)
        headers = {"List-Unsubscribe": f"<{url}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
    from .own_accounts import connection, usable_email

    own = usable_email(org)
    email = EmailMultiAlternatives(message.subject or str(catalog.get(message.type).label), message.body,
                                   from_address(org, own), [message.to], headers=headers,
                                   reply_to=[org.reply_email] if org.reply_email else None)
    email.attach_alternative(render_html(org.display_name, message.body), "text/html")
    for name, content, mimetype in attachments(message):
        email.attach(name, content, mimetype)
    try:
        connection(own).send_messages([email]) if own is not None else email.send(using=mailer_for(MESSAGES))
    except smtplib.SMTPRecipientsRefused as e:  # the address is wrong: trying again cannot help
        return SmsResult(ok=False, provider="email", error=str(e) or "Address refused", permanent=True)
    return SmsResult(ok=True, provider="email")
