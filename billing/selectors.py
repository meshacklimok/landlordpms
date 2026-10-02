"""Billing read queries: invoice lists, the lease statement and arrears aging (doc 11 §8)."""

import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Prefetch, Q, Sum
from django.utils.translation import gettext_lazy as _

from accounts.permissions import accessible_property_ids, can
from core.money import ZERO
from leases.models import Lease, LeaseTenant

from .models import Invoice, LedgerEntry

Kind = LedgerEntry.Kind

STATUS_FILTERS = {
    "open": (_("Open"), Q(status__in=Invoice.OPEN)),
    "paid": (_("Paid"), Q(status=Invoice.Status.PAID)),
    "void": (_("Void"), Q(status=Invoice.Status.VOID)),
}

# (key, label, lowest days past due, highest or None). Days count from the due date plus grace
# (`Invoice.overdue_after`), as doc 11 §26 defines arrears, so "current" includes the grace days.
AGING_BUCKETS = [
    ("current", _("Not yet due"), None, 0),
    ("d1_30", _("1–30 days"), 1, 30),
    ("d31_60", _("31–60 days"), 31, 60),
    ("d61_90", _("61–90 days"), 61, 90),
    ("d90", _("Over 90 days"), 91, None),
]


def _in_scope(membership, qs, path: str):
    ids = accessible_property_ids(membership)
    return qs if ids is None else qs.filter(**{f"{path}__in": ids})


def visible_invoices(membership):
    """Invoices on the properties the member may see, archived ones included: money history stays."""
    if not can(membership, "invoices.view"):
        return Invoice.objects.none()
    qs = Invoice.objects.for_org(membership.organization).select_related("lease__unit__property")
    return _in_scope(membership, qs, "lease__unit__property_id")


def filter_invoices(invoices, *, status="", q="", overdue=False, today=None):
    if status in STATUS_FILTERS:
        invoices = invoices.filter(STATUS_FILTERS[status][1])
    if overdue:
        invoices = invoices.filter(status__in=Invoice.OPEN, overdue_after__lt=today)
    if q:
        invoices = invoices.filter(
            Q(number__icontains=q) | Q(lease__number__icontains=q) | Q(lease__unit__code__icontains=q)
            | Q(lease_id__in=LeaseTenant.objects.filter(tenant__name__icontains=q).values("lease_id")))
    return invoices


# ---------------------------------------------------------------------------
# Statement
# ---------------------------------------------------------------------------


@dataclass
class StatementRow:
    entry: LedgerEntry
    balance: Decimal

    @property
    def debit(self):
        return self.entry.amount if self.entry.amount > 0 else None

    @property
    def credit(self):
        return -self.entry.amount if self.entry.amount < 0 else None


def statement(lease: Lease, *, start: datetime.date | None = None, end: datetime.date | None = None) -> dict:
    """Every ledger row in the period with a running balance, starting from what was owed before it."""
    entries = LedgerEntry.objects.filter(lease=lease)
    brought = ZERO
    if start:
        brought = entries.filter(entry_date__lt=start).aggregate(s=Sum("amount"))["s"] or ZERO
        entries = entries.filter(entry_date__gte=start)
    if end:
        entries = entries.filter(entry_date__lte=end)
    rows, balance = [], brought
    for entry in entries.select_related("invoice", "payment").order_by("entry_date", "pk"):
        balance += entry.amount
        rows.append(StatementRow(entry, balance))
    return {"lease": lease, "start": start, "end": end, "brought_forward": brought, "rows": rows,
            "closing": balance}


# ---------------------------------------------------------------------------
# Arrears and aging
# ---------------------------------------------------------------------------


@dataclass
class Debt:
    """Part of the balance still unpaid, from one debit, the day it fell due and the day it became late."""
    due: datetime.date
    amount: Decimal
    invoice: Invoice | None = None
    late_from: datetime.date | None = None

    def __post_init__(self):
        if self.late_from is None:
            self.late_from = self.due


@dataclass
class LeaseArrears:
    lease: Lease
    balance: Decimal
    debts: list[Debt]
    buckets: dict[str, Decimal] = field(default_factory=dict)
    oldest_due: datetime.date | None = None
    # The oldest unpaid amount's due date plus grace: days overdue count from here.
    late_from: datetime.date | None = None

    def days_overdue(self, today: datetime.date) -> int:
        return max((today - self.late_from).days, 0) if self.late_from else 0


