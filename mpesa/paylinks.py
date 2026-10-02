"""The tenant's payment link (D-046 item 1).

Each lease gets one private link, /p/<token>/, put in rent messages. The tenant (or anyone they
send it to) enters a Kenyan phone and any whole amount, and an M-Pesa prompt goes to that phone.
The money goes to the landlord's own Paybill (D-038); the payment is confirmed on the lease the
link belongs to, exactly like a request sent by staff.

The link only ever triggers a prompt that someone must approve with their PIN, so the risks are
nuisance prompts and cost, not lost money. Limits per link, per phone and per IP keep those small.
"""

import datetime
import secrets

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import require
from audit import services as audit
from billing.invoicing import lease_balance
from leases.models import Lease

from . import stk
from .models import PayLink, StkRequest

# Prompts one link may send: a few in a row (a wrong PIN, a retry), and a ceiling for the day.
PER_LINK_BURST, BURST_WINDOW = 3, datetime.timedelta(minutes=10)
PER_LINK_DAY = 10
# Prompts to one phone from any link of the organization in a day, so a link cannot pester a stranger.
PER_PHONE_DAY = 5
# Page loads and sends per IP per hour (the view checks it).
PER_IP_HOUR = 30

# Leases that ended still take payments while they owe money.
_CLOSED = (Lease.Status.ENDED, Lease.Status.TERMINATED)


def _token() -> str:
    return secrets.token_urlsafe(12)


def can_take_payments(lease: Lease) -> bool:
    """Whether a link for this lease works now."""
    if lease.archived_at is not None or not lease.organization.is_operational:
        return False
    if lease.status == Lease.Status.ACTIVE:
        return True
    return lease.status in _CLOSED and lease_balance(lease) > 0


def link_for(lease: Lease) -> PayLink | None:
    """The lease's working link, made the first time it is needed. None when it is turned off, the
    lease cannot take payments, or no Paybill can send prompts for the property."""
    existing = PayLink.objects.filter(lease=lease).first()
    if existing is not None and not existing.is_active:
        return None
    if not can_take_payments(lease) or not stk.stk_accounts(lease):
        return None
    if existing is not None:
        return existing
    link, _created = PayLink.objects.get_or_create(
        lease=lease, defaults={"organization_id": lease.organization_id, "token": _token()})
    return link if link.is_active else None


def url(link: PayLink) -> str:
    return settings.SITE_URL + reverse("pay_link", args=[link.token])


def message_url(lease: Lease) -> str:
    """The link for a rent message, or "" (the message then just leaves it out)."""
    link = link_for(lease)
    return url(link) if link else ""


def find(token: str) -> PayLink | None:
    """The working link behind a token, or None."""
    link = (PayLink.objects.select_related("lease__organization", "lease__unit__property")
            .filter(token=token, disabled_at__isnull=True).first())
    if link is None or not can_take_payments(link.lease):
        return None
    return link


# ---------------------------------------------------------------------------
# Staff: reset, turn off, turn on
# ---------------------------------------------------------------------------


def _check(actor: Membership, lease: Lease) -> None:
    if lease.organization_id != actor.organization_id:
        raise PermissionDenied(_("That record belongs to another organization."))
    require(actor, "payments.record", lease.unit.property)


def _change(actor: Membership, lease: Lease, action: str, *, request=None) -> PayLink:
    _check(actor, lease)
    with transaction.atomic():
        link = PayLink.objects.select_for_update().filter(lease=lease).first()
        if link is None:
            link = PayLink(organization_id=lease.organization_id, lease=lease, token=_token())
        if action == "reset":
            link.token, link.disabled_at = _token(), None
        elif action == "disable":
            link.disabled_at = link.disabled_at or timezone.now()
        else:
            link.disabled_at = None
        link.updated_by = actor.user
        link.save()
        audit.record(f"mpesa.pay_link_{action}", actor=actor.user, organization=lease.organization, obj=link,
                     request=request, changes={"lease": [None, lease.number]})
    return link


def reset(actor: Membership, lease: Lease, *, request=None) -> PayLink:
    """A new link; the old one stops working at once."""
    return _change(actor, lease, "reset", request=request)


def disable(actor: Membership, lease: Lease, *, request=None) -> PayLink:
    return _change(actor, lease, "disable", request=request)


def enable(actor: Membership, lease: Lease, *, request=None) -> PayLink:
    return _change(actor, lease, "enable", request=request)


# ---------------------------------------------------------------------------
# The tenant's side
# ---------------------------------------------------------------------------


def request_payment(link: PayLink, *, phone: str, amount, request=None) -> StkRequest:
    """Sends a prompt from the link. Limits are checked before anything is sent to Safaricom."""
    lease = link.lease
    now = timezone.now()
    sent = StkRequest.objects.filter(pay_link=link)
    if sent.filter(created_at__gte=now - BURST_WINDOW).count() >= PER_LINK_BURST:
        raise ValidationError(_("Several prompts were sent from this link just now. Try again in a few minutes."))
    if sent.filter(created_at__gte=now - datetime.timedelta(days=1)).count() >= PER_LINK_DAY:
        raise ValidationError(_("This link has sent as many prompts as it can today. Try again tomorrow, or pay "
                                "with the Paybill and account number."))
    number = stk._phone(phone)
    to_phone = StkRequest.objects.filter(organization_id=link.organization_id, pay_link__isnull=False, phone=number,
                                         created_at__gte=now - datetime.timedelta(days=1))
    if to_phone.count() >= PER_PHONE_DAY:
        raise ValidationError({"phone": _("This phone has had as many prompts as it can today.")})
    return stk.send(lease, phone=number, amount=amount, pay_link=link, request=request)
