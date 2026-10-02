"""Announcements: one notice to a chosen group of tenants (D-044 item 15).

The audience is the tenants on leases occupying a unit today, in the properties the member
can see, narrowed by property, building, money owed past its due date and lease end. Staff
preview who receives it and what it costs, then send; each recipient's copy is a `Message`.
"""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from accounts.models import Membership
from accounts.permissions import can, require
from audit import services as audit
from leases.models import Lease, LeaseTenant
from leases.services import occupying_leases, visible_leases

from . import catalog
from .delivery import effective_rule, notify
from .delivery import preview as preview_one
from .models import Announcement, Message
from .rendering import SMS_MAX, SMS_SEGMENT, fields_in, render

TYPE = "announcement"
# Fields a notice may use, filled per recipient.
FIELDS = ("tenant_name", "unit", "property")
# Batches up to this size are sent at once; larger ones go through `send_due_messages`.
BULK_INLINE = 25
MAX_DAYS = 365


@dataclass(frozen=True)
class Audience:
    """Who a notice goes to. Empty filters mean no narrowing."""

    properties: tuple = ()
    buildings: tuple = ()
    # Owing money that fell due at least this many days ago (None: no filter).
    owing_days: int | None = None
    # Lease contract ending within this many days (None: no filter).
    ending_within: int | None = None
    include_co_tenants: bool = True

    def as_json(self) -> dict:
        return {"properties": [p.pk for p in self.properties], "buildings": [b.pk for b in self.buildings],
                "owing_days": self.owing_days, "ending_within": self.ending_within,
                "include_co_tenants": self.include_co_tenants}

    def summary(self) -> str:
        parts = [", ".join(p.name for p in self.properties) if self.properties else _("All properties")]
        if self.buildings:
            parts.append(", ".join(b.name for b in self.buildings))
        if self.owing_days is not None:
            parts.append(ngettext("owing %(n)s+ day", "owing %(n)s+ days", self.owing_days)
                         % {"n": self.owing_days})
        if self.ending_within is not None:
            parts.append(ngettext("lease ending within %(n)s day", "lease ending within %(n)s days",
                                  self.ending_within) % {"n": self.ending_within})
        if not self.include_co_tenants:
            parts.append(_("main tenants only"))
        return " · ".join(parts)[:300]


@dataclass
class Recipient:
    tenant: object
    lease: Lease
    body: str = ""
    parts: int = 0
    skip_reason: str = ""
    channel: str = ""

    @property
    def skip_label(self) -> str:
        return Message.SkipReason(self.skip_reason).label if self.skip_reason else ""


@dataclass
class Preview:
    recipients: list[Recipient]
    sending: int = 0
    # Of those, how many go on WhatsApp (not counted in `parts` or `cost`).
    whatsapp: int = 0
    parts: int = 0
    cost: Decimal = Decimal("0")
    skipped: dict[str, int] = field(default_factory=dict)


