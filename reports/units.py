"""Unit by unit (D-112): every rentable unit over the last 12 months.

A landlord with 30 units reads the portfolio one row per unit: who paid each month and when, what each
unit rents for against its asking rent, how long it stood empty, what it owes and when the lease ends.
Two breakdowns also help larger portfolios: by kind of unit, and by the day of the month tenants pay.

Nothing new is stored. Rent expected and collected use the same split as the dashboard (metrics._rent),
so a unit's months add up to the dashboard's for the units still in use.
"""

import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import ExtractDay
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.permissions import can
from billing import selectors as billing_selectors
from billing.models import ChargeType, Invoice, InvoiceLine
from core.money import ZERO, round_money
from leases.models import Lease, LeaseRentChange
from leases.services import occupying_leases
from payments.models import Payment, PaymentAllocation
from properties.models import Unit

from . import metrics
from .metrics import add_months, month_end, month_start, rate

WINDOW = 12
# More units than this in view: the page asks for a property before listing them; the summaries count all.
UNIT_LIMIT = 200
ENDING_WITHIN = 90

PAID, LATE, PART, UNPAID, DUE, NONE = "paid", "late", "part", "unpaid", "due", "none"
STATES = (
    (PAID, _("Paid in time")),
    (LATE, _("Paid late")),
    (PART, _("Part paid")),
    (UNPAID, _("Not paid, overdue")),
    (DUE, _("Not due yet")),
    (NONE, _("Nothing billed")),
)
STATE_LABELS = dict(STATES)
# (label, first day, last day) of the month a payment is dated.
PAY_DAYS = (("1–5", 1, 5), ("6–10", 6, 10), ("11–15", 11, 15), ("16–20", 16, 20), ("21–25", 21, 25),
            ("26–31", 26, 31))


@dataclass
class Cell:
    """One unit's rent for one month."""

    month: datetime.date
    state: str = NONE
    expected: Decimal = ZERO
    collected: Decimal = ZERO

    @property
    def label(self) -> str:
        return str(STATE_LABELS[self.state])


@dataclass
class UnitRow:
    unit: Unit
    status: str  # occupied, vacant, reserved or maintenance
    lease: Lease | None = None
    rent: Decimal | None = None  # the occupying lease's monthly rent on the day
    rent_since: datetime.date | None = None  # when that rent took effect
    cells: list[Cell] = field(default_factory=list)
    empty_days: int = 0
    days_in_window: int = 0
    overdue: Decimal = ZERO
    days_late: int = 0
    ends_on: datetime.date | None = None  # the move-out day under notice, else the contract end
    grade: metrics.Grade | None = None

    @property
    def expected(self) -> Decimal:
        return sum((c.expected for c in self.cells), ZERO)

    @property
    def collected(self) -> Decimal:
        return sum((c.collected for c in self.cells), ZERO)

    @property
    def rate(self) -> Decimal | None:
        return rate(self.collected, self.expected)

    @property
    def below_asking(self) -> Decimal | None:
        """How far the rent is under the asking rent a month; None when it is not under or either is missing."""
        asking = self.unit.list_rent
        if self.rent is None or asking is None or self.rent >= asking:
            return None
        return asking - self.rent

    @property
    def empty_loss(self) -> Decimal | None:
        """About what the empty days cost at the asking rent (a month counted as 365 ÷ 12 days)."""
        if self.unit.list_rent is None or not self.empty_days:
            return None
        return round_money(self.unit.list_rent * 12 * self.empty_days / 365)

    @property
    def months_paid_in_time(self) -> int:
        return sum(1 for c in self.cells if c.state == PAID)

    @property
    def months_billed(self) -> int:
        return sum(1 for c in self.cells if c.state != NONE)


