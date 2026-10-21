"""Late fees (D-129): the landlord picks a fixed amount or a share of what is unpaid, staff approve each one.

The daily job proposes one fee per invoice that went overdue after late fees were switched on. Nothing is
charged until a member who can issue invoices approves it; the fee is then worked out again from what is
still unpaid and goes out as an invoice of its own, due the same day plus the lease's grace days. A fee whose
invoice was paid in the meantime is skipped on its own.
"""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership, Organization
from accounts.permissions import can, require
from audit import services as audit
from core.money import ZERO, format_money, round_money
from leases.models import Lease
from leases.services import visible_leases
from notifications import triggers

from .invoicing import _issue, _lock_lease
from .models import ChargeType, Invoice, InvoiceLine, LateFee

PAID_MEANWHILE = "Paid before it was charged."
SWITCHED_OFF = "Late fees were switched off."


def fee_for(org: Organization, unpaid: Decimal) -> Decimal:
    """The fee on `unpaid` under the organization's rule, never more than its maximum. Zero when off."""
    if unpaid <= 0:
        return ZERO
    mode = org.late_fee_mode
    if mode == Organization.LateFeeMode.FIXED:
        fee = org.late_fee_amount or ZERO
    elif mode == Organization.LateFeeMode.PERCENT:
        fee = round_money(unpaid * (org.late_fee_percent or ZERO) / 100)
    else:
        return ZERO
    if org.late_fee_cap is not None:
        fee = min(fee, org.late_fee_cap)
    return max(round_money(fee), ZERO)


def describe_rule(org: Organization) -> str:
    """"KES 500 per late invoice" or "5% of what is unpaid, at most KES 2,000"."""
    mode = org.late_fee_mode
    if mode == Organization.LateFeeMode.FIXED:
        text = _("%(amount)s per late invoice") % {"amount": format_money(org.late_fee_amount or ZERO,
                                                                          org.currency)}
    elif mode == Organization.LateFeeMode.PERCENT:
        text = _("%(percent)s%% of what is unpaid") % {"percent": f"{(org.late_fee_percent or ZERO).normalize():f}"}
    else:
        return _("No late fees")
    if org.late_fee_cap is not None:
        text += ", " + _("at most %(cap)s") % {"cap": format_money(org.late_fee_cap, org.currency)}
    return text


def late_fee_charge_type(org: Organization) -> ChargeType:
    """The organization's system "Late fee" charge type, made on first use."""
    category = ChargeType.Category.LATE_FEE
    qs = ChargeType.all_objects.for_org(org).filter(is_system=True, category=category)
    found = qs.first()
    if found is not None:
        return found
    name = str(category.label)
    taken = ChargeType.all_objects.for_org(org).filter(name__iexact=name).exists()
    try:
        with transaction.atomic():
            return ChargeType.objects.create(organization=org, name=name if not taken else f"{name} (system)",
                                             category=category, is_system=True)
    except IntegrityError:
        return qs.get()  # made at the same time


# ---------------------------------------------------------------------------
# The daily proposal
# ---------------------------------------------------------------------------


def candidates(org: Organization, today: datetime.date):
    """Invoices overdue today that went overdue after late fees were switched on and have no fee yet.

    An invoice that is itself a late fee never gets one, so fees never pile on fees.
    """
    if org.late_fee_mode == Organization.LateFeeMode.OFF or org.late_fees_from is None:
        return Invoice.objects.none()
    fee_invoices = InvoiceLine.objects.filter(organization=org, charge_type__category=ChargeType.Category.LATE_FEE)
    return (Invoice.objects.for_org(org).filter(status__in=Invoice.OPEN, overdue_after__lt=today,
                                                overdue_after__gte=org.late_fees_from, total__gt=F("amount_paid"))
            .exclude(lease__status=Lease.Status.DRAFT).filter(lease__archived_at__isnull=True)
            .filter(late_fee__isnull=True).exclude(pk__in=fee_invoices.values("invoice")))


