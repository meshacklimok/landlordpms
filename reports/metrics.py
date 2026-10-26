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

from django.core.exceptions import PermissionDenied
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


def occupancy_trend(sc: Scope, last: datetime.date, today: datetime.date,
                    count: int = TREND_MONTHS) -> list[Occupancy]:
    """Occupancy on the last day of each of the `count` months to `last` (today for this month), labelled by month.

    Units are counted as they are set up now: a unit added later counts as empty in the months before it.
    """
    Manual = Unit.ManualStatus
    months = [add_months(month_start(last), -n) for n in range(count - 1, -1, -1)]
    days = [min(today, month_end(m)) for m in months]
    # Two queries for every month, not one per month: the units once, and every lease that could occupy one.
    units = list(Unit.objects.filter(organization=sc.organization,
                                     property__in=sc.properties(include_archived=False))
                 .exclude(manual_status=Manual.INACTIVE).values_list("pk", "manual_status"))
    leases = list(Lease.objects.exclude(status=Lease.Status.DRAFT).filter(start_date__lte=max(days))
                  .filter(unit__in=[pk for pk, _status in units])
                  .values_list("unit_id", "start_date", "ended_on"))
    out = []
    for m, day in zip(months, days, strict=True):
        occupied = {unit for unit, started, ended in leases if started <= day and (ended is None or ended >= day)}
        row = Occupancy(label=f"{m:%b %Y}", key=m, rentable=len(units))
        for pk, status in units:
            if pk in occupied:
                row.occupied += 1
            elif status == Manual.RESERVED:
                row.reserved += 1
            elif status == Manual.UNDER_MAINTENANCE:
                row.maintenance += 1
        out.append(row)
    return out


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


def _billed_since(first: datetime.date) -> Q:
    """Lets the database use the (organization, billing_month) index: a line whose billing_month is set cannot be
    in a month before `first` unless that column is, so this only narrows the rows the month filter then checks."""
    return Q(billing_month__gte=first) | Q(billing_month__isnull=True)


def _billed_month():
    return Coalesce("billing_month", Cast(TruncMonth("invoice__period_start"), DateField()))