def _bucket(days: int) -> str:
    for key, _label, low, high in AGING_BUCKETS:
        if (low is None or days >= low) and (high is None or days <= high):
            return key
    raise AssertionError(days)


def unpaid_debts(entries: list[LedgerEntry]) -> list[Debt]:
    """What is still owed, oldest first (FIFO): credits pay the debits that fell due earliest.

    A void cancels its own invoice and a correction the entry it reverses, so they are paired
    off first; every other credit pays in due-date order.
    """
    cancelled = set()
    for e in entries:
        if e.kind == Kind.INVOICE_VOID:
            cancelled |= {("inv", e.invoice_id)}
        if e.reversal_of_id:
            cancelled |= {e.pk, e.reversal_of_id}
    debits, credit = [], ZERO
    for e in entries:
        if e.pk in cancelled or (e.invoice_id and ("inv", e.invoice_id) in cancelled):
            continue
        if e.amount > 0:
            invoice = e.invoice if e.kind == Kind.INVOICE else None
            due = invoice.due_date if invoice else e.entry_date
            late_from = (invoice.overdue_after or due) if invoice else due
            debits.append(Debt(due, e.amount, invoice, late_from))
        else:
            credit -= e.amount
    debits.sort(key=lambda d: (d.due, d.invoice.pk if d.invoice else 0))
    unpaid = []
    for debt in debits:
        paid = min(credit, debt.amount)
        credit -= paid
        if debt.amount > paid:
            unpaid.append(Debt(debt.due, debt.amount - paid, debt.invoice, debt.late_from))
    return unpaid


def lease_arrears(lease: Lease, entries: list[LedgerEntry], today: datetime.date) -> LeaseArrears:
    debts = unpaid_debts(entries)
    buckets = {key: ZERO for key, *_rest in AGING_BUCKETS}
    for debt in debts:
        buckets[_bucket((today - debt.late_from).days)] += debt.amount
    return LeaseArrears(lease=lease, balance=sum((e.amount for e in entries), ZERO), debts=debts, buckets=buckets,
                        oldest_due=debts[0].due if debts else None,
                        late_from=min((d.late_from for d in debts), default=None))


def arrears(membership, today: datetime.date, *, overdue_only=True) -> list[LeaseArrears]:
    """Leases that owe money, most overdue first, from the ledger in one query per table."""
    if not can(membership, "invoices.view"):
        return []
    return _arrears(membership, today, overdue_only=overdue_only)


def overdue_lease_count(membership, today: datetime.date) -> int:
    """How many leases have money overdue, with no amounts or names: the summary figure (D-056).

    The caller checks `dashboard.view_summary`; this needs no `invoices.view` (doc 10: a caretaker
    sees the arrears count only). It counts exactly the leases `arrears` lists.
    """
    return len(_arrears(membership, today, overdue_only=True))


def _arrears(membership, today: datetime.date, *, overdue_only: bool) -> list[LeaseArrears]:
    owing = (_in_scope(membership, LedgerEntry.objects.for_org(membership.organization), "lease__unit__property_id")
             .values("lease_id").annotate(s=Sum("amount")).filter(s__gt=0).values("lease_id"))
    leases = {lease.pk: lease for lease in Lease.all_objects.filter(pk__in=owing).select_related(
        "unit__property").prefetch_related(Prefetch("lease_tenants", LeaseTenant.objects.select_related("tenant")))}
    grouped = defaultdict(list)
    for e in (LedgerEntry.objects.filter(lease_id__in=leases).select_related("invoice").order_by("entry_date", "pk")):
        grouped[e.lease_id].append(e)
    rows = [lease_arrears(leases[pk], grouped[pk], today) for pk in leases]
    if overdue_only:
        rows = [r for r in rows if r.buckets["current"] < r.balance]
    rows.sort(key=lambda r: (r.late_from or today, -r.balance))
    return rows


def aging_totals(rows: list[LeaseArrears]) -> dict[str, Decimal]:
    totals = {key: ZERO for key, *_rest in AGING_BUCKETS}
    for row in rows:
        for key, amount in row.buckets.items():
            totals[key] += amount
    totals["total"] = sum((r.balance for r in rows), ZERO)
    return totals