def _skip_stale(org: Organization, today: datetime.date) -> int:
    """Pending fees that no longer apply: the invoice was paid or voided, or late fees were switched off."""
    pending = LateFee.objects.for_org(org).filter(status=LateFee.Status.PENDING)
    if org.late_fee_mode == Organization.LateFeeMode.OFF:
        stale = {pk: SWITCHED_OFF for pk in pending.values_list("pk", flat=True)}
    else:
        paid = pending.exclude(invoice__status__in=Invoice.OPEN) | pending.filter(
            invoice__amount_paid__gte=F("invoice__total"))
        stale = {pk: PAID_MEANWHILE for pk in paid.values_list("pk", flat=True)}
    for pk, reason in stale.items():
        with transaction.atomic():
            fee = LateFee.objects.select_for_update().filter(pk=pk, status=LateFee.Status.PENDING).first()
            if fee is not None:
                _decide(fee, LateFee.Status.SKIPPED, reason=reason, user=None, request=None, source="daily job")
    return len(stale)


def propose(org: Organization, today: datetime.date) -> int:
    """Adds a pending fee for each new candidate; returns how many were added."""
    added = 0
    for invoice in candidates(org, today).order_by("overdue_after", "pk"):
        amount = fee_for(org, invoice.outstanding)
        if amount <= 0:
            continue
        try:
            with transaction.atomic():
                LateFee.objects.create(organization=org, lease_id=invoice.lease_id, invoice=invoice,
                                       unpaid=invoice.outstanding, amount=amount)
        except IntegrityError:
            continue  # proposed at the same time
        added += 1
    return added


def run_daily(today: datetime.date) -> dict[str, int]:
    counts = {"late_fees_proposed": 0, "late_fees_skipped": 0}
    orgs = Organization.objects.filter(archived_at__isnull=True, status=Organization.Status.ACTIVE)
    from subscriptions.services import blocked_modules

    for org in orgs:
        if "invoices" in blocked_modules(org):
            continue  # paused for an overdue subscription (D-205)
        counts["late_fees_skipped"] += _skip_stale(org, today)
        counts["late_fees_proposed"] += propose(org, today)
    return counts


# ---------------------------------------------------------------------------
# Approving and skipping
# ---------------------------------------------------------------------------


def visible_fees(membership: Membership):
    return LateFee.objects.for_org(membership.organization).filter(
        lease__in=visible_leases(membership, Lease.all_objects.all()))


def to_approve(membership: Membership):
    """Pending fees on the member's properties, oldest overdue first."""
    return (visible_fees(membership).filter(status=LateFee.Status.PENDING)
            .select_related("invoice", "lease__unit__property").order_by("invoice__overdue_after", "pk"))


def _decide(fee: LateFee, status: str, *, reason: str, user, request, source: str = "", **extra) -> None:
    before = fee.status
    fee.status = status
    fee.reason = reason[:300]
    fee.decided_by = user
    fee.decided_at = timezone.now()
    fee.save(update_fields=["status", "reason", "decided_by", "decided_at", "updated_at", *extra.pop("fields", [])])
    changes = {"status": [before, status], "invoice": [None, fee.invoice.number], "amount": [None, str(fee.amount)]}
    if reason:
        changes["reason"] = [None, reason[:300]]
    if source:
        changes["source"] = [None, source]
    audit.record("late_fee.charge" if status == LateFee.Status.CHARGED else "late_fee.skip", actor=user,
                 organization=fee.organization, obj=fee, request=request, changes=changes)


def _lock(actor: Membership, fee: LateFee) -> LateFee:
    if fee.organization_id != actor.organization_id:
        raise PermissionDenied
    require(actor, "invoices.generate", property=fee.lease.unit.property)
    _lock_lease(fee.lease)
    fee = LateFee.objects.select_for_update(of=("self",)).select_related("invoice", "organization").get(pk=fee.pk)
    if fee.status != LateFee.Status.PENDING:
        raise ValidationError(_("This fee was already %(status)s.") % {"status": fee.get_status_display().lower()})
    return fee


