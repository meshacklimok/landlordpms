"""The facts behind the lease agreement PDF and the move-out statement (D-073).

Both are drawn fresh from the records each time; nothing here changes money or the lease.
"""

import datetime
from decimal import Decimal

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.models import Membership
from accounts.permissions import can
from billing import deposits
from billing.models import LedgerEntry
from billing.selectors import unpaid_debts

from .models import Lease

ZERO = Decimal("0.00")
Kind = LedgerEntry.Kind

# Ledger kinds grouped the way the statement shows them. Together they are the whole balance.
ACCOUNT_GROUPS = (
    ("charges", _("Rent and charges billed"), (Kind.OPENING_BALANCE, Kind.INVOICE, Kind.INVOICE_VOID)),
    ("adjustments", _("Adjustments and corrections"), (Kind.ADJUSTMENT, Kind.REVERSAL)),
    ("payments", _("Payments received"), (Kind.PAYMENT, Kind.PAYMENT_REVERSAL)),
    ("deposit_applied", _("Deposit applied to the balance"), (Kind.DEPOSIT_APPLIED,)),
)


def _tenants(lease: Lease) -> list:
    links = sorted(lease.lease_tenants.all(), key=lambda lt: (not lt.is_primary, lt.pk))
    return [lt.tenant for lt in links]


# ---------------------------------------------------------------------------
# Agreement
# ---------------------------------------------------------------------------


def agreement(lease: Lease, membership: Membership) -> dict:
    """What the agreement PDF prints. ID numbers and KRA PINs only for tenants.view_sensitive."""
    prop, unit = lease.unit.property, lease.unit
    sensitive = can(membership, "tenants.view_sensitive")
    ref = max(timezone.localdate(), lease.start_date)
    tenants = []
    for t in _tenants(lease):
        id_text = f"{t.get_id_type_display()} {t.id_number}".strip() if t.id_number else ""
        tenants.append({"name": t.name, "phone": t.phone, "email": t.email,
                        "id": id_text if sensitive else None, "kra_pin": t.kra_pin if sensitive else None})
    rent = lease.rent_on(ref)
    return {
        "lease": lease, "draft": lease.is_draft,
        "organization": lease.organization.name, "organization_pin": lease.organization.kra_pin,
        "property": prop.name, "address": prop.address_line,
        "unit": unit.code, "unit_label": unit.type_label or unit.get_unit_type_display(),
        "tenants": tenants,
        "start": lease.start_date, "end": lease.end_date,
        "rent": rent, "rent_per_period": rent * lease.months_per_period if rent is not None else None,
        "frequency": lease.get_billing_frequency_display(), "months": lease.months_per_period,
        "due_day": lease.due_day, "grace_days": lease.grace_days,
        "later_rents": list(lease.rent_changes.filter(effective_from__gt=ref).order_by("effective_from")),
        "charges": [c for c in lease.charges.select_related("charge_type").order_by("active_from", "pk")
                    if c.active_to is None or c.active_to >= ref],
        "deposit": lease.deposit_amount, "notice_days": lease.notice_days,
        "payers": list(lease.payers.all()),
        "terms": lease.terms.strip(),
        "currency": lease.currency,
    }


# ---------------------------------------------------------------------------
# Move-out statement
# ---------------------------------------------------------------------------


def move_out_applies(lease: Lease) -> bool:
    """Notice given, or ended or terminated. A renewal is not a move-out. Not for estates, water vendors or
    garbage collectors (D-130): they have no notice to vacate, deposit clearance or condition report to settle."""
    if not lease.organization.bills_rent:
        return False
    if lease.status in (Lease.Status.ENDED, Lease.Status.TERMINATED):
        return True
    return lease.status == Lease.Status.ACTIVE and lease.notice_given_on is not None


def _condition(lease: Lease) -> dict | None:
    from inspections import services as inspections
    from inspections.models import ConditionReport

    report = (ConditionReport.objects.filter(lease=lease, kind=ConditionReport.Kind.MOVE_OUT,
                                             status=ConditionReport.Status.COMPLETED)
              .order_by("-inspected_on", "-pk").first())
    if report is None:
        return None
    before = inspections.baseline(report)
    table = inspections.rows(report, before)
    return {"report": report, "compared": before is not None, "items": len(table),
            "worse": sum(1 for r in table if r["worse"])}


def move_out(lease: Lease, today: datetime.date | None = None) -> dict:
    """The move-out statement as at today: rent account, deposits, condition and what is due."""
    today = today or timezone.localdate()
    entries = list(LedgerEntry.objects.filter(lease=lease).select_related("invoice").order_by("entry_date", "pk"))
    account = [{"key": key, "label": label, "amount": sum((e.amount for e in entries if e.kind in kinds), ZERO)}
               for key, label, kinds in ACCOUNT_GROUPS]
    balance = sum((e.amount for e in entries), ZERO)
    clearance = deposits.clearance_statement(lease)
    held = clearance["held"]
    due = held - balance  # > 0 back to the tenant, < 0 still owed by them
    if lease.ended_on:
        moved_out, expected = lease.ended_on, False
    else:
        moved_out, expected = lease.move_out_by, True
    return {
        "lease": lease, "today": today, "currency": lease.currency,
        "organization": lease.organization.name,
        "tenants": _tenants(lease), "property": lease.unit.property.name, "unit": lease.unit.code,
        "moved_out": moved_out, "expected": expected,
        "account": [row for row in account if row["amount"] or row["key"] in ("charges", "payments")],
        "balance": balance,
        "unpaid": unpaid_debts(entries),
        "deposits": clearance["types"], "held": held,
        "condition": _condition(lease),
        "due": due,
    }
