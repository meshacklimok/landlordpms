"""Profit and loss, cash flow and aged receivables by property (D-065).

- Cash basis. Money received is split as in the income pack (D-050): each confirmed payment by
  its allocations to rent, other charges and deposits, plus money not yet applied (counted as
  rent). Reversed payments are left out.
- Money out is deposit refunds (by entry date; corrected ones left out) and payments to owners
  (not voided, by date paid). Owner payments belong to an owner, not a property, so they are
  counted only when the whole organization is in view.
- Expenses (D-067) are approved expenses by the date paid. Net operating income is income less
  expenses; in the cash flow they are money out. Waiting expenses are only mentioned in a note.
- Receivables come from the arrears aging (FIFO), everything owed including what is not yet due.
- Only the properties the member can see are counted, optionally one of them.
"""

import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import DateField, Sum
from django.db.models.functions import Cast, Coalesce, TruncMonth
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from accounts.models import Membership, Organization
from accounts.permissions import accessible_property_ids, require, visible_properties
from billing import selectors as billing_selectors
from billing.models import ChargeType, DepositEntry, Invoice, InvoiceLine
from core import reportcache
from core.money import ZERO, format_money, round_money
from expenses import services as expense_services
from payments.models import Payment, PaymentAllocation
from properties.models import Property

from .income import Split, divide, invoice_shares
from .metrics import Change, _billed_since, add_months, month_end, month_start, rate
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
    utilities: Decimal = ZERO
    other: Decimal = ZERO
    unapplied: Decimal = ZERO
    deposits: Decimal = ZERO
    rent_billed: Decimal = ZERO
    refunds: Decimal = ZERO
    owner_payments: Decimal = ZERO
    expenses: Decimal = ZERO

    FIELDS = ("rent", "utilities", "other", "unapplied", "deposits", "rent_billed", "refunds", "owner_payments",
              "expenses")

    @property
    def income(self) -> Decimal:
        return self.rent + self.utilities + self.other + self.unapplied

    @property
    def noi(self) -> Decimal:
        return self.income - self.expenses

    @property
    def collection_rate(self) -> Decimal | None:
        return rate(self.rent + self.unapplied, self.rent_billed)

    @property
    def money_in(self) -> Decimal:
        return self.income + self.deposits

    @property
    def money_out(self) -> Decimal:
        return self.expenses + self.refunds + self.owner_payments

    @property
    def net(self) -> Decimal:
        return self.money_in - self.money_out

    @property
    def expense_ratio(self) -> Decimal | None:
        """Expenses ÷ income (D-110): how much of what came in went on running the property."""
        return rate(self.expenses, self.income)

    @property
    def margin(self) -> Decimal | None:
        """Net operating income ÷ income (D-110)."""
        return rate(self.noi, self.income)

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
    # (category name, total) for the whole period, largest first.
    expense_categories: list = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # D-110: each property's totals when more than one is in view. Owner payments are left out: they
    # belong to an owner, not a property.
    by_property: list["PropertyMoney"] = field(default_factory=list)
    # D-110: the same report for the same number of months just before, for comparison.
    previous: "Report | None" = None

    @property
    def average(self) -> Month:
        """Each figure's monthly average over the period."""
        avg = Month(label=_("Monthly average"))
        for name in Month.FIELDS:
            setattr(avg, name, round_money(getattr(self.total, name) / len(self.months)))
        return avg

    def change(self, name: str) -> Change:
        """This period's total of one figure against the previous period's."""
        if self.previous is None:
            return Change(None, None)
        return Change(getattr(self.total, name), getattr(self.previous.total, name), self.previous.label)

    @property
    def currency(self) -> str:
        return self.organization.currency

    @property
    def label(self) -> str:
        if self.start == self.end:
            return f"{self.start:%b %Y}"
        return f"{self.start:%b %Y} – {self.end:%b %Y}"


@dataclass
class PropertyMoney:
    property: Property
    figures: Month


def previous_period(start: datetime.date, end: datetime.date) -> tuple[datetime.date, datetime.date]:
    """The same number of months, ending the month before `start`."""
    count = (end.year - start.year) * 12 + end.month - start.month + 1
    return add_months(start, -count), add_months(start, -1)


def _properties(membership: Membership, prop: Property | None):
    properties = visible_properties(membership, Property.all_objects.all())
    return properties.filter(pk=prop.pk) if prop is not None else properties


def money_report(membership: Membership, start: datetime.date, end: datetime.date,
                 prop: Property | None = None, *, compare: bool = False) -> Report:
    """The figures for both the profit and loss and the cash flow pages.

    With `compare`, `previous` holds the same report for the months just before (D-110). Kept for a few minutes
    (core/reportcache.py): the dashboard, performance, scorecard and profit pages all ask for the same months.
    """
    require(membership, "reports.view_financial")
    return reportcache.cached(membership, "money", (start, end, prop and prop.pk, compare),
                              lambda: _money_report(membership, start, end, prop, compare=compare))


