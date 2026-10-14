"""How tenants pay (D-135): days between the due date and the day each invoice was paid in full.

For invoices whose due date falls in the period (and has passed), the day an invoice counts as paid is the day
confirmed payments allocated to it first reached its total. Days are measured from the due date, so a negative
number is early. "In time" is as everywhere else: paid by the due date plus grace days.
Unpaid invoices past their due date are counted apart and are not in the averages.
"""

import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.permissions import can, require, visible_properties
from billing.models import Invoice
from core.money import ZERO
from payments.models import Payment, PaymentAllocation
from properties.models import Property

from .metrics import add_months, month_start, rate

ROW_LIMIT = 20000
MIN_INVOICES = 2  # a lease needs this many paid invoices to be ranked
RANKED = 10

# (key, label, first day, last day) of the days from the due date.
BANDS = (
    ("early", _("Before the due date"), None, -1),
    ("due", _("On the due date"), 0, 0),
    ("d1_7", _("1 to 7 days late"), 1, 7),
    ("d8_14", _("8 to 14 days late"), 8, 14),
    ("d15_30", _("15 to 30 days late"), 15, 30),
    ("d31", _("Over 30 days late"), 31, None),
)


@dataclass
class Band:
    key: str
    label: object
    count: int = 0
    share: Decimal | None = None


def _average(days: list[int]) -> Decimal | None:
    return (Decimal(sum(days)) / len(days)).quantize(Decimal("0.1")) if days else None


def _median(days: list[int]) -> Decimal | None:
    if not days:
        return None
    ordered = sorted(days)
    mid = len(ordered) // 2
    value = Decimal(ordered[mid]) if len(ordered) % 2 else Decimal(ordered[mid - 1] + ordered[mid]) / 2
    return value.quantize(Decimal("0.1"))


@dataclass
class MonthDays:
    month: datetime.date
    days: list[int] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.month:%b %Y}"

    @property
    def average(self) -> Decimal | None:
        return _average(self.days)


@dataclass
class LeaseDays:
    lease: object
    days: list[int] = field(default_factory=list)
    in_time: int = 0
    unpaid: int = 0
    tenant: object = None

    @property
    def average(self) -> Decimal | None:
        return _average(self.days)

    @property
    def slowest(self) -> int | None:
        return max(self.days) if self.days else None


@dataclass
class BehaviourReport:
    start: datetime.date
    end: datetime.date
    invoices: int  # past due in the period
    paid: int
    unpaid: int
    in_time: int
    bands: list[Band]
    months: list[MonthDays]
    slowest: list[LeaseDays]
    steadiest: list[LeaseDays]
    average: Decimal | None
    median: Decimal | None
    show_tenants: bool
    truncated: bool
    generated_at: datetime.datetime

    @property
    def in_time_share(self) -> Decimal | None:
        return rate(self.in_time, self.paid)


def _band(days: int) -> int:
    for i, (_key, _label, lo, hi) in enumerate(BANDS):
        if (lo is None or days >= lo) and (hi is None or days <= hi):
            return i
    return len(BANDS) - 1


def behaviour(membership, start: datetime.date, end: datetime.date, prop: Property | None = None, *,
              today: datetime.date | None = None, now: datetime.datetime | None = None) -> BehaviourReport:
    require(membership, "reports.view_financial")
    today = today or timezone.localdate()
    properties = visible_properties(membership, Property.all_objects.all())
    if prop is not None:
        properties = properties.filter(pk=prop.pk)
    last = min(end, today - datetime.timedelta(days=1))
    invoices = (Invoice.objects.filter(organization=membership.organization, lease__unit__property__in=properties,
                                       status__in=(*Invoice.OPEN, Invoice.Status.PAID), total__gt=0,
                                       due_date__gte=start, due_date__lte=last)
                .select_related("lease__unit__property").order_by("due_date", "pk"))
    found = list(invoices[:ROW_LIMIT + 1])
    truncated = len(found) > ROW_LIMIT
    found = found[:ROW_LIMIT]
    paid_at: dict[int, list[tuple[datetime.date, Decimal]]] = defaultdict(list)
    for invoice_id, day, amount in (PaymentAllocation.objects
                                    .filter(invoice_id__in=[i.pk for i in found],
                                            payment__status=Payment.Status.CONFIRMED)
                                    .order_by("payment__paid_at", "pk")
                                    .values_list("invoice_id", "payment__paid_at", "amount")):
        paid_at[invoice_id].append((day, amount))

    bands = [Band(key, label) for key, label, _lo, _hi in BANDS]
    by_month: dict[datetime.date, MonthDays] = {}
    month = month_start(start)
    while month <= month_start(max(last, start)):
        by_month[month] = MonthDays(month)
        month = add_months(month, 1)
    per_lease: dict[int, LeaseDays] = {}
    every: list[int] = []
    unpaid = in_time = 0
    for inv in found:
        row = per_lease.setdefault(inv.lease_id, LeaseDays(inv.lease))
        settled, running = None, ZERO
        for day, amount in paid_at.get(inv.pk, []):
            running += amount
            if running >= inv.total:
                settled = day
                break
        if settled is None:
            unpaid += 1
            row.unpaid += 1
            continue
        if isinstance(settled, datetime.datetime):
            settled = timezone.localtime(settled).date()
        days = (settled - inv.due_date).days
        every.append(days)
        row.days.append(days)
        bands[_band(days)].count += 1
        if settled <= inv.overdue_after:
            in_time += 1
            row.in_time += 1
        by_month.setdefault(month_start(inv.due_date), MonthDays(month_start(inv.due_date))).days.append(days)
    for b in bands:
        b.share = rate(b.count, len(every))

    show = can(membership, "tenants.view")
    ranked = [r for r in per_lease.values() if len(r.days) >= MIN_INVOICES]
    slowest = sorted(ranked, key=lambda r: (-r.average, r.lease.number))[:RANKED]
    steadiest = sorted(ranked, key=lambda r: (r.average, r.lease.number))[:RANKED]
    if show:
        for r in {id(x): x for x in (*slowest, *steadiest)}.values():
            r.tenant = r.lease.primary_tenant
    return BehaviourReport(
        start=start, end=end, invoices=len(found), paid=len(every), unpaid=unpaid, in_time=in_time, bands=bands,
        months=[by_month[m] for m in sorted(by_month)], slowest=slowest, steadiest=steadiest,
        average=_average(every), median=_median(every), show_tenants=show, truncated=truncated,
        generated_at=now or timezone.now())
