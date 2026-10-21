"""Property health score (D-194): one number out of 100 per property, with every part shown.

Five parts, each worked out from records the landlord already sees elsewhere. The weights are ours, not an
industry standard, and are shown on the page:

  Rent collection  30%  rent collected ÷ rent billed in the period
  Occupancy        25%  occupied ÷ rentable units on the period's last day (or today)
  Arrears          20%  overdue balance against one month of rent billed; two months overdue scores 0
  Maintenance      15%  open repairs that are not past their fix-by time
  Expense control  10%  expenses as a share of income: 30% or less scores 100, 80% or more scores 0

A part with nothing to measure (no rent billed, no income, no repairs in the last 90 days) is left out and the
others are re-weighted, so missing data never lowers a score. If any part is left out the score is marked
provisional; if parts worth under half the weight are left out it is not given at all.
"""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Count, Q
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from accounts.permissions import can
from billing import selectors as billing_selectors
from core.money import ZERO
from maintenance.models import MaintenanceRequest

from . import metrics

WEIGHTS = (("collection", 30), ("occupancy", 25), ("arrears", 20), ("maintenance", 15), ("expenses", 10))
LABELS = {"collection": _("Rent collection"), "occupancy": _("Occupancy"), "arrears": _("Arrears"),
          "maintenance": _("Maintenance"), "expenses": _("Expense control")}
MIN_WEIGHT = 50          # of 100: below this much measurable, no score
RECENT_DAYS = 90         # repairs reported this recently count as "there is maintenance to judge"
EXPENSE_GOOD, EXPENSE_BAD = Decimal("0.30"), Decimal("0.80")


@dataclass
class Part:
    key: str
    label: object
    weight: int
    points: int | None = None        # 0..100, None when there was nothing to measure
    detail: str = ""
    url: str = ""                    # the records behind the number, filtered to the property; "" if not allowed

    @property
    def band(self) -> str:
        if self.points is None:
            return "none"
        return "strong" if self.points >= 80 else "good" if self.points >= 60 else "attention"


@dataclass
class Health:
    parts: list[Part] = field(default_factory=list)

    @property
    def measured_weight(self) -> int:
        return sum(p.weight for p in self.parts if p.points is not None)

    @property
    def score(self) -> int | None:
        if self.measured_weight < MIN_WEIGHT:
            return None
        return round(sum(p.points * p.weight for p in self.parts if p.points is not None) / self.measured_weight)

    @property
    def provisional(self) -> bool:
        return self.score is not None and self.measured_weight < 100

    @property
    def band(self) -> str:
        s = self.score
        return "none" if s is None else "strong" if s >= 80 else "good" if s >= 60 else "attention"


def _pct(fraction: Decimal) -> int:
    return round(max(Decimal(0), min(Decimal(1), fraction)) * 100)


def points(*, collection, occupancy, overdue: Decimal | None, billed: Decimal | None, open_repairs: int,
           overdue_repairs: int, recent_repairs: int, income: Decimal, expenses: Decimal) -> Health:
    """The five parts from plain numbers (kept apart from the queries so the rules can be tested)."""
    parts = {k: Part(k, LABELS[k], w) for k, w in WEIGHTS}
    if collection is not None:
        parts["collection"].points = _pct(collection)
    if occupancy is not None:
        parts["occupancy"].points = _pct(occupancy)
    if overdue is not None and billed:
        parts["arrears"].points = _pct(1 - (overdue / billed) / 2)
    if open_repairs:
        parts["maintenance"].points = _pct(Decimal(open_repairs - overdue_repairs) / open_repairs)
    elif recent_repairs:
        parts["maintenance"].points = 100
    if income:
        ratio = expenses / income
        parts["expenses"].points = _pct((EXPENSE_BAD - ratio) / (EXPENSE_BAD - EXPENSE_GOOD))
    return Health(list(parts.values()))


def _repairs(membership, property_ids, now) -> dict[int, tuple[int, int, int]]:
    """Per property: open repairs, open repairs past their fix-by time, repairs reported in the last 90 days."""
    if not can(membership, "maintenance.view"):
        return {}
    out = {}
    qs = MaintenanceRequest.objects.filter(organization=membership.organization, property_id__in=property_ids)
    for r in qs.values("property_id").annotate(
            open=Count("pk", filter=Q(status__in=MaintenanceRequest.OPEN)),
            late=Count("pk", filter=Q(status__in=MaintenanceRequest.OPEN, due_at__lt=now)),
            recent=Count("pk", filter=Q(created_at__gte=now - datetime.timedelta(days=RECENT_DAYS)))):
        out[r["property_id"]] = (r["open"], r["late"], r["recent"])
    return out


# The page that lists the records behind each part, and what the member needs to open it.
LINKS = {"arrears": ("billing:call_list", "invoices.view"), "occupancy": ("properties:units", "units.view"),
         "maintenance": ("maintenance:list", "maintenance.view"), "expenses": ("expenses:list", "expenses.view")}


def _link(membership, key: str, prop) -> str:
    name, capability = LINKS.get(key, ("", ""))
    if not name or not can(membership, capability):
        return ""
    return f"{reverse(name)}?property={prop.public_id}"


def attach(membership, sc, rows, end: datetime.date, today: datetime.date, now) -> None:
    """Set `.health` on each scorecard row. Leaves it None for parts the member may not see."""
    if not rows:
        return
    ids = [r.property.pk for r in rows]
    overdue: dict[int, Decimal] | None = None
    if can(membership, "invoices.view"):
        overdue = {}
        for a in billing_selectors.arrears(membership, today, with_tenants=False):
            pk = a.lease.unit.property_id
            overdue[pk] = overdue.get(pk, ZERO) + a.balance
    billed = {}
    for c in metrics.collection_by_property(sc, metrics.month_start(min(end, today))):
        billed[c.key] = c.expected
    repairs = _repairs(membership, ids, now)
    seen_repairs = can(membership, "maintenance.view")
    for r in rows:
        o, late, recent = repairs.get(r.property.pk, (0, 0, 0))
        r.health = points(
            collection=r.collection, occupancy=r.occupancy,
            overdue=None if overdue is None else overdue.get(r.property.pk, ZERO),
            billed=billed.get(r.property.public_id),
            open_repairs=o if seen_repairs else 0, overdue_repairs=late, recent_repairs=recent if seen_repairs else 0,
            income=r.income, expenses=r.expenses)
        for part in r.health.parts:
            part.url = _link(membership, part.key, r.property)
