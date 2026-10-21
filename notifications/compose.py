"""Messages staff write for one tenant (D-125): an invoice, a receipt or a note, on the channel they choose.

The text starts as the wording the automatic message uses, and staff change it before sending.
Email carries the invoice or receipt PDF. WhatsApp goes with the approved announcement template,
so any wording can be sent there. The tenant's stops and consent apply as for any other message,
and each send is a `Message` in the log.
"""

import datetime
import re
import uuid
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import can, require
from audit import services as audit
from leases.models import Lease
from leases.services import occupying_leases, visible_leases

from . import catalog, emails, triggers
from .delivery import _NOISY, _address, _blocked, quiet_until, send_one, whatsapp_call
from .models import Message, MessageTemplate
from .rendering import SMS_MAX, render, template_for

TYPE = "tenant_message"
CHANNELS = (catalog.EMAIL, catalog.SMS, catalog.WHATSAPP)
EMAIL_MAX = 5000
# Meta allows 1024 characters in one template field; the rest is room for the template's own words.
WHATSAPP_MAX = 900
# The automatic message each topic starts from.
BASE_TYPE = {"invoice": "invoice_issued", "receipt": "payment_received"}


@dataclass
class Topic:
    """What the message is about: an invoice, a payment, or only the tenant (with their lease, if any)."""

    lease: Lease | None
    invoice: object = None
    payment: object = None
    tenant: object = None
    welcome: bool = False  # a note that starts as a welcome with room for the house rules

    @property
    def kind(self) -> str:
        return "invoice" if self.invoice else "receipt" if self.payment else "note"

    @property
    def property(self):
        return self.lease.unit.property if self.lease else None

    def tenants(self) -> list:
        """Who it may go to: the tenant asked about, else the lease's tenants, the payer or main tenant first."""
        if self.tenant is not None:
            return [self.tenant]
        first = self.payment.tenant if self.payment is not None else None
        return triggers.lease_recipients(self.lease, include_co_tenants=True, first=first)


def for_invoice(invoice) -> Topic:
    if invoice.status in (invoice.Status.DRAFT, invoice.Status.VOID):
        raise ValidationError(_("Only an issued invoice can be sent."))
    return Topic(lease=triggers._lease(invoice.lease), invoice=invoice)


def for_payment(payment) -> Topic:
    if getattr(payment, "receipt", None) is None:
        raise ValidationError(_("Only a payment with a receipt can be sent."))
    return Topic(lease=triggers._lease(payment.lease), payment=payment)


def for_tenant(membership: Membership, tenant, *, welcome: bool = False) -> Topic:
    """The tenant's lease today, if the member can see it, fills the unit and property."""
    lease = (visible_leases(membership, occupying_leases()).filter(lease_tenants__tenant=tenant)
             .select_related("unit__property").order_by("-start_date", "-pk").first())
    return Topic(lease=lease, tenant=tenant, welcome=welcome)


def can_send(membership: Membership, topic: Topic) -> bool:
    return can(membership, "messages.send", topic.property)


def channel_reasons(org, tenant) -> dict[str, str | None]:
    """For each channel, why it cannot reach this tenant (a skip reason), or None if it can."""
    ntype = catalog.get(TYPE)
    return {channel: _blocked(org, ntype, channel, tenant, None) for channel in CHANNELS}


# ---------------------------------------------------------------------------
# The starting text
# ---------------------------------------------------------------------------


def _context(org, topic: Topic, tenant) -> dict:
    context = {"org_name": org.display_name, "tenant_name": tenant.name}
    if topic.lease is not None:
        context.update(triggers.tenancy_fields(topic.lease, tenant))
    if topic.invoice is not None:
        context.update(triggers.invoice_fields(topic.invoice, topic.lease))
    if topic.payment is not None:
        context.update(triggers.payment_fields(topic.payment, topic.lease))
    return context


def draft(org, topic: Topic, tenant, channel: str) -> tuple[str, str]:
    """(subject, text) to start from: the organization's wording for the automatic message, filled in.

    Phone text leaves out the organization's name, which is added when it is sent.
    """
    language = tenant.language or catalog.EN
    context = _context(org, topic, tenant)
    if topic.welcome and topic.lease is not None:
        return _welcome(org, topic, tenant, channel, context)
    base =catalog.get(BASE_TYPE.get(topic.kind, "announcement"))
    if channel == catalog.EMAIL:
        subject = render(emails.subject(org, base, language), context)[:200]
        if topic.kind == "note":
            return subject, render(base.default_body(catalog.EMAIL, language), {**context, "text": ""},
                                   keep_lines=True)
        return subject, render(template_for(org, base, catalog.EMAIL, language) or "", context, keep_lines=True)
    if topic.kind == "note":
        return "", ""
    text = render(template_for(org, base, catalog.SMS, language) or "", context)
    return "", text.removesuffix(org.display_name).rstrip()


def _welcome(org, topic: Topic, tenant, channel: str, context: dict) -> tuple[str, str]:
    """An optional welcome for a new tenant.

    It starts from the wording staff last sent (kept for the organization), else a plain one with room for the
    house rules. Staff change it before sending, and what they send becomes the wording for the next tenant.
    """
    where = f"{context['property']}, {context['unit']}"
    saved = _saved_welcome(org, channel, tenant.language or catalog.EN)
    if channel != catalog.EMAIL:
        text = saved or "Hi {tenant_name}, welcome to {property}, {unit}. Your payment reference is {pay_reference}."
        return "", render(text, context)
    subject = f"Welcome to {context['property']}"
    if saved:
        return subject, render(saved, context, keep_lines=True)
    text = (f"Hi {tenant.name},\n\nWelcome to {where}. We are glad to have you.\n\n"
            f"To pay {org.vocab['rent'].lower()}, use the payment reference {context['pay_reference']}.\n\n"
            "House rules:\n- \n- \n- \n\n"
            f"If you need anything, reply to this email.\n\n{org.display_name}")
    return subject, text