def approve(actor: Membership, fee: LateFee, *, today: datetime.date | None = None, request=None) -> Invoice | None:
    """Charges the fee as an issued invoice of its own. Returns None when it was skipped because nothing is unpaid."""
    today = today or timezone.localdate()
    with transaction.atomic():
        fee = _lock(actor, fee)
        org = fee.organization
        if org.late_fee_mode == Organization.LateFeeMode.OFF:
            raise ValidationError(_("Late fees are switched off in Settings."))
        invoice = Invoice.objects.select_for_update().get(pk=fee.invoice_id)
        amount = fee_for(org, invoice.outstanding) if invoice.status in Invoice.OPEN else ZERO
        if amount <= 0:
            _decide(fee, LateFee.Status.SKIPPED, reason=PAID_MEANWHILE, user=actor.user, request=request)
            return None
        lease = fee.lease
        fee_invoice = Invoice.objects.create(
            organization=org, lease=lease, period_start=invoice.period_start, period_end=invoice.period_end,
            due_date=today, overdue_after=today + datetime.timedelta(days=lease.grace_days),
            currency=invoice.currency, subtotal=amount, tax_total=ZERO, total=amount, created_by=actor.user,
            notes=_("Late fee on %(number)s") % {"number": invoice.number})
        InvoiceLine.objects.create(
            organization=org, invoice=fee_invoice, lease=lease, charge_type=late_fee_charge_type(org),
            description=_("Late fee: %(number)s, %(unpaid)s unpaid after %(day)s") % {
                "number": invoice.number, "unpaid": format_money(invoice.outstanding, invoice.currency),
                "day": f"{invoice.overdue_after:%d %b %Y}"},
            quantity=1, unit_price=amount, amount=amount)
        _issue(fee_invoice, actor_user=actor.user, today=today)
        audit.record("invoice.issue", actor=actor.user, organization=org, obj=fee_invoice, request=request,
                     changes={"number": [None, fee_invoice.number], "lease": [None, lease.number],
                              "late_fee_on": [None, invoice.number], "total": [None, str(amount)]})
        fee.unpaid, fee.amount, fee.fee_invoice = invoice.outstanding, amount, fee_invoice
        _decide(fee, LateFee.Status.CHARGED, reason="", user=actor.user, request=request,
                fields=["unpaid", "amount", "fee_invoice"])
        transaction.on_commit(lambda: triggers.invoice_issued(fee_invoice))
    return fee_invoice


def skip(actor: Membership, fee: LateFee, *, reason: str = "", request=None) -> None:
    with transaction.atomic():
        fee = _lock(actor, fee)
        _decide(fee, LateFee.Status.SKIPPED, reason=reason.strip(), user=actor.user, request=request)


@dataclass
class Batch:
    charged: list[Invoice] = field(default_factory=list)
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def decide_many(actor: Membership, fees, *, charge: bool, reason: str = "", today=None, request=None) -> Batch:
    """Charges or skips several fees; one that fails is reported and the rest go ahead."""
    out = Batch()
    for fee in fees:
        if not can(actor, "invoices.generate", property=fee.lease.unit.property):
            out.errors.append(_("%(invoice)s: not allowed on this property.") % {"invoice": fee.invoice.number})
            continue
        try:
            if charge:
                made = approve(actor, fee, today=today, request=request)
                if made is None:
                    out.skipped += 1
                else:
                    out.charged.append(made)
            else:
                skip(actor, fee, reason=reason, request=request)
                out.skipped += 1
        except ValidationError as exc:
            out.errors.append(f"{fee.invoice.number}: {' '.join(exc.messages)}")
    return out
