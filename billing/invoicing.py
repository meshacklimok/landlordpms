"""Invoices and the rent ledger (doc 11 §8, doc 14 A2–A3, D-042).

Monthly generation is idempotent: every generated line carries its billing month, and
(lease, billing_month, charge_type) is unique among lines that are not void. Running the job
again, or two people pressing Generate at once, adds only what is missing.

Invoices go out `invoice_lead_days` before the month (rent is paid in advance), are due on the
lease's due day and become overdue after its grace days. Partial months are prorated by days
unless the organization bills them in full.
"""

import calendar
import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership, Organization
from accounts.permissions import can, require
from audit import services as audit
from core.money import ZERO, days_in_month, format_money, parse_money, round_money
from core.numbering import next_number
from leases.models import Lease

from .models import ChargeType, Invoice, InvoiceLine, LedgerEntry
from .services import ensure_default_charge_types

DAY = datetime.timedelta(days=1)


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


def month_start(day: datetime.date) -> datetime.date:
    return day.replace(day=1)


def month_end(day: datetime.date) -> datetime.date:
    return day.replace(day=days_in_month(day))


def next_month(day: datetime.date) -> datetime.date:
    return month_end(day) + DAY


def months_to_bill(org: Organization, today: datetime.date) -> list[datetime.date]:
    """This month, plus next month once we are within the organization's lead days of it."""
    months = [month_start(today)]
    upcoming = next_month(today)
    if today >= upcoming - datetime.timedelta(days=org.invoice_lead_days):
        months.append(upcoming)
    return months


def _fmt(day: datetime.date) -> str:
    return day.strftime("%d %b %Y")


# ---------------------------------------------------------------------------
# What a lease owes for a month
# ---------------------------------------------------------------------------


@dataclass
class DraftLine:
    charge_type: ChargeType
    description: str
    amount: Decimal
    service_start: datetime.date
    service_end: datetime.date


@dataclass
class _Piece:
    start: datetime.date
    end: datetime.date
    monthly: Decimal


@dataclass
class _Charge:
    charge_type: ChargeType
    label: str
    pieces: list[_Piece] = field(default_factory=list)


def billed_span(lease: Lease, month: datetime.date) -> tuple[datetime.date, datetime.date] | None:
    """The days of `month` the lease covers, or None."""
    first, last = month_start(month), month_end(month)
    start = max(first, lease.start_date)
    end = min(last, lease.effective_end) if lease.effective_end else last
    return (start, end) if start <= end else None


def _rent_pieces(lease: Lease, start: datetime.date, end: datetime.date) -> list[_Piece]:
    changes = list(lease.rent_changes.order_by("effective_from"))
    pieces = []
    for i, change in enumerate(changes):
        nxt = changes[i + 1].effective_from if i + 1 < len(changes) else None
        s = max(start, change.effective_from)
        e = min(end, nxt - DAY) if nxt else end
        if s <= e:
            pieces.append(_Piece(s, e, change.amount))
    return pieces


def _amount(charge: _Charge, month: datetime.date, prorate: bool) -> Decimal:
    """Exact sum of the pieces, rounded once: rounding happens at the line (doc 11 §25)."""
    if not prorate:
        # Full-month billing: the rate on the first billed day, for the whole month.
        return round_money(charge.pieces[0].monthly)
    days = days_in_month(month)
    exact = sum(Decimal((p.end - p.start).days + 1) * p.monthly / days for p in charge.pieces)
    return round_money(exact)


def _describe(charge: _Charge, month: datetime.date, prorate: bool) -> str:
    first, last = month_start(month), month_end(month)
    name = f"{charge.label} {month.strftime('%B %Y')}"
    if not prorate:
        return name
    pieces = charge.pieces
    if len(pieces) == 1 and (pieces[0].start, pieces[0].end) == (first, last):
        return name
    parts = [f"{p.start.day}–{p.end.day} {p.end.strftime('%b')} at {format_money(p.monthly)}/month" for p in pieces]
    return f"{name} ({'; '.join(parts)})"[:200]