@dataclass
class TypeRow:
    """Units of one kind, e.g. "Bedsitter" or a label such as "2 bedroom"."""

    label: str
    units: int = 0
    occupied: int = 0
    rent_total: Decimal = ZERO
    rented: int = 0  # occupied units with a rent, for the average
    asking_total: Decimal = ZERO
    asked: int = 0
    expected: Decimal = ZERO
    collected: Decimal = ZERO
    empty_days: int = 0
    days: int = 0

    @property
    def occupancy(self) -> Decimal | None:
        return rate(self.occupied, self.units)

    @property
    def average_rent(self) -> Decimal | None:
        return round_money(self.rent_total / self.rented) if self.rented else None

    @property
    def average_asking(self) -> Decimal | None:
        return round_money(self.asking_total / self.asked) if self.asked else None

    @property
    def rate(self) -> Decimal | None:
        return rate(self.collected, self.expected)

    @property
    def empty_share(self) -> Decimal | None:
        """The share of the 12 months these units stood empty."""
        return rate(self.empty_days, self.days)


@dataclass
class PayDay:
    label: str
    count: int = 0
    amount: Decimal = ZERO
    share: Decimal | None = None  # of the amount


@dataclass
class Summary:
    units: int = 0
    occupied: int = 0
    billed: int = 0  # units with rent billed this month
    paid: int = 0  # of those, paid in full (in time or late)
    part: int = 0
    unpaid: int = 0
    due: int = 0
    average_rent: Decimal | None = None
    below_asking_units: int = 0
    below_asking: Decimal = ZERO  # a month
    empty_now: int = 0
    empty_days: int = 0
    empty_loss: Decimal = ZERO
    always_in_time: int = 0  # occupied units paid in time every month billed in the window
    ending: list[UnitRow] = field(default_factory=list)
    expected: Decimal = ZERO
    collected: Decimal = ZERO

    @property
    def rate(self) -> Decimal | None:
        return rate(self.collected, self.expected)


@dataclass
class UnitReport:
    scope: metrics.Scope
    day: datetime.date
    months: list[datetime.date]
    rows: list[UnitRow]
    summary: Summary
    by_type: list[TypeRow]
    pay_days: list[PayDay]
    shows_arrears: bool

    @property
    def currency(self) -> str:
        return self.scope.organization.currency

    @property
    def this_month(self) -> datetime.date:
        return self.months[-1]

    @property
    def too_many(self) -> bool:
        """True when the table would be too long to read: the page asks for a property first."""
        return self.scope.selected_property is None and len(self.rows) > UNIT_LIMIT


# ---------------------------------------------------------------------------
# The parts
# ---------------------------------------------------------------------------


def _cells(sc: metrics.Scope, unit_ids: list[int], first: datetime.date, last: datetime.date,
           today: datetime.date) -> dict[int, dict[datetime.date, Cell]]:
    """Each unit's rent by billed month, and whether each month's invoices were paid in time."""
    return rent_states(sc.organization, Q(lease__unit_id__in=unit_ids), "lease__unit_id", first, last, today)


def rent_states(organization, where: Q, key: str, first: datetime.date, last: datetime.date,
                today: datetime.date) -> dict[int, dict[datetime.date, Cell]]:
    """Rent by billed month for the invoice lines `where` selects, grouped by `key` (a unit or a lease), and
    whether each month's invoices were paid in time. Payment patterns (D-114) use it for a scope or one lease."""
    lines = (InvoiceLine.objects
             .filter(where, organization=organization, is_void=False, invoice__status__in=metrics.LIVE_INVOICE,
                     charge_type__category=ChargeType.Category.RENT)
             .annotate(month=metrics._billed_month()).filter(month__gte=first, month__lte=last)
             .values("invoice_id", "month", key).annotate(total=Sum("amount")))
    rent: dict[int, dict[datetime.date, Decimal]] = defaultdict(dict)
    unit_of: dict[int, int] = {}
    for r in lines:
        rent[r["invoice_id"]][r["month"]] = r["total"]
        unit_of[r["invoice_id"]] = r[key]

    out: dict[int, dict[datetime.date, Cell]] = defaultdict(dict)
    if not rent:
        return out
    whole = dict(InvoiceLine.objects.filter(invoice_id__in=rent, is_void=False)
                 .values("invoice_id").annotate(t=Sum("amount")).values_list("invoice_id", "t"))
    late_after = dict(Invoice.objects.filter(pk__in=rent).values_list("pk", "overdue_after"))
    paid: dict[int, list[tuple[datetime.date, Decimal]]] = defaultdict(list)
    for invoice_id, paid_at, amount in (PaymentAllocation.objects
                                        .filter(invoice_id__in=rent, payment__status=Payment.Status.CONFIRMED)
                                        .order_by("payment__paid_at", "pk")
                                        .values_list("invoice_id", "payment__paid_at", "amount")):
        paid[invoice_id].append((paid_at, amount))

    # Each invoice's state; a month with several invoices takes the worst of them.
    order = {s: i for i, s in enumerate((PAID, LATE, DUE, PART, UNPAID))}
    for invoice_id, by_month in rent.items():
        total, payments = whole.get(invoice_id, ZERO), paid.get(invoice_id, [])
        amount = sum((a for _d, a in payments), ZERO)
        settled, running = None, ZERO
        for paid_at, a in payments:
            running += a
            if running >= total:
                settled = paid_at
                break
        if settled is not None:
            state = LATE if settled > late_after[invoice_id] else PAID
        elif amount > 0:
            state = PART
        else:
            state = UNPAID if today > late_after[invoice_id] else DUE
        for month, amount_rent in by_month.items():
            cell = out[unit_of[invoice_id]].setdefault(month, Cell(month))
            cell.expected += amount_rent
            if amount and total:
                cell.collected += round_money(amount * amount_rent / total)
            if cell.state == NONE or order[state] > order[cell.state]:
                cell.state = state
    return out


