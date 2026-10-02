"""Metric definitions (doc 11 §26, D-051): one metric, one definition, one place.

Dashboards, PDFs and exports read from here so their numbers agree. Views stay thin.

- Figures are live, "as recorded now": a late payment or a reversal changes past months.
- Only the properties the member can see are counted; a scoped member never sees org totals.
- Expected and collected rent are by the month each invoice line bills (D-049), so a quarterly
  invoice counts in its three months. Collected splits each confirmed allocation across its
  invoice's live lines by their share, as the income pack does (D-050).
"""

import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Count, DateField, Q, Sum
from django.db.models.functions import Cast, Coalesce, TruncMonth
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import accessible_property_ids, can, require, visible_properties
from billing import selectors as billing_selectors
from billing.models import ChargeType, Invoice, InvoiceLine
from core.money import ZERO, round_money
from leases.models import Lease
from leases.services import occupying_leases
from mpesa.inbox import visible_transactions
from mpesa.models import MpesaTransaction
from payments.models import Payment, PaymentAllocation
from properties.models import Property, Unit

D = datetime.date
LIVE_INVOICE = (Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID, Invoice.Status.PAID)
TREND_MONTHS = 12


def month_start(day: datetime.date) -> datetime.date:
    return day.replace(day=1)


def add_months(month: datetime.date, n: int) -> datetime.date:
    index = month.year * 12 + month.month - 1 + n
    return D(index // 12, index % 12 + 1, 1)


def month_end(month: datetime.date) -> datetime.date:
    return add_months(month, 1) - datetime.timedelta(days=1)


def rate(part, whole) -> Decimal | None:
    """part ÷ whole as a fraction, or None when there is nothing to divide by (not 0%)."""
    if not whole:
        return None
    return Decimal(part) / Decimal(whole)


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


@dataclass
class Scope:
    """Which properties a figure covers: the member's, optionally narrowed to one."""

    membership: Membership
    selected_property: Property | None = None

    @property
    def organization(self):
        return self.membership.organization

    @property
    def is_partial(self) -> bool:
        """True when the figures leave some of the organization out."""
        return self.selected_property is not None or accessible_property_ids(self.membership) is not None

    def properties(self, *, include_archived=True):
        """Money history keeps archived properties; occupancy counts live ones only."""
        base = Property.all_objects.all() if include_archived else Property.objects.all()
        qs = visible_properties(self.membership, base)
        return qs.filter(pk=self.selected_property.pk) if self.selected_property is not None else qs


def scope(membership: Membership, property: Property | None = None, *,
          capability: str = "dashboard.view_financial") -> Scope:
    require(membership, capability)
    if property is not None and not visible_properties(membership, Property.all_objects.all()).filter(
            pk=property.pk).exists():
        property = None
    return Scope(membership, property)


# ---------------------------------------------------------------------------
# Occupancy
# ---------------------------------------------------------------------------


@dataclass
class Occupancy:
    label: str = ""
    key: object = None
    rentable: int = 0
    occupied: int = 0
    reserved: int = 0
    maintenance: int = 0

    @property
    def vacant(self) -> int:
        return self.rentable - self.occupied

    @property
    def rate(self) -> Decimal | None:
        return rate(self.occupied, self.rentable)


def occupancy_by_property(sc: Scope, day: datetime.date) -> list[Occupancy]:
    """Rentable units (live, not Inactive, on live properties) and how many are occupied on the day."""
    Manual = Unit.ManualStatus
    occupied_ids = occupying_leases(day).values("unit_id")
    units = (Unit.objects.filter(organization=sc.organization, property__in=sc.properties(include_archived=False))
             .exclude(manual_status=Manual.INACTIVE))
    rows = (units.values("property_id", "property__name", "property__public_id").order_by("property__name")
            .annotate(rentable=Count("pk"),
                      occupied=Count("pk", filter=Q(pk__in=occupied_ids)),
                      reserved=Count("pk", filter=Q(manual_status=Manual.RESERVED) & ~Q(pk__in=occupied_ids)),
                      maintenance=Count("pk", filter=Q(manual_status=Manual.UNDER_MAINTENANCE)
                                        & ~Q(pk__in=occupied_ids))))
    return [Occupancy(label=r["property__name"], key=r["property__public_id"], rentable=r["rentable"],
                      occupied=r["occupied"], reserved=r["reserved"], maintenance=r["maintenance"]) for r in rows]


def occupancy(sc: Scope, day: datetime.date) -> Occupancy:
    return total_occupancy(occupancy_by_property(sc, day))


def total_occupancy(rows: list[Occupancy]) -> Occupancy:
    total = Occupancy()
    for row in rows:
        total.rentable += row.rentable
        total.occupied += row.occupied
        total.reserved += row.reserved
        total.maintenance += row.maintenance
    return total


# ---------------------------------------------------------------------------
# Rent expected and collected, cash received
# ---------------------------------------------------------------------------


@dataclass
class MonthFigures:
    month: datetime.date
    expected: Decimal = ZERO
    collected: Decimal = ZERO
    cash: Decimal = ZERO

    @property
    def label(self) -> str:
        return f"{self.month:%b %Y}"

    @property
    def outstanding(self) -> Decimal:
        return self.expected - self.collected

    @property
    def rate(self) -> Decimal | None:
        return rate(self.collected, self.expected)


def _billed_month():
    return Coalesce("billing_month", Cast(TruncMonth("invoice__period_start"), DateField()))


def months(sc: Scope, first: datetime.date, last: datetime.date) -> list[MonthFigures]:
    """Expected rent, rent collected for the month and cash received, for each month first..last."""
    first, last = month_start(first), month_start(last)
    rows: dict[datetime.date, MonthFigures] = {}
    m = first
    while m <= last:
        rows[m] = MonthFigures(m)
        m = add_months(m, 1)
    props = sc.properties()

    rent_lines = (InvoiceLine.objects
                  .filter(organization=sc.organization, is_void=False, invoice__status__in=LIVE_INVOICE,
                          lease__unit__property__in=props, charge_type__category=ChargeType.Category.RENT)
                  .annotate(month=_billed_month()).filter(month__gte=first, month__lte=last)
                  .values("invoice_id", "month").annotate(total=Sum("amount")))
    rent_by_invoice: dict[int, dict[datetime.date, Decimal]] = defaultdict(dict)
    for r in rent_lines:
        rows[r["month"]].expected += r["total"]
        rent_by_invoice[r["invoice_id"]][r["month"]] = r["total"]

    if rent_by_invoice:
        whole = dict(InvoiceLine.objects.filter(invoice_id__in=rent_by_invoice, is_void=False)
                     .values("invoice_id").annotate(t=Sum("amount")).values_list("invoice_id", "t"))
        paid = dict(PaymentAllocation.objects.filter(invoice_id__in=rent_by_invoice,
                                                     payment__status=Payment.Status.CONFIRMED)
                    .values("invoice_id").annotate(t=Sum("amount")).values_list("invoice_id", "t"))
        for invoice_id, by_month in rent_by_invoice.items():
            amount, total = paid.get(invoice_id, ZERO), whole.get(invoice_id, ZERO)
            if not amount or not total:
                continue
            for month, rent in by_month.items():
                rows[month].collected += round_money(amount * rent / total)

    cash = (Payment.objects.filter(organization=sc.organization, status=Payment.Status.CONFIRMED,
                                   paid_at__gte=first, paid_at__lte=month_end(last),
                                   lease__unit__property__in=props)
            .annotate(month=Cast(TruncMonth("paid_at"), DateField())).values("month")
            .annotate(total=Sum("amount")))
    for r in cash:
        rows[r["month"]].cash += r["total"]
    return list(rows.values())


def trend(sc: Scope, last: datetime.date, count: int = TREND_MONTHS) -> list[MonthFigures]:
    return months(sc, add_months(month_start(last), 1 - count), last)


# ---------------------------------------------------------------------------
# Arrears and the M-Pesa inbox
# ---------------------------------------------------------------------------


@dataclass
class Arrears:
    buckets: list[tuple[str, str, Decimal]] = field(default_factory=list)
    leases: int = 0

    @property
    def total(self) -> Decimal:
        return sum((amount for _key, _label, amount in self.buckets), ZERO)


def arrears(sc: Scope, today: datetime.date) -> Arrears:
    """What is overdue today, by aging bucket (FIFO, billing.selectors). "Not yet due" is left out."""
    rows = billing_selectors.arrears(sc.membership, today)
    if sc.selected_property is not None:
        rows = [r for r in rows if r.lease.unit.property_id == sc.selected_property.pk]
    totals = billing_selectors.aging_totals(rows)
    return Arrears(buckets=[(key, label, totals[key]) for key, label, *_rest in billing_selectors.AGING_BUCKETS
                            if key != "current"],
                   leases=len(rows))


@dataclass
class Unmatched:
    count: int = 0
    amount: Decimal = ZERO


def unmatched_mpesa(sc: Scope) -> Unmatched | None:
    """Payments waiting in the M-Pesa inbox; None when the member cannot see M-Pesa."""
    if not can(sc.membership, "mpesa.view_transactions"):
        return None
    txs = visible_transactions(sc.membership).filter(status=MpesaTransaction.Status.UNMATCHED)
    if sc.selected_property is not None:
        txs = txs.filter(payment_account__properties__property=sc.selected_property).distinct()
    agg = txs.order_by().aggregate(n=Count("pk", distinct=True), s=Sum("amount"))
    return Unmatched(count=agg["n"] or 0, amount=agg["s"] or ZERO)


# ---------------------------------------------------------------------------
# Collectability grade (D-052)
# ---------------------------------------------------------------------------

GRADE_INVOICES = 6
GRADE_WINDOW = datetime.timedelta(days=365)
GRADE_MIN_INVOICES = 2
# (grade, highest average days late); A needs every invoice paid in time.
GRADE_LIMITS = (("B", 5), ("C", 15), ("D", 30))
GRADES = ("A", "B", "C", "D", "E")


@dataclass
class Grade:
    """How reliably a tenancy pays: A (always in time) to E, or None ("New") without enough history."""

    grade: str | None = None
    invoices: int = 0
    on_time: int = 0
    average_late: Decimal = ZERO
    # Days the oldest invoice still unpaid is past its due date plus grace.
    open_late: int = 0

    @property
    def label(self) -> str:
        return self.grade or _("New")


def tenancies(leases) -> dict[int, list[int]]:
    """Each lease's tenancy: its pk and the leases it renewed or moved from, in one query per org."""
    leases = list(leases)
    if not leases:
        return {}
    previous = dict(Lease.all_objects.filter(organization_id__in={lease.organization_id for lease in leases})
                    .exclude(status=Lease.Status.DRAFT).values_list("pk", "previous_lease_id"))
    out = {}
    for lease in leases:
        chain, pk = [], lease.pk
        while pk is not None and pk not in chain and len(chain) < 100:
            chain.append(pk)
            pk = previous.get(pk)
        out[lease.pk] = chain
    return out


def _grade(days: list[int], open_late: int) -> Grade:
    g = Grade(invoices=len(days), on_time=sum(1 for d in days if d == 0), open_late=open_late)
    if not days:
        return g
    g.average_late = (Decimal(sum(days)) / len(days)).quantize(Decimal("0.1"))
    if len(days) < GRADE_MIN_INVOICES:
        return g
    if g.on_time == len(days):
        grade = "A"
    else:
        grade = next((letter for letter, limit in GRADE_LIMITS if g.average_late <= limit), "E")
    if open_late > 60:
        grade = "E"
    elif open_late > 30:
        grade = max(grade, "D")
    g.grade = grade
    return g


def grades(leases, today: datetime.date) -> dict[int, Grade]:
    """The grade of each lease's tenancy from its last invoices past due (D-052)."""
    chains = tenancies(leases)
    owner_of = {pk: lease_pk for lease_pk, chain in chains.items() for pk in chain}
    invoices = (Invoice.objects.filter(lease_id__in=owner_of, status__in=(*Invoice.OPEN, Invoice.Status.PAID),
                                       total__gt=0, overdue_after__lt=today,
                                       overdue_after__gte=today - GRADE_WINDOW)
                .order_by("-overdue_after", "-pk").only("pk", "lease_id", "total", "overdue_after"))
    window: dict[int, list[Invoice]] = defaultdict(list)
    for inv in invoices:
        # A lease in two tenancies (it cannot be, but be safe) counts for the first.
        rows = window[owner_of[inv.lease_id]]
        if len(rows) < GRADE_INVOICES:
            rows.append(inv)
    ids = [inv.pk for rows in window.values() for inv in rows]
    paid: dict[int, list[tuple[datetime.date, Decimal]]] = defaultdict(list)
    for invoice_id, paid_at, amount in (PaymentAllocation.objects
                                        .filter(invoice_id__in=ids, payment__status=Payment.Status.CONFIRMED)
                                        .order_by("payment__paid_at", "pk")
                                        .values_list("invoice_id", "payment__paid_at", "amount")):
        paid[invoice_id].append((paid_at, amount))
    out = {}
    for lease_pk in chains:
        days, open_late = [], 0
        for inv in window.get(lease_pk, []):
            settled, running = None, ZERO
            for paid_at, amount in paid.get(inv.pk, []):
                running += amount
                if running >= inv.total:
                    settled = paid_at
                    break
            if settled is None:
                open_late = max(open_late, (today - inv.overdue_after).days)
            days.append(max(((settled or today) - inv.overdue_after).days, 0))
        out[lease_pk] = _grade(days, open_late)
    return out


# ---------------------------------------------------------------------------
# The dashboard
# ---------------------------------------------------------------------------


@dataclass
class Dashboard:
    scope: Scope
    month: datetime.date
    day: datetime.date
    occupancy: Occupancy
    by_property: list[Occupancy]
    current: MonthFigures
    trend: list[MonthFigures]
    arrears: Arrears
    unmatched: Unmatched | None

    @property
    def currency(self) -> str:
        return self.scope.organization.currency

    @property
    def is_current_month(self) -> bool:
        return self.month == month_start(timezone.localdate())


def dashboard(membership: Membership, *, month: datetime.date | None = None,
              property: Property | None = None, today: datetime.date | None = None) -> Dashboard:
    today = today or timezone.localdate()
    sc = scope(membership, property)
    month = month_start(month or today)
    # Occupancy on the day that stands for the month: today, or the month's last day if it is past.
    day = min(today, month_end(month))
    by_property = occupancy_by_property(sc, day)
    rows = trend(sc, month)
    return Dashboard(scope=sc, month=month, day=day, occupancy=total_occupancy(by_property), by_property=by_property,
                     current=rows[-1], trend=rows, arrears=arrears(sc, today), unmatched=unmatched_mpesa(sc))