def lines_for_month(lease: Lease, month: datetime.date) -> list[DraftLine]:
    """Rent and recurring charges for one month, prorated for a partial month (doc 14 A3)."""
    span = billed_span(lease, month)
    if span is None:
        return []
    start, end = span
    org = lease.organization
    prorate = org.prorate_partial_months
    rent_type = rent_charge_type(org)

    charges: list[_Charge] = []
    rent = _Charge(rent_type, _("Rent"), _rent_pieces(lease, start, end))
    if rent.pieces:
        charges.append(rent)
    by_type: dict[int, _Charge] = {}
    for c in lease.charges.select_related("charge_type").order_by("active_from"):
        s, e = max(start, c.active_from), min(end, c.active_to) if c.active_to else end
        if s > e:
            continue
        entry = by_type.setdefault(c.charge_type_id, _Charge(c.charge_type, c.charge_type.name))
        entry.pieces.append(_Piece(s, e, c.amount))
    charges.extend(by_type.values())

    lines = []
    for charge in charges:
        if not prorate and charge.pieces[0].start != start:
            # Full-month billing charges what applies from the first billed day only.
            continue
        amount = _amount(charge, month, prorate)
        if amount > 0:
            lines.append(DraftLine(charge.charge_type, _describe(charge, month, prorate), amount,
                                   charge.pieces[0].start, charge.pieces[-1].end))
    return lines


def rent_charge_type(org: Organization) -> ChargeType:
    ensure_default_charge_types(org)
    return ChargeType.all_objects.for_org(org).get(is_system=True, category=ChargeType.Category.RENT)


# ---------------------------------------------------------------------------
# Generating and issuing
# ---------------------------------------------------------------------------


def _due_date(lease: Lease, month: datetime.date, start: datetime.date, issued: datetime.date) -> datetime.date:
    """The lease's due day in the billed month, but never before the tenant moves in or the invoice exists."""
    due = month.replace(day=min(lease.due_day, calendar.monthrange(month.year, month.month)[1]))
    return max(due, start, issued)


def _issue(invoice: Invoice, *, actor_user=None, today: datetime.date) -> None:
    """Numbers the invoice and posts it to the ledger. Runs in the caller's transaction."""
    invoice.number = next_number(invoice.organization, "invoice", prefix="INV", period=str(today.year))
    invoice.status = Invoice.Status.ISSUED
    invoice.issue_date = today
    invoice.issued_at = timezone.now()
    invoice.save(update_fields=["number", "status", "issue_date", "issued_at", "updated_at"])
    if invoice.total > 0:
        LedgerEntry.objects.create(organization=invoice.organization, lease=invoice.lease, entry_date=today,
                                   kind=LedgerEntry.Kind.INVOICE, amount=invoice.total, currency=invoice.currency,
                                   invoice=invoice, created_by=actor_user)


def _lock_lease(lease: Lease) -> Lease:
    return Lease.all_objects.select_for_update().select_related("organization").get(pk=lease.pk)


def _billable(lease: Lease) -> bool:
    return lease.status != Lease.Status.DRAFT and lease.archived_at is None