def _rent(sc: Scope, first: datetime.date, last: datetime.date) -> dict[tuple[int, datetime.date], list[Decimal]]:
    """[expected, collected] rent by (property id, billed month), for the months first..last."""
    out: dict[tuple[int, datetime.date], list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    rent_lines = (InvoiceLine.objects
                  .filter(organization=sc.organization, is_void=False, invoice__status__in=LIVE_INVOICE,
                          lease__unit__property__in=sc.properties(), charge_type__category=ChargeType.Category.RENT)
                  .filter(_billed_since(first))
                  .annotate(month=_billed_month()).filter(month__gte=first, month__lte=last)
                  .values("invoice_id", "month", "lease__unit__property_id").annotate(total=Sum("amount")))
    rent_by_invoice: dict[int, dict[datetime.date, Decimal]] = defaultdict(dict)
    place: dict[int, int] = {}
    for r in rent_lines:
        out[r["lease__unit__property_id"], r["month"]][0] += r["total"]
        rent_by_invoice[r["invoice_id"]][r["month"]] = r["total"]
        place[r["invoice_id"]] = r["lease__unit__property_id"]

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
                out[place[invoice_id], month][1] += round_money(amount * rent / total)
    return out


def _confirmed_payments(sc: Scope, first: datetime.date, last: datetime.date):
    """Confirmed payments on the properties in scope, dated first..last."""
    return Payment.objects.filter(organization=sc.organization, status=Payment.Status.CONFIRMED,
                                  paid_at__gte=first, paid_at__lte=last, lease__unit__property__in=sc.properties())


def months(sc: Scope, first: datetime.date, last: datetime.date) -> list[MonthFigures]:
    """Expected rent, rent collected for the month and cash received, for each month first..last."""
    first, last = month_start(first), month_start(last)
    rows: dict[datetime.date, MonthFigures] = {}
    m = first
    while m <= last:
        rows[m] = MonthFigures(m)
        m = add_months(m, 1)
    for (_place, month), (expected, collected) in _rent(sc, first, last).items():
        rows[month].expected += expected
        rows[month].collected += collected

    cash = (_confirmed_payments(sc, first, month_end(last))
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


def arrears(sc: Scope, today: datetime.date, rows: list | None = None) -> Arrears:
    """What is overdue today, by aging bucket (FIFO, billing.selectors). "Not yet due" is left out.

    `rows` is `billing_selectors.arrears(sc.membership, today)` when the caller already has it.
    """
    rows = billing_selectors.arrears(sc.membership, today) if rows is None else rows
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
# Comparisons and breakdowns (D-110)
# ---------------------------------------------------------------------------


@dataclass
class Change:
    """A figure against the same figure earlier. No change is shown when either side is missing."""

    now: Decimal | int | None
    before: Decimal | int | None
    label: str = ""  # what `before` is, e.g. "Aug 2026"

    @property
    def delta(self):
        if self.now is None or self.before is None:
            return None
        return self.now - self.before

    @property
    def ratio(self) -> Decimal | None:
        """The change as a fraction of `before`; None when `before` is 0 (no percentage from nothing)."""
        delta = self.delta
        return None if delta is None or not self.before else Decimal(delta) / abs(Decimal(self.before))


@dataclass
class PropertyCollection:
    label: str
    key: object
    expected: Decimal = ZERO
    collected: Decimal = ZERO

    @property
    def outstanding(self) -> Decimal:
        return self.expected - self.collected

    @property
    def rate(self) -> Decimal | None:
        return rate(self.collected, self.expected)


def collection_by_property(sc: Scope, month: datetime.date) -> list[PropertyCollection]:
    """Rent expected and collected for the month by property, the lowest collection rate first.

    The same figures as the month's totals (`months`), split by the property each invoice's lease is on.
    Properties with nothing billed in the month are left out.
    """
    month = month_start(month)
    names = {p.pk: p for p in sc.properties()}
    rows = [PropertyCollection(label=names[pk].name, key=names[pk].public_id, expected=expected, collected=collected)
            for (pk, _m), (expected, collected) in _rent(sc, month, month).items() if expected]
    return sorted(rows, key=lambda r: (r.rate, -r.outstanding, r.label.lower()))


@dataclass
class MethodTotal:
    method: str
    label: str
    count: int
    amount: Decimal


def cash_by_method(sc: Scope, month: datetime.date) -> list[MethodTotal]:
    """Cash received in the month by payment method, the largest first."""
    month = month_start(month)
    labels = dict(Payment.Method.choices)
    rows = (_confirmed_payments(sc, month, month_end(month)).values("method").order_by()
            .annotate(n=Count("pk"), total=Sum("amount")))
    return sorted((MethodTotal(r["method"], str(labels.get(r["method"], r["method"])), r["n"], r["total"])
                   for r in rows), key=lambda r: (-r.amount, r.label))


@dataclass
class VacancyLoss:
    """Asking rent of the rentable units standing empty on the day: rent a full building would bill."""

    units: int = 0
    amount: Decimal = ZERO
    unpriced: int = 0  # vacant units with no asking rent, so not in `amount`


def vacancy_loss(sc: Scope, day: datetime.date) -> VacancyLoss:
    vacant = (Unit.objects.filter(organization=sc.organization, property__in=sc.properties(include_archived=False))
              .exclude(manual_status=Unit.ManualStatus.INACTIVE)
              .exclude(pk__in=occupying_leases(day).values("unit_id")))
    agg = vacant.aggregate(n=Count("pk"), s=Sum("list_rent"), unpriced=Count("pk", filter=Q(list_rent__isnull=True)))
    return VacancyLoss(units=agg["n"] or 0, amount=agg["s"] or ZERO, unpriced=agg["unpriced"] or 0)


def grade_mix(sc: Scope, day: datetime.date, today: datetime.date) -> list[tuple[str, int]]:
    """How many occupying leases have each collectability grade (D-052), A to E, then "New"."""
    leases = list(occupying_leases(day).filter(organization=sc.organization,
                                               unit__property__in=sc.properties(include_archived=False))
                  .only("pk", "organization_id"))
    counts = dict.fromkeys(GRADES, 0)
    new = 0
    for g in grades(leases, today).values():
        if g.grade is None:
            new += 1
        else:
            counts[g.grade] += 1
    return [*counts.items(), (str(_("New")), new)] if leases else []


@dataclass
class Debtor:
    lease: Lease
    overdue: Decimal
    days: int


def largest_debts(sc: Scope, today: datetime.date, rows: list, count: int = 5) -> list[Debtor]:
    """The leases with the most overdue today, from the arrears rows the dashboard already has."""
    if sc.selected_property is not None:
        rows = [r for r in rows if r.lease.unit.property_id == sc.selected_property.pk]
    debtors = [Debtor(r.lease, r.balance - r.buckets["current"], r.days_overdue(today)) for r in rows]
    return sorted(debtors, key=lambda d: (-d.overdue, -d.days))[:count]


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
    # D-110: the month against last month and the same month last year, and where the money came from.
    previous: MonthFigures | None = None
    last_year: MonthFigures | None = None
    occupancy_before: Occupancy | None = None
    collection: list[PropertyCollection] = field(default_factory=list)
    methods: list[MethodTotal] = field(default_factory=list)
    vacancy: VacancyLoss = field(default_factory=VacancyLoss)
    grade_mix: list[tuple[str, int]] = field(default_factory=list)
    debtors: list[Debtor] = field(default_factory=list)
    occupancy_trend: list[Occupancy] = field(default_factory=list)

    @property
    def currency(self) -> str:
        return self.scope.organization.currency

    @property
    def is_current_month(self) -> bool:
        return self.month == month_start(timezone.localdate())

    def _change(self, other: MonthFigures | None, name: str) -> Change:
        if other is None:
            return Change(None, None)
        return Change(getattr(self.current, name), getattr(other, name), other.label)

    @property
    def changes(self) -> dict:
        """{figure: (against last month, against the same month last year)} for the headline money figures."""
        return {name: (self._change(self.previous, name), self._change(self.last_year, name))
                for name in ("expected", "collected", "rate", "cash")}

    @property
    def occupancy_change(self) -> Change:
        before = self.occupancy_before
        return Change(self.occupancy.occupied, None if before is None else before.occupied,
                      f"{add_months(self.month, -1):%b %Y}")

    @property
    def debtors_share(self) -> Decimal | None:
        """The share of all arrears owed by the leases in `debtors`."""
        return rate(sum((d.overdue for d in self.debtors), ZERO), self.arrears.total)

    @property
    def collected_share(self) -> Decimal | None:
        """Rent collected for the month as a share of the cash received in it; the rest paid other months,
        other charges or deposits."""
        return rate(self.current.collected, self.current.cash)


def dashboard(membership: Membership, *, month: datetime.date | None = None,
              property: Property | None = None, today: datetime.date | None = None,
              arrears_rows: list | None = None, analysis: bool = False) -> Dashboard:
    """The dashboard's figures. `analysis` adds the comparisons and breakdowns (D-110) the dashboard page shows;
    the home page and the assistant leave them out, as they do not show them."""
    today = today or timezone.localdate()
    sc = scope(membership, property)
    month = month_start(month or today)
    # Occupancy on the day that stands for the month: today, or the month's last day if it is past.
    day = min(today, month_end(month))
    by_property = occupancy_by_property(sc, day)
    rows = trend(sc, month)
    if arrears_rows is None:
        arrears_rows = billing_selectors.arrears(membership, today)
    board = Dashboard(scope=sc, month=month, day=day, occupancy=total_occupancy(by_property), by_property=by_property,
                      current=rows[-1], trend=rows, arrears=arrears(sc, today, arrears_rows),
                      unmatched=unmatched_mpesa(sc), previous=rows[-2])
    if analysis:
        last_year = add_months(month, -12)
        board.last_year = months(sc, last_year, last_year)[0]
        board.occupancy_before = occupancy(sc, month - datetime.timedelta(days=1))
        board.collection = collection_by_property(sc, month)
        board.methods = cash_by_method(sc, month)
        board.vacancy = vacancy_loss(sc, day)
        board.grade_mix = grade_mix(sc, day, today)
        board.debtors = largest_debts(sc, today, arrears_rows)
        board.occupancy_trend = occupancy_trend(sc, month, today)
    return board


# ---------------------------------------------------------------------------
# One property at a glance (D-110)
# ---------------------------------------------------------------------------


@dataclass
class PropertyGlance:
    """This month for one property against last month, what is overdue and what the empty units would bring."""

    occupancy: Occupancy
    current: MonthFigures
    previous: MonthFigures
    arrears: Arrears
    vacancy: VacancyLoss

    def change(self, name: str) -> Change:
        return Change(getattr(self.current, name), getattr(self.previous, name), self.previous.label)


def property_glance(membership: Membership, prop: Property, today: datetime.date | None = None) -> PropertyGlance:
    today = today or timezone.localdate()
    sc = scope(membership, prop)
    if sc.selected_property is None or sc.selected_property.pk != prop.pk:
        raise PermissionDenied("dashboard.view_financial")
    month = month_start(today)
    previous, current = months(sc, add_months(month, -1), month)
    return PropertyGlance(occupancy=occupancy(sc, today), current=current, previous=previous,
                          arrears=arrears(sc, today), vacancy=vacancy_loss(sc, today))
