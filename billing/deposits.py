"""The deposit sub-ledger (doc 14 A1).

A deposit is money held for the tenant, not income, and it never offsets rent on its own.
Putting part of it towards what the tenant owes is an explicit deduction with a reason, which
also writes a DEPOSIT_APPLIED credit to the rent ledger. When a lease is renewed or the tenant
moves unit, what is held moves to the new lease as a linked pair of DEPOSIT_TRANSFERRED entries.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import require
from audit import services as audit
from core.money import ZERO, format_money, parse_money
from leases.models import Lease

from .models import DepositEntry, LedgerEntry

Kind = DepositEntry.Kind
DepositType = DepositEntry.DepositType


def held(lease: Lease, deposit_type: str | None = None) -> Decimal:
    qs = DepositEntry.objects.filter(lease=lease)
    if deposit_type:
        qs = qs.filter(deposit_type=deposit_type)
    return qs.aggregate(s=Sum("amount"))["s"] or ZERO


def held_by_type(lease: Lease) -> dict[str, Decimal]:
    """{"RENT": 30000.00, ...}, only types with money held or entries."""
    rows = (DepositEntry.objects.filter(lease=lease).values("deposit_type").annotate(s=Sum("amount"))
            .order_by("deposit_type"))
    return {r["deposit_type"]: r["s"] for r in rows}


def clearance_statement(lease: Lease) -> dict:
    """The Deposit Clearance Statement (doc 14 A1): every entry per type, and what is left."""
    entries = list(DepositEntry.objects.filter(lease=lease).select_related("created_by")
                   .order_by("deposit_type", "entry_date", "pk"))
    types = []
    for code, label in DepositType.choices:
        rows = [e for e in entries if e.deposit_type == code]
        if not rows:
            continue

        def total(*kinds, rows=rows):
            return sum((e.amount for e in rows if e.kind in kinds), ZERO)

        types.append({
            "code": code, "label": label, "entries": rows,
            "received": total(Kind.RECEIVED),
            "deductions": -total(Kind.DEDUCTION),
            "refunded": -total(Kind.REFUNDED),
            "transferred": total(Kind.TRANSFERRED),
            "held": sum((e.amount for e in rows), ZERO),
        })
    return {"lease": lease, "types": types, "held": sum((t["held"] for t in types), ZERO)}


def _check(actor: Membership, lease: Lease, capability: str) -> Lease:
    if lease.organization_id != actor.organization_id:
        raise PermissionDenied(_("That record belongs to another organization."))
    require(actor, capability, lease.unit.property)
    lease = Lease.all_objects.select_for_update().get(pk=lease.pk)
    if lease.status == Lease.Status.DRAFT:
        raise ValidationError(_("Activate the lease before recording its deposit."))
    return lease


def _amount(value) -> Decimal:
    try:
        return parse_money(value, allow_zero=False)
    except ValidationError as e:
        raise ValidationError({"amount": e.messages}) from None


def _type(value: str) -> str:
    if value not in DepositType.values:
        raise ValidationError({"deposit_type": _("Choose a kind of deposit.")})
    return value


def _date(value) -> datetime.date:
    day = value or timezone.localdate()
    if day > timezone.localdate():
        raise ValidationError({"entry_date": _("The date cannot be in the future.")})
    return day


def _audit(action, actor, lease, entry, request, extra=None):
    audit.record(action, actor=actor.user, organization=lease.organization, obj=lease, request=request, changes={
        "deposit_type": [None, entry.deposit_type], "amount": [None, str(entry.amount)], **(extra or {})})


def _require_held(lease: Lease, deposit_type: str, amount: Decimal) -> None:
    available = held(lease, deposit_type)
    if amount > available:
        raise ValidationError({"amount": _("Only %(held)s of this deposit is held.")
                               % {"held": format_money(available, lease.currency)}})


@transaction.atomic
def record_received(actor: Membership, lease: Lease, *, amount, deposit_type=DepositType.RENT, entry_date=None,
                    reference="", reason="", request=None) -> DepositEntry:
    """Money received as a deposit, or held already at go-live (doc 14 A2)."""
    lease = _check(actor, lease, "deposits.record")
    entry = DepositEntry.objects.create(
        organization=lease.organization, lease=lease, deposit_type=_type(deposit_type), entry_date=_date(entry_date),
        kind=Kind.RECEIVED, amount=_amount(amount), currency=lease.currency, reference=reference.strip()[:60],
        reason=reason.strip()[:300], created_by=actor.user)
    _audit("deposit.received", actor, lease, entry, request)
    return entry


@transaction.atomic
def deduct(actor: Membership, lease: Lease, *, amount, reason: str, deposit_type=DepositType.RENT,
           entry_date=None, apply_to_balance=False, request=None) -> DepositEntry:
    """Keeps part of a deposit, e.g. for damage. Needs deposits.deduct and a reason.

    With apply_to_balance the deduction pays down what the tenant owes on this lease.
    """
    lease = _check(actor, lease, "deposits.deduct")
    deposit_type, amount, day = _type(deposit_type), _amount(amount), _date(entry_date)
    reason = (reason or "").strip()[:300]
    if not reason:
        raise ValidationError({"reason": _("Say why the deposit is being deducted.")})
    _require_held(lease, deposit_type, amount)
    credit = None
    if apply_to_balance:
        credit = LedgerEntry.objects.create(
            organization=lease.organization, lease=lease, entry_date=day, kind=LedgerEntry.Kind.DEPOSIT_APPLIED,
            amount=-amount, currency=lease.currency, reason=reason, created_by=actor.user)
    entry = DepositEntry.objects.create(
        organization=lease.organization, lease=lease, deposit_type=deposit_type, entry_date=day,
        kind=Kind.DEDUCTION, amount=-amount, currency=lease.currency, reason=reason, ledger_entry=credit,
        created_by=actor.user)
    _audit("deposit.deduct", actor, lease, entry, request,
           {"reason": [None, reason], **({"applied_to_balance": [None, True]} if credit else {})})
    return entry


@transaction.atomic
def refund(actor: Membership, lease: Lease, *, amount, deposit_type=DepositType.RENT, entry_date=None,
           reference="", reason="", request=None) -> DepositEntry:
    """Pays deposit money back to the tenant."""
    lease = _check(actor, lease, "deposits.record")
    deposit_type, amount = _type(deposit_type), _amount(amount)
    _require_held(lease, deposit_type, amount)
    entry = DepositEntry.objects.create(
        organization=lease.organization, lease=lease, deposit_type=deposit_type, entry_date=_date(entry_date),
        kind=Kind.REFUNDED, amount=-amount, currency=lease.currency, reference=reference.strip()[:60],
        reason=reason.strip()[:300], created_by=actor.user)
    _audit("deposit.refund", actor, lease, entry, request)
    return entry


@transaction.atomic
def reverse(actor: Membership, entry: DepositEntry, *, reason: str, request=None) -> DepositEntry:
    """Cancels a mistaken entry with an equal and opposite one. Transfers cannot be reversed."""
    capability = "deposits.deduct" if entry.kind == Kind.DEDUCTION else "deposits.record"
    lease = _check(actor, entry.lease, capability)
    entry = DepositEntry.objects.select_for_update().get(pk=entry.pk)
    reason = (reason or "").strip()[:300]
    if not reason:
        raise ValidationError({"reason": _("Say why this entry is being corrected.")})
    if entry.kind in (Kind.TRANSFERRED, Kind.REVERSAL):
        raise ValidationError(_("This entry cannot be corrected."))
    if DepositEntry.objects.filter(reversal_of=entry).exists():
        raise ValidationError(_("This entry was already corrected."))
    if entry.amount > 0:
        _require_held(lease, entry.deposit_type, entry.amount)
    today = timezone.localdate()
    credit = entry.ledger_entry
    if credit is not None:
        LedgerEntry.objects.create(organization=lease.organization, lease=lease, entry_date=today,
                                   kind=LedgerEntry.Kind.REVERSAL, amount=-credit.amount, reversal_of=credit,
                                   reason=reason, created_by=actor.user)
    rev = DepositEntry.objects.create(
        organization=lease.organization, lease=lease, deposit_type=entry.deposit_type, entry_date=today,
        kind=Kind.REVERSAL, amount=-entry.amount, currency=entry.currency, reason=reason, reversal_of=entry,
        created_by=actor.user)
    _audit("deposit.reverse", actor, lease, rev, request, {"reason": [None, reason],
                                                           "reversed": [entry.get_kind_display(), None]})
    return rev


def transfer_held(actor: Membership, old: Lease, new: Lease, *, entry_date: datetime.date, request=None) -> list:
    """Moves everything held on `old` to `new` (D-016). Called when a renewal or transfer is activated.

    Runs inside the activation transaction, which already checked the actor may do it.
    """
    moved = []
    for deposit_type, amount in held_by_type(old).items():
        if amount <= 0:
            continue
        out = DepositEntry.objects.create(
            organization=old.organization, lease=old, deposit_type=deposit_type, entry_date=entry_date,
            kind=Kind.TRANSFERRED, amount=-amount, currency=old.currency,
            reason=_("Moved to lease %(lease)s") % {"lease": new.number or new.pk}, created_by=actor.user)
        into = DepositEntry.objects.create(
            organization=new.organization, lease=new, deposit_type=deposit_type, entry_date=entry_date,
            kind=Kind.TRANSFERRED, amount=amount, currency=new.currency, counterpart=out,
            reason=_("Moved from lease %(lease)s") % {"lease": old.number}, created_by=actor.user)
        out.counterpart = into
        out.save(update_fields=["counterpart"])
        moved.append((deposit_type, amount))
    if moved:
        audit.record("deposit.transfer", actor=actor.user, organization=old.organization, obj=new, request=request,
                     changes={"from_lease": [None, old.number],
                              "deposits": [None, {t: str(a) for t, a in moved}]})
    return moved
