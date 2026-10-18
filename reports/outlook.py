"""Rent outlook (D-172): what the leases in force say will be due over the next six months, and what is at risk.

Every other report looks back. This one reads forward from the leases:

- Contracted rent for a month is the rent of each lease in force on its rent due day that month, at the rent in
  force that day (so a rent change already recorded counts). A lease is in force from its start date to the earliest
  of its end date, the day it was closed and the move-out day agreed under a notice. An active lease past its end
  date is holding over and counts as staying; the page lists those apart. Rent is the monthly rent, whatever the
  billing frequency, so a quarterly lease shows as a third of its invoice each month.
- Rent ending in a month is the contracted rent of leases that are in force that month but not the next (so it
  is gone from the month after, unless renewed). The last month shown has no next month, so it shows none.
- Expected to collect is contracted rent at the collection rate of the last six full months (rent collected for the
  month ÷ rent billed), or nothing when no rent has been billed yet. It is an estimate; the page says so.
- Empty units' asking rent is what the units empty today would add if let; units with no asking rent are counted,
  not priced.

Nothing is stored. Leases still in draft do not count.
"""

import datetime
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.permissions import can
from core.money import ZERO, round_money
from leases.models import Lease
from leases.services import occupying_leases
from properties.models import Unit

from . import metrics
from .metrics import add_months, month_start

HORIZON = 6  # months shown, this one first
HISTORY = 6  # full months the collection rate is taken from


@dataclass
class Month:
    month: datetime.date
    leases: int = 0
    contracted: Decimal = ZERO
    ending: Decimal = ZERO
    ending_leases: int = 0
    starting: Decimal = ZERO
    expected: Decimal | None = None  # contracted × collection rate, None without a rate

    @property
    def label(self) -> str:
        return f"{self.month:%b %Y}"


@dataclass
class EndingLine:
    lease: Lease
    ends: datetime.date
    rent: Decimal
    days: int
    why: str  # "end date", "notice" or "closed"
    tenant: object = None


@dataclass
class Outlook:
    today: datetime.date
    currency: str
    months: list[Month]
    collection_rate: Decimal | None
    ending: list[EndingLine]
    holding_over: int
    holding_over_rent: Decimal
    empty_units: int
    empty_priced: int
    empty_rent: Decimal
    show_tenants: bool
    generated_at: datetime.datetime

    @property
    def contracted_total(self) -> Decimal:
        return sum((m.contracted for m in self.months), ZERO)

    @property
    def expected_total(self) -> Decimal | None:
        return (sum((m.expected for m in self.months), ZERO) if self.collection_rate is not None else None)

    @property
    def ending_total(self) -> Decimal:
        """Monthly rent that stops, added up over the months it is lost from."""
        return sum((m.ending for m in self.months), ZERO)


def _rent_on(changes: list, day: datetime.date) -> Decimal | None:
    """The rent in force on the day, from the lease's rent changes (already loaded, newest first)."""
    for change in changes:
        if change.effective_from <= day:
            return change.amount
    return None


def _end_of(lease: Lease, today: datetime.date) -> tuple[datetime.date | None, str]:
    """The last day the lease is in force, and why; (None, "") when it runs on."""
    days = []
    if lease.ended_on:
        days.append((lease.ended_on, "closed"))
    if lease.move_out_on:
        days.append((lease.move_out_on, "notice"))
    # A contract end date that has passed on an active lease is holding over: the tenant is still there.
    if lease.end_date and not (lease.end_date < today and lease.status == Lease.Status.ACTIVE):
        days.append((lease.end_date, "end date"))
    return min(days) if days else (None, "")


