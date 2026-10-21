"""Hostel report (D-192): beds, the term's collection, parents who owe, the waitlist and bookings ending.

Nothing new is stored. A hostel is a property whose rooms hold beds (properties.hostel); the figures come from
the same units, invoices and prospects the rest of the app uses.
"""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Q

from accounts.permissions import visible_properties
from billing.models import Invoice
from core.money import ZERO
from leases.models import Lease
from leases.rollover import candidates
from leases.services import with_occupancy
from properties.models import Property, Unit
from prospects.services import waitlist

ENDING_WITHIN = 60
OWING_LIMIT = 50


@dataclass
class Owing:
    student: str
    guardian: str
    guardian_phone: str
    unit: str
    invoice: str
    due: datetime.date
    owed: Decimal
    days_late: int


@dataclass
class HostelRow:
    prop: Property
    rooms: int = 0
    beds: int = 0
    taken: int = 0
    by_gender: dict = field(default_factory=dict)  # "F"/"M"/"" -> [beds, taken, students waiting]
    billed: Decimal = ZERO
    paid: Decimal = ZERO
    owing: list = field(default_factory=list)
    owed: Decimal = ZERO
    waiting_total: int = 0
    ready: int = 0
    ending: list = field(default_factory=list)  # (student, unit, end date, drafted)

    @property
    def free(self) -> int:
        return self.beds - self.taken

    @property
    def occupancy(self) -> Decimal | None:
        return Decimal(self.taken) / self.beds if self.beds else None

    @property
    def rate(self) -> Decimal | None:
        return self.paid / self.billed if self.billed else None


def hostel_report(membership, property: Property | None = None, today: datetime.date | None = None) -> list[HostelRow]:
    from properties import hostel

    today = today or datetime.date.today()
    props = visible_properties(membership, Property.objects.filter(
        Q(category=Property.Category.HOSTEL) | Q(rooms__isnull=False)).distinct()).order_by("name")
    if property is not None:
        props = props.filter(pk=property.pk)
    rows = []
    for prop in props:
        row = HostelRow(prop=prop, rooms=prop.rooms.filter(archived_at__isnull=True).count())
        units = with_occupancy(Unit.objects.filter(property=prop, room__isnull=False, room__archived_at__isnull=True,
                                                   manual_status=Unit.ManualStatus.NORMAL), today)
        for gender, occupied in units.values_list("room__gender", "is_occupied"):
            cell = row.by_gender.setdefault(gender, [0, 0, 0])
            cell[0] += 1
            cell[1] += 1 if occupied else 0
            row.beds += 1
            row.taken += 1 if occupied else 0
        # The term: invoices of active bookings that run over today.
        invoices = (Invoice.objects.filter(lease__unit__property=prop, lease__status=Lease.Status.ACTIVE,
                                           status__in=[*Invoice.OPEN, Invoice.Status.PAID],
                                           period_start__lte=today, period_end__gte=today)
                    .select_related("lease__unit").prefetch_related("lease__lease_tenants__tenant"))
        for inv in invoices:
            row.billed += inv.total
            row.paid += inv.amount_paid
        late = (Invoice.objects.filter(lease__unit__property=prop, status__in=Invoice.OPEN, overdue_after__lt=today)
                .select_related("lease__unit").prefetch_related("lease__lease_tenants__tenant")
                .order_by("overdue_after"))
        for inv in late:
            owed = inv.total - inv.amount_paid
            row.owed += owed
            if len(row.owing) < OWING_LIMIT:
                student = inv.lease.primary_tenant
                row.owing.append(Owing(student.name if student else "", student.emergency_contact_name if student else "",
                                       student.emergency_contact_phone if student else "", inv.lease.unit.code,
                                       inv.number, inv.due_date, owed, (today - inv.overdue_after).days))
        for p in waitlist(prop):
            row.by_gender.setdefault(p.gender, [0, 0, 0])[2] += 1
            row.waiting_total += 1
            whole = {"Bed": False, "Whole room": True}.get(p.wants)
            if p.gender and hostel.free_beds(prop, p.gender, whole, today).exists():
                row.ready += 1
        horizon = today + datetime.timedelta(days=ENDING_WITHIN)
        for c in candidates(prop, today):
            end = c["lease"].end_date
            if today <= end <= horizon:
                row.ending.append((c["student"].name if c["student"] else "", c["lease"].unit.code, end,
                                   bool(c["has_draft"])))
        rows.append(row)
    return rows