def _empty_days(unit: Unit, leases: list[Lease], first: datetime.date, day: datetime.date) -> tuple[int, int]:
    """(days empty, days counted) from the window's first day, or from when the unit came in, to `day`."""
    starts = [lease.start_date for lease in leases] + [timezone.localtime(unit.created_at).date()]
    begin = max(first, min(starts))
    if begin > day:
        return 0, 0
    spans = sorted((max(lease.start_date, begin), min(lease.ended_on or day, day)) for lease in leases)
    covered, cursor = 0, begin
    for start, end in spans:
        start = max(start, cursor)
        if end >= start:
            covered += (end - start).days + 1
            cursor = end + datetime.timedelta(days=1)
    counted = (day - begin).days + 1
    return counted - covered, counted


def pay_days(sc: metrics.Scope, first: datetime.date, last: datetime.date) -> list[PayDay]:
    """Confirmed payments in the window by the day of the month they are dated."""
    rows = (metrics._confirmed_payments(sc, first, last).annotate(d=ExtractDay("paid_at")).values("d").order_by()
            .annotate(n=Count("pk"), total=Sum("amount")))
    out = [PayDay(label) for label, _lo, _hi in PAY_DAYS]
    for r in rows:
        slot = next(i for i, (_label, lo, hi) in enumerate(PAY_DAYS) if lo <= r["d"] <= hi)
        out[slot].count += r["n"]
        out[slot].amount += r["total"]
    total = sum((p.amount for p in out), ZERO)
    for p in out:
        p.share = rate(p.amount, total)
    return out if total else []


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def unit_report(membership, *, property=None, today: datetime.date | None = None) -> UnitReport:
    today = today or timezone.localdate()
    sc = metrics.scope(membership, property)
    last = month_start(today)
    first = add_months(last, 1 - WINDOW)
    months = [add_months(first, i) for i in range(WINDOW)]

    units = list(Unit.objects.filter(organization=sc.organization, property__in=sc.properties(include_archived=False))
                 .exclude(manual_status=Unit.ManualStatus.INACTIVE).select_related("property")
                 .order_by("property__name", "code"))
    ids = [u.pk for u in units]
    occupying = {lease.unit_id: lease for lease in occupying_leases(today).filter(unit_id__in=ids)
                 .prefetch_related("lease_tenants__tenant")}
    history: dict[int, list[Lease]] = defaultdict(list)
    for lease in (Lease.all_objects.filter(unit_id__in=ids).exclude(status=Lease.Status.DRAFT)
                  .filter(start_date__lte=today).only("pk", "unit_id", "start_date", "ended_on")):
        history[lease.unit_id].append(lease)
    changes: dict[int, list[LeaseRentChange]] = defaultdict(list)
    for change in LeaseRentChange.objects.filter(lease__in=occupying.values()).order_by("effective_from"):
        changes[change.lease_id].append(change)
    cells = _cells(sc, ids, first, last, today)
    grades = metrics.grades(occupying.values(), today)

    shows_arrears = can(membership, "invoices.view")
    owed: dict[int, list] = defaultdict(lambda: [ZERO, 0])
    for row in billing_selectors.arrears(membership, today) if shows_arrears else []:
        overdue = row.balance - row.buckets["current"]
        if overdue > 0:
            owed[row.lease.unit_id][0] += overdue
            owed[row.lease.unit_id][1] = max(owed[row.lease.unit_id][1], row.days_overdue(today))

    Manual = Unit.ManualStatus
    rows = []
    for unit in units:
        lease = occupying.get(unit.pk)
        if lease is not None:
            status = "occupied"
        else:
            status = {Manual.RESERVED: "reserved", Manual.UNDER_MAINTENANCE: "maintenance"}.get(unit.manual_status,
                                                                                               "vacant")
        row = UnitRow(unit=unit, status=status, lease=lease)
        if lease is not None:
            on = max(today, lease.start_date)
            current = next((c for c in reversed(changes[lease.pk]) if c.effective_from <= on), None)
            if current is not None:
                row.rent, row.rent_since = current.amount, current.effective_from
            row.ends_on = lease.move_out_by or lease.ended_on or lease.end_date
            row.grade = grades.get(lease.pk)
        row.cells = [cells[unit.pk].get(m, Cell(m)) for m in months]
        row.empty_days, row.days_in_window = _empty_days(unit, history[unit.pk], first, today)
        row.overdue, row.days_late = owed[unit.pk] if unit.pk in owed else (ZERO, 0)
        rows.append(row)

    return UnitReport(scope=sc, day=today, months=months, rows=rows, summary=_summary(rows, today),
                      by_type=_by_type(rows), pay_days=pay_days(sc, first, month_end(last)),
                      shows_arrears=shows_arrears)


