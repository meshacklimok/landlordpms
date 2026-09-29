"""Profit and loss, cash flow and aged receivables by property (D-065).

- Cash basis. Money received is split as in the income pack (D-050): each confirmed payment by
  its allocations to rent, other charges and deposits, plus money not yet applied (counted as
  rent). Reversed payments are left out.
- Money out is deposit refunds (by entry date; corrected ones left out) and payments to owners
  (not voided, by date paid). Owner payments belong to an owner, not a property, so they are
  counted only when the whole organization is in view.
- Expenses are not recorded yet (flow C), so net operating income equals income for now.
- Receivables come from the arrears aging (FIFO), everything owed including what is not yet due.
- Only the properties the member can see are counted, optionally one of them.
"""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import DateField, Sum
from django.db.models.functions import Cast, Coalesce, TruncMonth
from django.utils.translation import gettext as _

from accounts.models import Membership, Organization
from accounts.permissions import accessible_property_ids, require, visible_properties
from billing import selectors as billing_selectors
from billing.models import ChargeType, DepositEntry, Invoice, InvoiceLine
from core.money import ZERO
from payments.models import Payment
from properties.models import Property

from .income import _invoice_shares, _split
from .metrics import add_months, month_end, month_start, rate
from .models import OwnerRemittance

MAX_MONTHS = 12
DEFAULT_MONTHS = 6
LIVE_INVOICE = (Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID, Invoice.Status.PAID)


# ---------------------------------------------------------------------------
# The period
# ---------------------------------------------------------------------------


def _month(value: str) -> datetime.date | None:
    try:
        return datetime.datetime.strptime((value or "").strip()[:7], "%Y-%m").date()
    except ValueError:
        return None


def period(start: str, end: str, today: datetime.date) -> tuple[datetime.date, datetime.date]:
    """The first days of the first and last months, from "YYYY-MM" values; at most 12 months.

    Missing or unreadable: the last 6 months including this one. The wrong way round: swapped.
    Longer than 12 months: the 12 months ending with the last one.
    """
    last = _month(end) or month_start(today)
    first = _month(start) or add_months(last, 1 - DEFAULT_MONTHS)
    if first > last:
        first, last = last, first
    if (last.year - first.year) * 12 + last.month - first.month >= MAX_MONTHS:
        first = add_months(last, 1 - MAX_MONTHS)
    return first, last


# ---------------------------------------------------------------------------
# Profit and loss, cash flow
# ---------------------------------------------------------------------------


@dataclass
class Month:
    label: str
    key: datetime.date | None = None
    rent: Decimal = ZERO
    other: Decimal = ZERO
    unapplied: Decimal = ZERO
    deposits: Decimal = ZERO
    rent_billed: Decimal = ZERO
    refunds: Decimal = ZERO
    owner_payments: Decimal = ZERO

    FIELDS = ("rent", "other", "unapplied", "deposits", "rent_billed", "refunds", "owner_payments")

    @property
    def income(self) -> Decimal:
        return self.rent + self.other + self.unapplied

    @property
    def noi(self) -> Decimal:
        return self.income  # less expenses, once they are recorded

    @property
    def collection_rate(self) -> Decimal | None:
        return rate(self.rent + self.unapplied, self.rent_billed)

    @property
    def money_in(self) -> Decimal:
        return self.income + self.deposits

    @property
    def money_out(self) -> Decimal:
        return self.refunds + self.owner_payments

    @property
    def net(self) -> Decimal:
        return self.money_in - self.money_out

    def add(self, other: "Month") -> None:
        for name in self.FIELDS:
            setattr(self, name, getattr(self, name) + getattr(other, name))


@dataclass
class Report:
    organization: Organization
    start: datetime.date
    end: datetime.date
    property: Property | None
    months: list[Month]
    total: Month
    whole_organization: bool
    notes: list[str] = field(default_factory=list)

    @property
    def currency(self) -> str:
        return self.organization.currency

    @property
    def label(self) -> str:
        if self.start == self.end:
            return f"{self.start:%b %Y}"
        return f"{self.start:%b %Y} – {self.end:%b %Y}"


def _properties(membership: Membership, prop: Property | None):
    properties = visible_properties(membership, Property.all_objects.all())
    return properties.filter(pk=prop.pk) if prop is not None else properties


