"""Who pays what for one reading (D-143).

The bill is the use × the rate, or the utility company's bill when one is typed in. A meter serving one room
bills that room's lease for the days it covered. A shared meter divides the bill:

- equally, by weight (e.g. bedrooms or people), or by each room's own meter over the same dates;
- among the rooms that were let (the default; an empty room's share is spread over the others, day by day), or
  over every room, with the landlord paying the empty rooms' share.

The minimum charge is per room per reading, and shrinks with the days a lease covered. Nothing here saves.
"""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Count, Prefetch
from django.utils.translation import gettext as _

from core.money import ZERO, round_money
from leases.models import LeaseTenant

from . import services
from .models import Meter, MeterReading, MeterUnit

Split = Meter.Split
Vacant = Meter.VacantShare
# A room's own meter read up to this many days after the shared one still counts for the same dates.
SLACK_DAYS = 3
UNITS = Decimal("0.001")


@dataclass
class LeasePart:
    lease: object
    tenant: str
    start: datetime.date
    end: datetime.date
    days: int
    amount: Decimal
    at_minimum: bool = False
    # The part of the meter's use this lease is charged for, before any minimum charge.
    units: Decimal = Decimal(0)


@dataclass
class RoomShare:
    unit: object
    weight: Decimal
    basis: Decimal | None
    parts: list[LeasePart] = field(default_factory=list)

    @property
    def amount(self) -> Decimal:
        return sum((p.amount for p in self.parts), ZERO)

    @property
    def vacant(self) -> bool:
        return not self.parts


@dataclass
class Division:
    """One way of dividing a reading's bill. `available` is False when the way cannot be used, with why."""

    split: str
    vacant_share: str
    total: Decimal
    rooms: list[RoomShare]
    shared: bool
    available: bool = True
    reason: str = ""

    @property
    def label(self) -> str:
        return str(Split(self.split).label)

    @property
    def charged(self) -> Decimal:
        return sum((r.amount for r in self.rooms), ZERO)

    @property
    def landlord(self) -> Decimal:
        """What the tenants' shares leave for the landlord (below zero when minimum charges add to the bill)."""
        return self.total - self.charged


def period(reading: MeterReading) -> tuple[datetime.date, datetime.date, int]:
    start, end = reading.previous.read_on + datetime.timedelta(days=1), reading.read_on
    return start, end, (end - start).days + 1


def own_use(meter: Meter, units: list, start: datetime.date, end: datetime.date) -> dict[int, Decimal | None]:
    """Unit pk -> what its own meter (same utility, serving only it) measured from `start` to `end`, or None when
    it has no own meter or no reading for those dates."""
    unit_ids = [u.pk for u in units]
    subs = (Meter.all_objects.filter(property_id=meter.property_id, utility=meter.utility)
            .exclude(pk=meter.pk).annotate(n=Count("served")).filter(n=1))
    unit_of = dict(MeterUnit.objects.filter(meter__in=subs, unit_id__in=unit_ids).values_list("meter_id", "unit_id"))
    slack = datetime.timedelta(days=SLACK_DAYS)
    readings = (MeterReading.objects.filter(meter_id__in=unit_of, status__in=MeterReading.LIVE, is_baseline=False,
                                            read_on__gt=start - datetime.timedelta(days=1) + slack,
                                            read_on__lte=end + slack)
                .select_related("previous"))
    used: dict[int, Decimal | None] = dict.fromkeys(unit_ids)
    for r in readings:
        if r.usage is not None and r.usage >= 0:
            used[unit_of[r.meter_id]] = (used[unit_of[r.meter_id]] or ZERO) + r.usage
    return used


def _leases(unit, start, end):
    links = Prefetch("lease_tenants", queryset=LeaseTenant.objects.select_related("tenant"))
    return list(services.covering_leases([unit], start, end).select_related("organization")
                .prefetch_related(links).order_by("start_date"))


def _tenant(lease) -> str:
    tenant = lease.primary_tenant or next((lt.tenant for lt in lease.lease_tenants.all()), None)
    return tenant.name if tenant else ""