def sms_parts(text: str) -> int:
    """How many SMS a text costs: one up to 160 characters, then 153 per part."""
    n = len(text)
    return 0 if not n else 1 if n <= SMS_SEGMENT else -(-n // 153)


def price_per_part() -> Decimal:
    return Decimal(str(getattr(settings, "SMS_PRICE_ESTIMATE", "0.80")))


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def can_target_owing(membership: Membership) -> bool:
    """Choosing tenants by what they owe reveals balances, so it needs invoices.view."""
    return can(membership, "invoices.view")


def validate_text(org, text: str) -> str:
    text = (text or "").strip()
    if not text:
        raise ValidationError({"text": _("Write the message.")})
    unknown = sorted(set(fields_in(text)) - set(FIELDS))
    if unknown:
        raise ValidationError({"text": _("Unknown fields: %(fields)s. You can use: %(allowed)s.") % {
            "fields": ", ".join("{" + f + "}" for f in unknown),
            "allowed": ", ".join("{" + f + "}" for f in FIELDS)}})
    room = SMS_MAX - len(org.display_name) - 1
    if len(text) > room:
        raise ValidationError({"text": _("Keep it under %(n)s characters (your organization name is added).")
                               % {"n": room}})
    return text


def _check_audience(actor: Membership, audience: Audience) -> None:
    for days in (audience.owing_days, audience.ending_within):
        if days is not None and not 0 <= days <= MAX_DAYS:
            raise ValidationError(_("Days must be between 0 and %(n)s.") % {"n": MAX_DAYS})
    if audience.owing_days is not None and not can_target_owing(actor):
        raise ValidationError(_("You cannot choose tenants by what they owe."))
    for obj in (*audience.properties, *audience.buildings):
        if obj.organization_id != actor.organization_id:
            raise ValidationError(_("That property belongs to another organization."))
        prop = getattr(obj, "property", obj)
        if not can(actor, "messages.send_bulk", prop):
            raise ValidationError(_("You cannot send to %(name)s.") % {"name": prop.name})


# ---------------------------------------------------------------------------
# Audience
# ---------------------------------------------------------------------------


def _owing_lease_ids(actor: Membership, days: int, today: datetime.date) -> set[int]:
    from billing.selectors import arrears

    return {row.lease.pk for row in arrears(actor, today) if row.days_overdue(today) >= max(days, 1)}


def recipients(actor: Membership, audience: Audience, today: datetime.date | None = None) -> list[Recipient]:
    """One entry per tenant, by property and unit. The primary tenant first on each lease."""
    today = today or timezone.localdate()
    leases = visible_leases(actor, occupying_leases(today))
    if audience.properties:
        leases = leases.filter(unit__property__in=audience.properties)
    if audience.buildings:
        leases = leases.filter(unit__building__in=audience.buildings)
    if audience.ending_within is not None:
        leases = leases.filter(status=Lease.Status.ACTIVE, end_date__gte=today,
                               end_date__lte=today + datetime.timedelta(days=audience.ending_within))
    if audience.owing_days is not None:
        leases = leases.filter(pk__in=_owing_lease_ids(actor, audience.owing_days, today))
    links = (LeaseTenant.objects.filter(lease__in=leases, tenant__archived_at__isnull=True)
             .select_related("tenant", "lease__unit__property")
             .order_by("lease__unit__property__name", "lease__unit__code", "-is_primary", "tenant__name"))
    if not audience.include_co_tenants:
        links = links.filter(is_primary=True)
    seen, out = set(), []
    for link in links:
        if link.tenant_id not in seen:
            seen.add(link.tenant_id)
            out.append(Recipient(tenant=link.tenant, lease=link.lease))
    return out


def personal_text(text: str, recipient: Recipient) -> str:
    unit = recipient.lease.unit
    return render(text, {"tenant_name": recipient.tenant.name, "unit": unit.code, "property": unit.property.name})


def _context(org, text: str, recipient: Recipient) -> dict:
    unit = recipient.lease.unit
    return {"tenant_name": recipient.tenant.name, "unit": unit.code, "property": unit.property.name,
            "org_name": org.display_name, "text": personal_text(text, recipient)}


def preview(actor: Membership, text: str, audience: Audience, today: datetime.date | None = None) -> Preview:
    """Who would receive it, who would be skipped and why, and the SMS it would take. Writes nothing."""
    require(actor, "messages.send_bulk")
    org = actor.organization
    text = validate_text(org, text)
    _check_audience(actor, audience)
    rule = effective_rule(org, catalog.get(TYPE))
    result = Preview(recipients=recipients(actor, audience, today))
    for r in result.recipients:
        chosen, reason = preview_one(org, TYPE, tenant=r.tenant, rule=rule)
        if chosen is None:
            r.skip_reason = reason
            result.skipped[reason] = result.skipped.get(reason, 0) + 1
            continue
        channel, template = chosen
        r.channel = channel
        r.body = render(template, _context(org, text, r))
        r.parts = sms_parts(r.body) if channel == catalog.SMS else 0
        result.sending += 1
        result.whatsapp += channel == catalog.WHATSAPP
        result.parts += r.parts
    result.cost = (price_per_part() * result.parts).quantize(Decimal("0.01"))
    return result


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


@transaction.atomic
def send(actor: Membership, text: str, audience: Audience, *, nonce=None, urgent: bool = False,
         today: datetime.date | None = None, now: datetime.datetime | None = None,
         request=None) -> tuple[Announcement, bool]:
    """Writes the announcement and one message per recipient. Returns (announcement, created).

    `nonce` is the form's one-time id: sending the same form twice returns the first announcement.
    """
    require(actor, "messages.send_bulk")
    org = actor.organization
    text = validate_text(org, text)
    _check_audience(actor, audience)
    if nonce is not None:
        existing = Announcement.objects.filter(public_id=nonce).first()
        if existing is not None:
            if existing.organization_id != org.pk:
                raise ValidationError(_("That form was already used. Start again."))
            return existing, False
    people = recipients(actor, audience, today)
    if not people:
        raise ValidationError(_("No tenants match. Change who it goes to."))
    fields = {"public_id": nonce} if nonce is not None else {}
    try:
        with transaction.atomic():
            announcement = Announcement.objects.create(
                organization=org, text=text, summary=audience.summary(), audience=audience.as_json(),
                recipient_count=len(people), urgent=urgent, created_by=actor.user, **fields)
    except IntegrityError:
        existing = Announcement.objects.filter(public_id=nonce, organization=org).first()
        if existing is None:
            raise
        return existing, False
    send_now = len(people) <= BULK_INLINE
    for r in people:
        notify(org, TYPE, tenant=r.tenant, context=_context(org, text, r), lease=r.lease, announcement=announcement,
               dedupe_key=f"{TYPE}:{announcement.pk}:{r.tenant.pk}", created_by=actor.user, urgent=urgent,
               now=now, send_now=send_now)
    audit.record("announcement.sent", organization=org, obj=announcement, request=request, actor=actor.user,
                 changes={"recipients": [None, len(people)], "audience": [None, announcement.summary]})
    return announcement, True


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def visible_announcements(membership: Membership):
    """Members with every property see all announcements; scoped members see the ones they sent."""
    qs = Announcement.objects.for_org(membership.organization)
    return qs if membership.all_properties else qs.filter(created_by=membership.user)