def generate_lease_month(lease: Lease, month: datetime.date, *, actor=None, today=None,
                         request=None) -> Invoice | None:
    """Bills whatever of `month` is not billed yet for one lease, as one issued invoice.

    Returns None when there is nothing new to bill. Safe to call any number of times.
    """
    today = today or timezone.localdate()
    month = month_start(month)
    with transaction.atomic():
        lease = _lock_lease(lease)
        if not _billable(lease):
            return None
        billed = set(InvoiceLine.objects.filter(lease=lease, billing_month=month, is_void=False)
                     .values_list("charge_type_id", flat=True))
        lines = [ln for ln in lines_for_month(lease, month) if ln.charge_type.pk not in billed]
        if not lines:
            return None
        start = min(ln.service_start for ln in lines)
        end = max(ln.service_end for ln in lines)
        due = _due_date(lease, month, start, today)
        subtotal = sum((ln.amount for ln in lines), ZERO)
        invoice = Invoice.objects.create(
            organization=lease.organization, lease=lease, period_start=start, period_end=end, due_date=due,
            overdue_after=due + datetime.timedelta(days=lease.grace_days), currency=lease.currency,
            subtotal=subtotal, tax_total=ZERO, total=subtotal,
            created_by=actor.user if actor else None)
        try:
            with transaction.atomic():
                InvoiceLine.objects.bulk_create([
                    InvoiceLine(organization=lease.organization, invoice=invoice, lease=lease,
                                charge_type=ln.charge_type, description=ln.description, quantity=1,
                                unit_price=ln.amount, amount=ln.amount, service_start=ln.service_start,
                                service_end=ln.service_end, billing_month=month)
                    for ln in lines])
        except IntegrityError:
            # Billed by someone else after our check; the lease lock makes this a backstop only.
            raise ValidationError(_("This month was billed at the same time. Refresh and try again.")) from None
        _issue(invoice, actor_user=actor.user if actor else None, today=today)
        audit.record("invoice.issue", actor=actor.user if actor else None, organization=lease.organization,
                     obj=invoice, request=request, changes={
                         "number": [None, invoice.number], "lease": [None, lease.number],
                         "month": [None, month.strftime("%Y-%m")], "total": [None, str(invoice.total)],
                         **({} if actor else {"source": [None, "monthly job"]}),
                     })
    return invoice


def billable_leases(org: Organization, month: datetime.date):
    """Issued leases that cover at least one day of `month`."""
    first, last = month_start(month), month_end(month)
    return (Lease.objects.for_org(org).exclude(status=Lease.Status.DRAFT).filter(start_date__lte=last)
            .annotate(last_day=Coalesce("ended_on", "end_date"))
            .filter(Q(last_day__isnull=True) | Q(last_day__gte=first)))


@dataclass
class RunResult:
    invoices: list[Invoice] = field(default_factory=list)
    errors: list[tuple[Lease, str]] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return sum((i.total for i in self.invoices), ZERO)


def generate_month(org: Organization, month: datetime.date, *, actor: Membership | None = None,
                   today=None, request=None) -> RunResult:
    """Bills one month for every lease the actor may bill (every lease when run by the job)."""
    if actor is not None:
        if actor.organization_id != org.pk:
            raise PermissionDenied(_("That record belongs to another organization."))
        if not can(actor, "invoices.generate"):
            raise PermissionDenied(_("You don't have permission to generate invoices."))
    elif not org.is_operational:
        return RunResult()
    result = RunResult()
    for lease in billable_leases(org, month).select_related("unit__property").order_by("pk"):
        if actor is not None and not can(actor, "invoices.generate", lease.unit.property):
            continue
        try:
            invoice = generate_lease_month(lease, month, actor=actor, today=today, request=request)
        except ValidationError as e:
            result.errors.append((lease, e.messages[0]))
            continue
        if invoice is not None:
            result.invoices.append(invoice)
    return result


def run_scheduled(today=None) -> dict[str, int]:
    """The daily job: bills this month and, within the lead days, next month, for every organization."""
    today = today or timezone.localdate()
    counts = {"organizations": 0, "invoices": 0, "errors": 0}
    for org in Organization.objects.filter(archived_at__isnull=True, status=Organization.Status.ACTIVE):
        counts["organizations"] += 1
        for month in months_to_bill(org, today):
            result = generate_month(org, month, today=today)
            counts["invoices"] += len(result.invoices)
            counts["errors"] += len(result.errors)
    return counts


# ---------------------------------------------------------------------------
# Voiding
# ---------------------------------------------------------------------------


def can_void(membership: Membership, invoice: Invoice) -> bool:
    return (invoice.organization_id == membership.organization_id
            and invoice.status in (Invoice.Status.DRAFT, *Invoice.OPEN) and invoice.amount_paid == 0
            and can(membership, "invoices.void", invoice.lease.unit.property))


