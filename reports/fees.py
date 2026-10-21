"""The agency's own income: management fees earned over the last 12 finished months (D-127).

The fee is worked out exactly as on the owner statement (statements.fee_for): a percentage of rent
collected in the month, plus any fixed monthly fee for the months a property is managed. Live
figures, drawn on request; only the properties the member can see are counted.
"""

import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import accessible_property_ids, require, visible_properties
from core.money import ZERO
from payments.models import Payment
from properties.models import Property

from .income import _invoice_shares, _split
from .metrics import add_months, month_end
from .statements import default_month, fee_for

MONTHS = 12


@dataclass
class Row:
    """A property, owner or branch: rent collected and fees, month by month (oldest first)."""

    label: str
    rent: list[Decimal] = field(default_factory=lambda: [ZERO] * MONTHS)
    fees: list[Decimal] = field(default_factory=lambda: [ZERO] * MONTHS)
    note: str = ""
    obj: object = None
    share: Decimal | None = None  # part of all fees, as a percentage

    @property
    def rent_total(self) -> Decimal:
        return sum(self.rent, ZERO)

    @property
    def fee_total(self) -> Decimal:
        return sum(self.fees, ZERO)

    @property
    def fee_rate(self) -> Decimal | None:
        """Fees as a percentage of rent collected."""
        return (self.fee_total * 100 / self.rent_total).quantize(Decimal("0.1")) if self.rent_total else None

    def add(self, other: "Row") -> None:
        for i in range(MONTHS):
            self.rent[i] += other.rent[i]
            self.fees[i] += other.fees[i]


@dataclass
class FeeReport:
    months: list[datetime.date]
    total: Row
    by_property: list[Row]
    by_owner: list[Row]
    by_branch: list[Row]
    scoped: bool
    no_fee: list[Property]

    @property
    def month_rows(self) -> list[tuple[datetime.date, Decimal, Decimal]]:
        """Newest first, for the month table."""
        return [(m, self.total.rent[i], self.total.fees[i]) for i, m in reversed(list(enumerate(self.months)))]



def report(membership: Membership, today: datetime.date | None = None) -> FeeReport:
    require(membership, "reports.view_financial")
    org = membership.organization
    last = default_month(today or timezone.localdate())
    months = [add_months(last, i - MONTHS + 1) for i in range(MONTHS)]
    index = {m: i for i, m in enumerate(months)}
    props = list(visible_properties(membership, Property.all_objects.all()).select_related("owner", "branch")
                 .order_by("name"))

    # Rent collected per property per month, with money not yet applied counted as rent, as on statements.
    rent = defaultdict(lambda: [ZERO] * MONTHS)
    payments = list(Payment.objects.filter(organization=org, status=Payment.Status.CONFIRMED,
                                           paid_at__gte=months[0], paid_at__lte=month_end(last),
                                           lease__unit__property__in=props)
                    .select_related("lease__unit").prefetch_related("allocations"))
    shares = _invoice_shares(payments)
    for p in payments:
        part = _split(p, shares)
        rent[p.lease.unit.property_id][index[p.paid_at.replace(day=1)]] += part.rent + part.unapplied

    by_property = []
    for prop in props:
        row = Row(label=prop.name, obj=prop, rent=rent[prop.pk])
        row.fees = [fee_for(prop, row.rent[i], m) for i, m in enumerate(months)]
        if prop.is_archived:
            row.note = _("archived")
        if row.rent_total or row.fee_total:
            by_property.append(row)

    total = Row(label=_("All properties"))
    owners: dict[int | None, Row] = {}
    branches: dict[int | None, Row] = {}
    for row in by_property:
        prop = row.obj
        total.add(row)
        owner = owners.setdefault(prop.owner_id, Row(
            label=prop.owner.name if prop.owner else _("No owner set"), obj=prop.owner))
        owner.add(row)
        branch = branches.setdefault(prop.branch_id, Row(
            label=prop.branch.name if prop.branch else _("No branch"), obj=prop.branch))
        branch.add(row)

    def ranked(rows):
        rows = sorted(rows, key=lambda r: (-r.fee_total, r.label.lower()))
        for r in rows:
            r.share = (r.fee_total * 100 / total.fee_total).quantize(Decimal("0.1")) if total.fee_total else None
        return rows

    return FeeReport(
        months=months, total=total, by_property=ranked(by_property), by_owner=ranked(owners.values()),
        by_branch=ranked(branches.values()) if any(k is not None for k in branches) else [],
        scoped=accessible_property_ids(membership) is not None,
        no_fee=[p for p in props if not p.is_archived and not p.management_fee_percent
                and not p.management_fee_fixed],
    )


def csv_rows(rep: FeeReport) -> list[list]:
    """One row per property and month: what an accountant needs to check the totals."""
    rows = [[_("Property"), _("Branch"), _("Owner"), _("Month"), _("Rent collected"), _("Fee")]]
    for row in sorted(rep.by_property, key=lambda r: r.label.lower()):
        prop = row.obj
        for i, month in enumerate(rep.months):
            if row.rent[i] or row.fees[i]:
                rows.append([prop.name, prop.branch.name if prop.branch else "", prop.owner.name if prop.owner else "",
                             f"{month:%Y-%m}", row.rent[i], row.fees[i]])
    return rows