def _saved_welcome(org, channel: str, language: str) -> str:
    found = {t.language: t.body for t in MessageTemplate.objects.filter(
        organization=org, type=TYPE, channel=channel, language__in={language, catalog.EN})}
    return found.get(language) or found.get(catalog.EN) or ""


def _as_template(text: str, context: dict) -> str:
    """The text with this tenant's own details turned back into fields, so the next tenant gets theirs."""
    text = text.replace("{", "{{").replace("}", "}}")
    for field in ("tenant_name", "pay_reference", "property", "org_name", "unit"):
        value = str(context.get(field) or "")
        if value:
            text = re.sub(rf"(?<!\w){re.escape(value)}(?!\w)", "{" + field + "}", text)
    return text


def _remember_welcome(actor: Membership, topic: Topic, tenant, channel: str, text: str) -> None:
    if channel not in (catalog.EMAIL, catalog.SMS):
        return
    body = _as_template(text, _context(actor.organization, topic, tenant))
    MessageTemplate.objects.update_or_create(
        organization=actor.organization, type=TYPE, channel=channel, language=tenant.language or catalog.EN,
        defaults={"body": body, "updated_by": actor.user})


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def _check_text(org, channel: str, subject: str, text: str) -> None:
    if not text:
        raise ValidationError({"text": _("Write the message.")})
    if channel == catalog.EMAIL:
        if not subject:
            raise ValidationError({"subject": _("Write a subject.")})
        if len(text) > EMAIL_MAX:
            raise ValidationError({"text": _("Keep it under %(n)s characters.") % {"n": EMAIL_MAX}})
    elif channel == catalog.SMS:
        room = SMS_MAX - len(org.display_name) - 1
        if len(text) > room:
            raise ValidationError({"text": _("Keep it under %(n)s characters (your organization name is added).")
                                   % {"n": room}})
    elif len(text) > WHATSAPP_MAX:
        raise ValidationError({"text": _("Keep it under %(n)s characters.") % {"n": WHATSAPP_MAX}})


def _fields(org, tenant, channel: str, subject: str, text: str, language: str) -> dict:
    if channel == catalog.SMS:
        return {"body": f"{text} {org.display_name}"}
    if channel == catalog.WHATSAPP:
        announcement = catalog.get("announcement")
        template = announcement.default_body(catalog.WHATSAPP, language)
        context = {"org_name": org.display_name, "text": text}
        return {"body": render(template, context, keep_lines=True),
                "provider_template": whatsapp_call(announcement, template, language, context)}
    return {"subject": subject[:200], "body": f"{text}\n\n{emails.footer(tenant, language)}"}


@transaction.atomic
def send(actor: Membership, topic: Topic, tenant, channel: str, *, text: str, subject: str = "",
         nonce: uuid.UUID | None = None, urgent: bool = False, now: datetime.datetime | None = None,
         request=None) -> tuple[Message, bool]:
    """Writes the message and sends it after commit. Returns (message, created).

    `nonce` is the form's one-time id: sending the same form twice returns the first message.
    """
    org = actor.organization
    require(actor, "messages.send", topic.property)
    if tenant not in topic.tenants():
        raise ValidationError(_("That tenant is not on this lease."))
    if channel not in CHANNELS:
        raise ValidationError(_("Choose email, SMS or WhatsApp."))
    dedupe_key = f"{TYPE}:{nonce}" if nonce is not None else ""
    if dedupe_key:
        existing = Message.objects.filter(organization=org, dedupe_key=dedupe_key).first()
        if existing is not None:
            return existing, False
    reason = channel_reasons(org, tenant)[channel]
    if reason:
        raise ValidationError(_("Cannot send by %(channel)s: %(reason)s.") % {
            "channel": dict(catalog.CHANNEL_CHOICES)[channel], "reason": Message.SkipReason(reason).label.lower()})
    subject, text = (subject or "").strip(), (text or "").strip()
    _check_text(org, channel, subject, text)

    now = now or timezone.now()
    language = tenant.language or catalog.EN
    try:
        with transaction.atomic():
            message = Message.objects.create(
                organization=org, type=TYPE, channel=channel, tenant=tenant, to=_address(channel, tenant, None),
                language=language, status=Message.Status.QUEUED,
                send_after=None if urgent or channel not in _NOISY else quiet_until(org, now),
                lease=topic.lease, invoice=topic.invoice, payment=topic.payment, dedupe_key=dedupe_key,
                created_by=actor.user, **_fields(org, tenant, channel, subject, text, language))
    except IntegrityError:
        existing = Message.objects.filter(organization=org, dedupe_key=dedupe_key).first() if dedupe_key else None
        if existing is None:
            raise
        return existing, False
    if topic.welcome:
        _remember_welcome(actor, topic, tenant, channel, text)
    if message.send_after is None:
        transaction.on_commit(lambda: send_one(message.pk))
    audit.record("message.composed", actor=actor.user, organization=org, obj=message, request=request,
                 changes={"channel": [None, channel], "about": [None, topic.kind], "to": [None, tenant.name]})
    return message, True