def _money_report(membership: Membership, start: datetime.date, end: datetime.date,
                  prop: Property | None, *, compare: bool) -> Report:
    org = membership.organization
    properties = _properties(membership, prop)
    first, last = start, month_end(end)
    months: dict[datetime.date, Month] = {}
    key = start
    while key <= end:
        months[key] = Month(label=f"{key:%b %Y}", key=key)
        key = add_months(key, 1)
    places: dict[int, Month] = defaultdict(lambda: Month(label=""))

    # Rent billed, by the month each line bills.
    for r in (InvoiceLine.objects.filter(organization=org, is_void=False, invoice__status__in=LIVE_INVOICE,
                                         lease__unit__property__in=properties,
                                         charge_type__category=ChargeType.Category.RENT)
              .filter(_billed_since(first))
              .annotate(month=Coalesce("billing_month", Cast(TruncMonth("invoice__period_start"), DateField())))
              .filter(month__gte=first, month__lte=last).values("month", "lease__unit__property_id")
              .annotate(total=Sum("amount"))):
        months[r["month"]].rent_billed += r["total"]
        places[r["lease__unit__property_id"]].rent_billed += r["total"]

    # Money received, split by what each payment paid. Plain rows, not models: a year is hundreds of payments.
    paid = Payment.objects.filter(organization=org, status=Payment.Status.CONFIRMED, paid_at__gte=first,
                                  paid_at__lte=last, lease__unit__property__in=properties)
    allocations = defaultdict(list)
    for payment_id, invoice_id, amount in (PaymentAllocation.objects.filter(payment__in=paid.values("pk"))
                                           .values_list("payment_id", "invoice_id", "amount")):
        allocations[payment_id].append((invoice_id, amount))
    shares = invoice_shares({invoice_id for rows in allocations.values() for invoice_id, _amount in rows})
    for pk, paid_at, amount, property_id in paid.values_list("pk", "paid_at", "amount", "lease__unit__property_id"):
        part = divide(Split(payment=None), amount, allocations.get(pk, ()), shares)
        for row in (months[month_start(paid_at)], places[property_id]):
            row.rent += part.rent
            row.utilities += part.utilities
            row.other += part.other
            row.deposits += part.deposit
            row.unapplied += part.unapplied

    # Deposit refunds; a corrected refund is left out, whenever corrected, as reversed payments are.
    refunds = (DepositEntry.objects.filter(organization=org, lease__unit__property__in=properties,
                                           kind=DepositEntry.Kind.REFUNDED, reversed_by__isnull=True,
                                           entry_date__gte=first, entry_date__lte=last)
               .annotate(month=Cast(TruncMonth("entry_date"), DateField()))
               .values("month", "lease__unit__property_id").annotate(total=Sum("amount")))
    for r in refunds:
        months[r["month"]].refunds -= r["total"]
        places[r["lease__unit__property_id"]].refunds -= r["total"]

    spent = expense_services.approved(org, properties, first, last)
    for r in (spent.annotate(bucket=Cast(TruncMonth("paid_on"), DateField())).values("bucket", "property_id")
              .annotate(total=Sum("amount"))):
        months[r["bucket"]].expenses += r["total"]
        places[r["property_id"]].expenses += r["total"]
    categories = [(r["category__name"], r["total"]) for r in
                  spent.values("category__name").annotate(total=Sum("amount")).order_by("-total", "category__name")]

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
                    total=total, whole_organization=whole, expense_categories=categories)
    if prop is None and len(places) > 1:
        named = {p.pk: p for p in properties.filter(pk__in=places)}
        for pk, figures in places.items():
            figures.label = named[pk].name
        report.by_property = sorted((PropertyMoney(named[pk], figures) for pk, figures in places.items()),
                                    key=lambda r: (-r.figures.noi, r.property.name.lower()))
    report.notes = _notes(report, membership, expense_services.waiting_summary(org, properties, first, last))
    if compare:
        report.previous = money_report(membership, *previous_period(start, end), prop)
    return report


def _notes(report: Report, membership: Membership, waiting) -> list[str]:
    notes = [_("Cash basis: money is counted in the month it was received or paid out. Reversed payments "
               "are left out.")]
    if report.total.unapplied:
        notes.append(_("Money received but not yet applied to an invoice is counted as rent."))
    notes.append(_("Expenses are counted once approved, in the month they were paid."))
    if waiting.waiting_count:
        notes.append(ngettext(
            "%(n)s expense of %(total)s is waiting for approval and is not counted.",
            "%(n)s expenses totalling %(total)s are waiting for approval and are not counted.",
            waiting.waiting_count) % {"n": waiting.waiting_count, "total": format_money(waiting.waiting,
                                                                                      report.currency)})
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
# Chart colours (D-114): not yet due in the first series colour, then later and later down the late scale.
AGING_CHART_COLORS = {"current": "1", "d1_30": "late-1", "d31_60": "late-2", "d61_90": "late-3", "d90": "late-4"}


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


@dataclass
class ReceivablesSummary:
    """What the aged receivables add up to (D-110): each bucket's share, what is overdue and who owes most."""

    total: PropertyAging
    leases: int
    largest: list  # LeaseArrears rows with the largest balances owed, largest first

    @property
    def overdue(self) -> Decimal:
        return self.total.total - self.total.buckets["current"]

    @property
    def shares(self) -> list[tuple[str, str, Decimal, Decimal | None]]:
        """(key, label, amount, share of everything owed) for each bucket."""
        return [(key, label, self.total.buckets[key], rate(self.total.buckets[key], self.total.total))
                for key, label, *_rest in billing_selectors.AGING_BUCKETS]

    @property
    def over_60_share(self) -> Decimal | None:
        """The share owed more than 60 days late: the money least likely to come in."""
        return rate(self.total.buckets["d61_90"] + self.total.buckets["d90"], self.total.total)


def receivables_summary(rows: list[PropertyAging], total: PropertyAging, count: int = 10) -> ReceivablesSummary:
    lease_rows = [lease_row for row in rows for lease_row in row.leases]
    largest = sorted(lease_rows, key=lambda r: (-sum(r.buckets.values(), ZERO), r.lease.number))[:count]
    return ReceivablesSummary(total=total, leases=len(lease_rows), largest=largest)