def outlook(membership, prop=None, *, today: datetime.date | None = None,
            now: datetime.datetime | None = None) -> Outlook:
    sc = metrics.scope(membership, prop, capability="reports.view_financial")
    today = today or timezone.localdate()
    org = membership.organization
    first = month_start(today)
    show = can(membership, "tenants.view")

    leases = (Lease.objects.for_org(org).exclude(status=Lease.Status.DRAFT)
              .filter(unit__property__in=sc.properties())
              .filter(Q(ended_on__isnull=True) | Q(ended_on__gte=first))
              .select_related("unit__property").prefetch_related("rent_changes"))
    if show:
        leases = leases.prefetch_related("lease_tenants__tenant")

    months = [Month(add_months(first, i)) for i in range(HORIZON)]
    horizon_end = metrics.month_end(months[-1].month)
    ending: list[EndingLine] = []
    holding, holding_rent = 0, ZERO
    for lease in leases:
        changes = sorted(lease.rent_changes.all(), key=lambda c: c.effective_from, reverse=True)
        end, why = _end_of(lease, today)
        in_force = []
        for m in months:
            due = m.month.replace(day=lease.due_day)
            rent = _rent_on(changes, due)
            on = rent is not None and lease.start_date <= due and (end is None or end >= due)
            in_force.append(rent if on else None)
        for m, rent, following in zip(months, in_force, in_force[1:] + [None], strict=True):
            if rent is None:
                continue
            m.leases += 1
            m.contracted += rent
            # Leaving after this month, inside the horizon: the next month's rent is lost. The last month shown
            # has no next month to compare with, so what ends after it is not counted.
            if following is None and m is not months[-1]:
                m.ending += rent
                m.ending_leases += 1
        # A lease that starts later in the horizon adds to the month it first has rent due.
        began = next((m for m, rent in zip(months, in_force) if rent is not None), None)
        if began is not None and lease.start_date > today:
            began.starting += in_force[months.index(began)]
        if end is not None and today <= end <= horizon_end and any(r is not None for r in in_force):
            rent = next(r for r in reversed(in_force) if r is not None)
            ending.append(EndingLine(lease, end, rent, (end - today).days, why,
                                     lease.primary_tenant if show else None))
        if (lease.status == Lease.Status.ACTIVE and lease.end_date and lease.end_date < today
                and lease.ended_on is None and lease.start_date <= today):
            holding += 1
            holding_rent += _rent_on(changes, today) or ZERO

    history = metrics.months(sc, add_months(first, -HISTORY), add_months(first, -1))
    billed = sum((h.expected for h in history), ZERO)
    rate = metrics.rate(sum((h.collected for h in history), ZERO), billed)
    for m in months:
        m.expected = round_money(m.contracted * rate) if rate is not None else None

    occupied = occupying_leases(today).values("unit_id")
    empty = (Unit.objects.filter(organization=org, property__in=sc.properties(include_archived=False))
             .exclude(manual_status=Unit.ManualStatus.INACTIVE).exclude(pk__in=occupied))
    asking = [u.list_rent for u in empty.only("list_rent") if u.list_rent]

    ending.sort(key=lambda x: (x.ends, x.lease.number))
    return Outlook(
        today=today, currency=org.currency, months=months, collection_rate=rate, ending=ending[:100],
        holding_over=holding, holding_over_rent=holding_rent, empty_units=empty.count(), empty_priced=len(asking),
        empty_rent=sum(asking, ZERO), show_tenants=show, generated_at=now or timezone.now())


def headline(report: Outlook) -> list[str]:
    """Plain sentences for the top of the page; only what the figures support."""
    out = []
    if report.months and report.months[0].contracted:
        peak = max(report.months, key=lambda m: m.ending)
        if peak.ending:
            out.append(_("%(n)s lease(s) end or are under notice in %(month)s, taking %(share)s of this month's "
                         "contracted rent with them unless renewed.")
                       % {"n": peak.ending_leases, "month": peak.label,
                          "share": f"{peak.ending / report.months[0].contracted * 100:.0f}%"})
    if report.holding_over:
        out.append(_("%(n)s lease(s) are past their end date and still running; they are counted as staying.")
                   % {"n": report.holding_over})
    return out