@transaction.atomic
def void_invoice(actor: Membership, invoice: Invoice, *, reason: str, request=None) -> Invoice:
    """Cancels an invoice with a reason. Its number is kept and never reused (doc 11 §21).

    The ledger gets a matching credit, and its lines stop counting as billed, so the month
    can be billed again, for example after a rent correction.
    """
    if invoice.organization_id != actor.organization_id:
        raise PermissionDenied(_("That record belongs to another organization."))
    require(actor, "invoices.void", invoice.lease.unit.property)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": _("Say why the invoice is being voided.")})
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status == Invoice.Status.VOID:
        raise ValidationError(_("This invoice is already void."))
    if invoice.amount_paid > 0:
        raise ValidationError(_("Payments are allocated to this invoice. Reverse or move them first."))
    was = invoice.status
    today = timezone.localdate()
    InvoiceLine.objects.filter(invoice=invoice).update(is_void=True)
    invoice.status = Invoice.Status.VOID
    invoice.voided_at = timezone.now()
    invoice.voided_by = actor.user
    invoice.void_reason = reason
    invoice.save(update_fields=["status", "voided_at", "voided_by", "void_reason", "updated_at"])
    if was != Invoice.Status.DRAFT and invoice.total > 0:
        LedgerEntry.objects.create(organization=invoice.organization, lease=invoice.lease, entry_date=today,
                                   kind=LedgerEntry.Kind.INVOICE_VOID, amount=-invoice.total,
                                   currency=invoice.currency, invoice=invoice, reason=reason,
                                   created_by=actor.user)
    audit.record("invoice.void", actor=actor.user, organization=invoice.organization, obj=invoice,
                 request=request, changes={"status": [was, Invoice.Status.VOID], "reason": [None, reason],
                                           "total": [str(invoice.total), None]})
    return invoice


# ---------------------------------------------------------------------------
# Opening balances (doc 14 A2)
# ---------------------------------------------------------------------------


def opening_balance(lease: Lease) -> LedgerEntry | None:
    """The live balance brought forward: the latest OPENING_BALANCE that was not reversed."""
    return (LedgerEntry.objects.filter(lease=lease, kind=LedgerEntry.Kind.OPENING_BALANCE,
                                       reversed_by__isnull=True).order_by("-pk").first())


@transaction.atomic
def set_opening_balance(actor: Membership, lease: Lease, *, amount, as_of: datetime.date, reason="",
                        request=None) -> LedgerEntry | None:
    """What the tenant owed (positive) or had paid ahead (negative) at go-live.

    Changing it reverses the old entry and writes a new one; nothing is edited. Zero clears it.
    """
    if lease.organization_id != actor.organization_id:
        raise PermissionDenied(_("That record belongs to another organization."))
    require(actor, "invoices.adjust", lease.unit.property)
    lease = _lock_lease(lease)
    if lease.status == Lease.Status.DRAFT:
        raise ValidationError(_("Activate the lease before entering its balance."))
    try:
        amount = parse_money(amount, allow_negative=True)
    except ValidationError as e:
        raise ValidationError({"amount": e.messages}) from None
    old = opening_balance(lease)
    if old is not None and old.amount == amount and old.entry_date == as_of:
        return old
    if old is not None:
        LedgerEntry.objects.create(organization=lease.organization, lease=lease, entry_date=as_of,
                                   kind=LedgerEntry.Kind.REVERSAL, amount=-old.amount, reversal_of=old,
                                   reason=_("Balance brought forward changed"), created_by=actor.user)
    new = None
    if amount != 0:
        new = LedgerEntry.objects.create(organization=lease.organization, lease=lease, entry_date=as_of,
                                         kind=LedgerEntry.Kind.OPENING_BALANCE, amount=amount,
                                         currency=lease.currency, reason=reason.strip()[:300],
                                         created_by=actor.user)
    audit.record("lease.opening_balance", actor=actor.user, organization=lease.organization, obj=lease,
                 request=request, changes={"opening_balance": [str(old.amount) if old else None, str(amount)],
                                           "as_of": [str(old.entry_date) if old else None, str(as_of)]})
    return new


# ---------------------------------------------------------------------------
# Balances
# ---------------------------------------------------------------------------


def lease_balance(lease: Lease) -> Decimal:
    """Positive = owed, negative = in credit. Deposits are never part of it (doc 14 A1)."""
    return LedgerEntry.objects.filter(lease=lease).aggregate(s=Sum("amount"))["s"] or ZERO
