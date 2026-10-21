"""Landlords to call about an unpaid subscription, soonest to lapse first.

An account that has missed its payment is read-only after the grace days. Reminders go out by email on their
own, but a phone call or a WhatsApp message from a person recovers far more of them, so this is the list to work
through each morning: who, how much, how long they have left, and a link to message them.
"""

import datetime
from decimal import Decimal
from urllib.parse import quote

from django.db.models import Prefetch
from django.utils import timezone

from accounts.models import Membership
from subscriptions import services
from subscriptions.models import Subscription, SubscriptionInvoice

Status = Subscription.Status


def whatsapp_link(phone: str, text: str) -> str:
    """A wa.me link to a Kenyan number (07.., 7.., 254.. or +254..), or blank when it is not a number."""
    digits = "".join(c for c in phone or "" if c.isdigit())
    if digits.startswith("0"):
        digits = "254" + digits[1:]
    elif len(digits) == 9:
        digits = "254" + digits
    return f"https://wa.me/{digits}?text={quote(text)}" if len(digits) >= 11 else ""


def _message(name: str, org: str, amount: Decimal, left: int | None) -> str:
    first = (name or "").split(" ")[0] or "there"
    if left is None:
        end = "is read-only until it is paid. All your records are safe."
    elif left <= 0:
        end = "becomes read-only very soon."
    else:
        end = f"becomes read-only in {left} days."
    return (f"Hello {first}, this is NyumbaPMS. The subscription for {org} is unpaid (KES {amount:,.0f}) and the "
            f"account {end} Can I help you pay it today?")


def rows(today: datetime.date | None = None) -> list[dict]:
    """One row per organization whose subscription is past due or lapsed. Past due first, fewest days left first,
    then the lapsed ones, longest unpaid first."""
    today = today or timezone.localdate()
    open_invoices = SubscriptionInvoice.objects.filter(status=SubscriptionInvoice.Status.OPEN).prefetch_related("payments")
    subs = list(Subscription.objects.filter(status__in=[Status.PAST_DUE, Status.LAPSED])
                .filter(organization__archived_at__isnull=True)
                .select_related("organization", "plan")
                .prefetch_related(Prefetch("invoices", queryset=open_invoices, to_attr="open_invoices")))
    owners = {}
    for m in (Membership.objects.filter(organization__in=[s.organization_id for s in subs], is_active=True,
                                        role__is_owner_role=True).select_related("user").order_by("-pk")):
        owners[m.organization_id] = m  # the oldest owner wins
    out = []
    for sub in subs:
        org = sub.organization
        owner = owners.get(org.pk)
        user = owner.user if owner else None
        amount = sum((i.outstanding for i in sub.open_invoices), Decimal("0"))
        since = sub.past_due_since
        overdue = (today - since).days if since else None
        left = None
        if sub.status == Status.PAST_DUE:
            left = services.grace_days() - (overdue or 0)
            if sub.grace_until and sub.grace_until >= today:
                left = max(left, (sub.grace_until - today).days)
        phone = (user.phone if user else "") or org.billing_phone
        out.append({
            "org": org, "sub": sub, "owner": owner, "name": user.full_name if user else "", "phone": phone,
            "email": (user.email if user else "") or org.billing_email,
            "lapsed": sub.status == Status.LAPSED, "overdue": overdue, "left": left, "amount": amount,
            "invoice": sub.open_invoices[0] if sub.open_invoices else None,
            "disputed": any(i.disputed_at for i in sub.open_invoices),
            "extended_to": sub.grace_until if sub.grace_until and sub.grace_until >= today else None,
            "whatsapp": whatsapp_link(phone, _message(user.full_name if user else "", org.name, amount, left)),
        })
    return sorted(out, key=lambda r: (r["lapsed"], r["left"] if r["left"] is not None else 0,
                                      -(r["overdue"] or 0), r["org"].name))