def divide(reading: MeterReading, *, split: str | None = None, vacant_share: str | None = None,
           rate: Decimal | None = None, minimum: Decimal | None = None, bill_total: Decimal | None = None,
           served: list | None = None, leases: dict | None = None, use: dict | None = None) -> Division:
    """How the reading's bill falls on each room and lease. `served`, `leases` and `use` let `compare` load them
    once for every way."""
    meter = reading.meter
    split = split or meter.split
    vacant_share = vacant_share or meter.vacant_share
    rate = meter.rate if rate is None else rate
    minimum = meter.minimum_charge if minimum is None else minimum
    usage = reading.usage or ZERO
    total = bill_total if bill_total is not None else usage * rate
    start, end, days = period(reading)
    served = served if served is not None else list(meter.served.select_related("unit"))
    shared = len(served) > 1
    if not shared:
        split, vacant_share = Split.EQUAL, Vacant.LANDLORD
    leases = leases if leases is not None else {mu.unit_id: _leases(mu.unit, start, end) for mu in served}
    if split == Split.USAGE and use is None:
        use = own_use(meter, [mu.unit for mu in served], start, end)
    division = Division(split=split, vacant_share=vacant_share, total=round_money(total), rooms=[], shared=shared)

    basis = {}
    for mu in served:
        if split == Split.WEIGHTED:
            basis[mu.unit_id] = mu.weight
        elif split == Split.USAGE:
            basis[mu.unit_id] = use.get(mu.unit_id)
        else:
            basis[mu.unit_id] = Decimal(1)
    if split == Split.USAGE:
        missing = [mu.unit.code for mu in served if basis[mu.unit_id] is None]
        if missing:
            division.available = False
            division.reason = _("No reading of the own meter of %(rooms)s for these dates.") % {
                "rooms": ", ".join(missing)}
            division.rooms = [RoomShare(mu.unit, mu.weight, None) for mu in served]
            return division

    spans = {}
    for mu in served:
        for lease in leases[mu.unit_id]:
            s = max(start, lease.start_date)
            e = min(end, lease.effective_end) if lease.effective_end else end
            if e >= s:
                spans.setdefault(mu.unit_id, []).append((lease, s, e, (e - s).days + 1))
    if vacant_share == Vacant.OCCUPIED:
        whole = sum((basis[u] * d / days for u, rows in spans.items() for _l, _s, _e, d in rows), ZERO)
    else:
        whole = sum(basis.values(), ZERO)

    for mu in served:
        room = RoomShare(mu.unit, mu.weight, basis[mu.unit_id])
        for lease, s, e, d in spans.get(mu.unit_id, []):
            fraction = basis[mu.unit_id] * d / days / whole if whole > 0 else ZERO
            share = total * fraction
            floor = minimum * d / days if minimum > 0 else ZERO
            amount = round_money(max(share, floor))
            room.parts.append(LeasePart(lease, _tenant(lease), s, e, d, amount, at_minimum=floor > share,
                                        units=(usage * fraction).quantize(UNITS)))
        division.rooms.append(room)
    if whole <= 0 and split == Split.USAGE:
        division.reason = _("The rooms' own meters show no use for these dates.")
    return division


def compare(reading: MeterReading, *, vacant_share: str | None = None, bill_total: Decimal | None = None,
            rate: Decimal | None = None, minimum: Decimal | None = None) -> list[Division]:
    """Every way of dividing the reading's bill, side by side, for the landlord to pick one."""
    meter = reading.meter
    served = list(meter.served.select_related("unit"))
    start, end, _days = period(reading)
    leases = {mu.unit_id: _leases(mu.unit, start, end) for mu in served}
    use = own_use(meter, [mu.unit for mu in served], start, end)
    kw = {"vacant_share": vacant_share, "bill_total": bill_total, "rate": rate, "minimum": minimum,
          "served": served, "leases": leases, "use": use}
    ways = [Split.EQUAL]
    if len({mu.weight for mu in served}) > 1 or meter.split == Split.WEIGHTED:
        ways.append(Split.WEIGHTED)
    ways.append(Split.USAGE)
    return [divide(reading, split=way, **kw) for way in ways]


def describe(reading: MeterReading, division: Division, room: RoomShare, part: LeasePart, *, rate: Decimal,
             bill_total: Decimal | None) -> str:
    """The invoice line text for one lease's part."""
    meter = reading.meter
    values = {"label": meter.label, "prev": services.fmt_value(reading.previous.value),
              "now": services.fmt_value(reading.value), "use": services.fmt_value(reading.usage),
              "rate": f"{rate:,.2f}", "bill": f"{bill_total or 0:,.2f}"}
    if bill_total is not None:
        head = (_("Electricity %(label)s: bill %(bill)s for %(prev)s → %(now)s kWh (%(use)s kWh)")
                if meter.is_electricity else _("Water %(label)s: bill %(bill)s for %(prev)s → %(now)s m³ (%(use)s m³)"))
    else:
        head = (_("Electricity %(label)s: %(prev)s → %(now)s kWh = %(use)s kWh × %(rate)s") if meter.is_electricity
                else _("Water %(label)s: %(prev)s → %(now)s m³ = %(use)s m³ × %(rate)s"))
    parts = [head % values]
    if division.shared:
        if division.split == Split.USAGE:
            used = sum((r.basis or ZERO for r in division.rooms), ZERO)
            parts.append(_("unit %(code)s used %(w)s of %(total)s %(unit)s on its own meter") % {
                "code": room.unit.code, "w": services.fmt_value(room.basis), "total": services.fmt_value(used),
                "unit": meter.unit_label})
        else:
            rooms = (division.rooms if division.vacant_share == Vacant.LANDLORD
                     else [r for r in division.rooms if not r.vacant])
            parts.append(_("unit %(code)s share %(w)s of %(total)s") % {
                "code": room.unit.code, "w": services.fmt_weight(room.basis),
                "total": services.fmt_weight(sum((r.basis for r in rooms), ZERO))})
            if division.vacant_share == Vacant.OCCUPIED and len(rooms) < len(division.rooms):
                parts.append(_("divided among the rooms let"))
    if part.at_minimum:
        parts.append(_("minimum charge"))
    _s, _e, days = period(reading)
    if part.days != days:
        parts.append(_("%(n)s of %(d)s days") % {"n": part.days, "d": days})
    return (" · ".join(parts) + f" ({part.start:%d %b} – {part.end:%d %b %Y})")[:200]
