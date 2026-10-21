"""Daily arrears snapshots and arrears over time (D-077).

The daily job writes, for every property, what is overdue at the end of the day. The dashboard
reads the last snapshot of each month. A backfill rebuilds past days from the ledger as it stands
today; those rows are marked `rebuilt` and never replace a row the job wrote on the day.
"""

import datetime
from collections import defaultdict
from dataclasses import dataclass

from django.db.models import Count, Q, Sum
from django.utils import timezone

from accounts.models import Organization
from billing.selectors import organization_arrears
from core.money import ZERO
from properties.models import Property

from . import metrics
from .models import DailySnapshot

BUCKETS = ("d1_30", "d31_60", "d61_90", "d90")


def take(org: Organization, day: datetime.date, *, rebuilt: bool = False) -> int:
    """Writes one row per property for `day`: live properties, and archived ones that still have arrears.

    The job's own rows are replaced when it runs again the same day. Rebuilt rows only fill gaps.
    """
    totals = defaultdict(lambda: {**{key: ZERO for key in BUCKETS}, "leases": 0})
    for row in organization_arrears(org, day, as_recorded_now=rebuilt):
        t = totals[row.lease.unit.property_id]
        for key in BUCKETS:
            t[key] += row.buckets[key]
        t["leases"] += 1
    property_ids = set(Property.objects.for_org(org).values_list("pk", flat=True)) | set(totals)
    rows = [DailySnapshot(organization=org, property_id=pk, day=day, rebuilt=rebuilt, **totals[pk])
            for pk in sorted(property_ids)]
    if rebuilt:
        DailySnapshot.objects.bulk_create(rows, ignore_conflicts=True)
    else:
        DailySnapshot.objects.bulk_create(rows, update_conflicts=True, unique_fields=["property", "day"],
                                          update_fields=[*BUCKETS, "leases", "rebuilt", "taken_at"])
    return len(rows)


def take_all(day: datetime.date | None = None) -> dict:
    """The daily job: every organization that is not frozen."""
    day = day or timezone.localdate()
    counts = {"organizations": 0, "rows": 0}
    for org in Organization.objects.exclude(status=Organization.Status.FROZEN).order_by("pk"):
        counts["rows"] += take(org, day)
        counts["organizations"] += 1
    return counts


def backfill(org: Organization, days: int, *, today: datetime.date | None = None) -> int:
    """Rebuilds the `days` days before today from the ledger as recorded now."""
    today = today or timezone.localdate()
    return sum(take(org, today - datetime.timedelta(days=n), rebuilt=True) for n in range(days, 0, -1))


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


@dataclass
class ArrearsPoint:
    month: datetime.date
    day: datetime.date | None = None  # the snapshot used; None when the month has none
    d1_30: object = ZERO
    d31_60: object = ZERO
    d61_90: object = ZERO
    d90: object = ZERO
    leases: int = 0
    rebuilt: bool = False

    @property
    def label(self) -> str:
        return self.month.strftime("%b %Y")

    @property
    def total(self):
        return None if self.day is None else self.d1_30 + self.d31_60 + self.d61_90 + self.d90


def history(sc: metrics.Scope, last: datetime.date, count: int = metrics.TREND_MONTHS) -> list[ArrearsPoint]:
    """Arrears on the last snapshot day of each month, for the properties in scope."""
    first = metrics.add_months(metrics.month_start(last), -(count - 1))
    end = metrics.month_end(metrics.month_start(last))
    rows = (DailySnapshot.objects.filter(organization=sc.organization, property__in=sc.properties(),
                                         day__range=(first, end))
            .values("day").order_by("day")
            .annotate(**{key: Sum(key) for key in BUCKETS}, n_leases=Sum("leases"),
                      n_rebuilt=Count("pk", filter=Q(rebuilt=True))))
    latest = {}
    for r in rows:  # ordered by day, so the last one in each month wins
        latest[metrics.month_start(r["day"])] = r
    points = []
    for i in range(count):
        month = metrics.add_months(first, i)
        r = latest.get(month)
        if r is None:
            points.append(ArrearsPoint(month))
        else:
            points.append(ArrearsPoint(month, r["day"], *(r[key] for key in BUCKETS), leases=r["n_leases"],
                                       rebuilt=bool(r["n_rebuilt"])))
    return points