def money_report(membership: Membership, start: datetime.date, end: datetime.date,
                 prop: Property | None = None) -> Report:
    """The figures for both the profit and loss and the cash flow pages."""
    require(membership, "reports.view_financial")
    org = membership.organization
    properties = _properties(membership, prop)
    first, last = start, month_end(end)
    months: dict[datetime.date, Month] = {}
    key = start
    while key <= end:
        months[key] = Month(label=f"{key:%b %Y}", key=key)
        key = add_months(key, 1)

    # Rent billed, by the month each line bills.
    for r in (InvoiceLine.objects.filter(organization=org, is_void=False, invoice__status__in=LIVE_INVOICE,
                                         lease__unit__property__in=properties,
                                         charge_type__category=ChargeType.Category.RENT)
              .annotate(month=Coalesce("billing_month", Cast(TruncMonth("invoice__period_start"), DateField())))
              .filter(month__gte=first, month__lte=last).values("month").annotate(total=Sum("amount"))):
        months[r["month"]].rent_billed += r["total"]

    # Money received, split by what each payment paid.
    payments = list(Payment.objects.filter(organization=org, status=Payment.Status.CONFIRMED, paid_at__gte=first,
                                           paid_at__lte=last, lease__unit__property__in=properties)
                    .prefetch_related("allocations"))
    shares = _invoice_shares(payments)
    for p in payments:
        part = _split(p, shares)
        row = months[month_start(p.paid_at)]
        row.rent += part.rent
        row.other += part.other
        row.deposits += part.deposit
        row.unapplied += part.unapplied

    # Deposit refunds; a corrected refund is left out, whenever corrected, as reversed payments are.
    refunds = (DepositEntry.objects.filter(organization=org, lease__unit__property__in=properties,
                                           kind=DepositEntry.Kind.REFUNDED, reversed_by__isnull=True,
                                           entry_date__gte=first, entry_date__lte=last)
               .annotate(month=Cast(TruncMonth("entry_date"), DateField())).values("month")
               .annotate(total=Sum("amount")))
    for r in refunds:
        months[r["month"]].refunds -= r["total"]

    whole = prop is None and accessible_property_ids(membership) is None
    if whole:
        for r in (OwnerRemittance.objects.live().filter(organization=org, paid_on__gte=first, paid_on__lte=last)
                  .annotate(bucket=Cast(TruncMonth("paid_on"), DateField())).values("bucket")
                  .annotate(total=Sum("amount"))):
            months[r["bucket"]].owner_payments += r["total"]

    total = Month(label=_("Total"))
    for row in months.values():
        total.add(row)
    report = Report(organization=org, start=start, end=end, property=prop, months=list(months.values()),
                    total=total, whole_organization=whole)
    report.notes = _notes(report, membership)
    return report


def _notes(report: Report, membership: Membership) -> list[str]:
    notes = [_("Cash basis: money is counted in the month it was received or paid out. Reversed payments "
               "are left out.")]
    if report.total.unapplied:
        notes.append(_("Money received but not yet applied to an invoice is counted as rent."))
    notes.append(_("Expenses are not recorded yet, so net operating income is the same as income."))
    notes.append(_("Deposits are held for tenants and are not income."))
    if report.whole_organization:
        notes.append(_("Payments to owners are counted by the date they were paid."))
    else:
        notes.append(_("Payments to owners are left out: they belong to an owner, not a property. Choose all "
                       "properties to see them."))
    if report.property is None and accessible_property_ids(membership) is not None:
        notes.append(_("Only the properties you can see are included."))
    return notes


# ---------------------------------------------------------------------------
# Aged receivables
# ---------------------------------------------------------------------------


BUCKETS = [key for key, *_rest in billing_selectors.AGING_BUCKETS]


@dataclass
class PropertyAging:
    property: Property | None
    leases: list = field(default_factory=list)
    buckets: dict = field(default_factory=lambda: dict.fromkeys(BUCKETS, ZERO))

    @property
    def total(self) -> Decimal:
        return sum(self.buckets.values(), ZERO)

    def add(self, buckets: dict) -> None:
        for key in BUCKETS:
            self.buckets[key] += buckets[key]


def receivables(membership: Membership, today: datetime.date,
                prop: Property | None = None) -> tuple[list[PropertyAging], PropertyAging]:
    """Everything owed as of today, by property and age, largest first. Credit balances are left out."""
    require(membership, "reports.view_financial")
    rows: dict[int, PropertyAging] = {}
    total = PropertyAging(property=None)
    for lease_row in billing_selectors.arrears(membership, today, overdue_only=False):
        place = lease_row.lease.unit.property
        if prop is not None and place.pk != prop.pk:
            continue
        row = rows.setdefault(place.pk, PropertyAging(property=place))
        row.leases.append(lease_row)
        row.add(lease_row.buckets)
        total.add(lease_row.buckets)
    return sorted(rows.values(), key=lambda r: (-r.total, r.property.name.lower())), total