def _summary(rows: list[UnitRow], today: datetime.date) -> Summary:
    s = Summary(units=len(rows))
    rents = []
    for row in rows:
        now = row.cells[-1] if row.cells else None
        if now is not None and now.state != NONE:
            s.billed += 1
            s.paid += now.state in (PAID, LATE)
            s.part += now.state == PART
            s.unpaid += now.state == UNPAID
            s.due += now.state == DUE
        if row.status == "occupied":
            s.occupied += 1
            if row.rent is not None:
                rents.append(row.rent)
            if row.months_billed and row.months_paid_in_time == row.months_billed:
                s.always_in_time += 1
        else:
            s.empty_now += 1
        if row.below_asking is not None:
            s.below_asking_units += 1
            s.below_asking += row.below_asking
        s.empty_days += row.empty_days
        s.empty_loss += row.empty_loss or ZERO
        s.expected += row.expected
        s.collected += row.collected
        if row.ends_on is not None and today <= row.ends_on <= today + datetime.timedelta(days=ENDING_WITHIN):
            s.ending.append(row)
    s.average_rent = round_money(sum(rents, ZERO) / len(rents)) if rents else None
    s.ending.sort(key=lambda r: r.ends_on)
    return s


def type_label(unit: Unit) -> str:
    return unit.type_label.strip() or str(unit.get_unit_type_display())


def _by_type(rows: list[UnitRow]) -> list[TypeRow]:
    """Units grouped by their label (e.g. "2 bedroom"), or their type when they have no label; most units first."""
    groups: dict[str, TypeRow] = {}
    for row in rows:
        label = type_label(row.unit)
        g = groups.setdefault(label.lower(), TypeRow(label))
        g.units += 1
        if row.status == "occupied":
            g.occupied += 1
            if row.rent is not None:
                g.rent_total += row.rent
                g.rented += 1
        if row.unit.list_rent is not None:
            g.asking_total += row.unit.list_rent
            g.asked += 1
        g.expected += row.expected
        g.collected += row.collected
        g.empty_days += row.empty_days
        g.days += row.days_in_window
    return sorted(groups.values(), key=lambda g: (-g.units, g.label.lower()))
