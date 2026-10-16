"""Water management (D-157): the property's water plan, and the month's readings unit by unit.

However a reading comes in (the sheet, a QR code at the meter, an Excel sheet, a photo of the notebook), it is
recorded by services.record_reading and approved by services.approve: one set of checks, one way to bill. This
module only reports on them and saves the plan.
"""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership
from billing import invoicing
from properties.models import Property, Unit

from . import plans, services, sheet
from .models import Meter, MeterReading, WaterPlan, WaterPlanUnit

Method = WaterPlan.Method
Status = MeterReading.Status


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


@transaction.atomic
def save_plan(actor: Membership, prop: Property, *, method: str, flat_amount, unit_amounts: dict[int, str],
              approve_clean: bool, request=None) -> WaterPlan:
    """Saves how the property charges for water. `unit_amounts`: unit pk -> its own flat amount, "" for none."""
    services._check(actor, prop, "meters.manage")
    if method not in Method.values:
        raise ValidationError({"method": _("Choose how water is charged.")})
    flat = services._clean_amount(flat_amount, "flat_amount")
    units = {u.pk: u for u in Unit.objects.filter(property=prop)}
    own: dict[int, Decimal] = {}
    for pk, text in unit_amounts.items():
        if pk not in units or str(text or "").strip() == "":
            continue
        own[pk] = services._clean_amount(text, f"unit-{pk}")
    if method == Method.FLAT and flat <= 0 and not any(a > 0 for a in own.values()):
        raise ValidationError({"flat_amount": _("Enter what each unit pays for water a month.")})
    plan = WaterPlan.objects.select_for_update().filter(property=prop).first()
    before = ({"method": plan.method, "flat_amount": str(plan.flat_amount), "approve_clean": plan.approve_clean,
               "units": _describe(plan)} if plan else
              {"method": Method.METERED, "flat_amount": "0.00", "approve_clean": False, "units": ""})
    if plan is None:
        plan = WaterPlan(organization=prop.organization, property=prop)
    plan.method, plan.flat_amount, plan.approve_clean, plan.updated_by = method, flat, approve_clean, actor.user
    plan.save()
    WaterPlanUnit.objects.filter(plan=plan).exclude(unit_id__in=own).delete()
    for pk, amount in own.items():
        WaterPlanUnit.objects.update_or_create(plan=plan, unit_id=pk, defaults={"amount": amount})
    after = {"method": plan.method, "flat_amount": str(plan.flat_amount), "approve_clean": plan.approve_clean,
             "units": _describe(plan)}
    changes = {k: [before[k], after[k]] for k in after if before[k] != after[k]}
    if changes:
        services._audit("meters.water_plan", actor, prop, request, changes)
    return plan


def _describe(plan: WaterPlan) -> str:
    return ", ".join(f"{pu.unit.code}={pu.amount}" for pu in plan.units.select_related("unit").order_by("unit__code"))


def meters_ignored(prop: Property, method: str) -> list[Meter]:
    """Water meters set to bill tenants that the plan stops billing: shown when the plan is flat or included."""
    if method == Method.METERED:
        return []
    return list(Meter.objects.filter(property=prop, utility=Meter.Utility.WATER, kind=Meter.Kind.POSTPAID,
                                     payer=Meter.Payer.TENANTS).order_by("label"))


# ---------------------------------------------------------------------------
# The month, unit by unit
# ---------------------------------------------------------------------------

# What a unit's row says, in the order counted on the page.
APPROVED, WAITING, FLAGGED, MISSING = "approved", "waiting", "flagged", "missing"
VACANT, FLAT, FREE, INCLUDED = "vacant", "flat", "free", "included"
LANDLORD, PREPAID, NO_METER = "landlord", "prepaid", "no_meter"
NEEDS_READING = (APPROVED, WAITING, FLAGGED, MISSING)


@dataclass
class UnitRow:
    unit: Unit
    tenant: str | None
    status: str
    meter: Meter | None = None
    shared: bool = False
    flat: Decimal | None = None
    reading: MeterReading | None = None  # this month's, the latest if more than one
    last: MeterReading | None = None  # the one before this month

    @property
    def usage(self) -> Decimal | None:
        return self.reading.usage if self.reading is not None else None


@dataclass
class Session:
    """One property's water for one month."""

    property: Property
    month: datetime.date
    plan: WaterPlan
    rows: list[UnitRow] = field(default_factory=list)
    to_read: list[Meter] = field(default_factory=list)  # billed meters serving a let unit, not read this month

    def count(self, *statuses) -> int:
        return sum(1 for r in self.rows if r.status in statuses)

    @property
    def needed(self) -> int:
        return self.count(*NEEDS_READING)

    @property
    def recorded(self) -> int:
        return self.count(APPROVED, WAITING, FLAGGED)

    @property
    def percent(self) -> int:
        return round(100 * self.recorded / self.needed) if self.needed else 100

    @property
    def counts(self) -> dict[str, int]:
        return {s: self.count(s) for s in (APPROVED, WAITING, FLAGGED, MISSING, VACANT, FLAT, FREE, INCLUDED,
                                           LANDLORD, PREPAID, NO_METER)}

    @property
    def complete(self) -> bool:
        return self.needed > 0 and self.recorded == self.needed

    @property
    def not_billed(self) -> int:
        """Units on the property that need no reading this month: empty, landlord pays, prepaid or no meter."""
        return self.count(VACANT, LANDLORD, PREPAID, NO_METER)


def month_bounds(month: datetime.date) -> tuple[datetime.date, datetime.date]:
    first = invoicing.month_start(month)
    return first, invoicing.month_end(first)


def _latest(readings) -> dict[int, MeterReading]:
    out: dict[int, MeterReading] = {}
    for r in readings:  # newest first
        out.setdefault(r.meter_id, r)
    return out


def session(prop: Property, month: datetime.date, *, today: datetime.date | None = None) -> Session:
    """Every unit of the property with how its water is charged this month and, if metered, its reading."""
    today = today or timezone.localdate()
    first, last = month_bounds(month)
    plan = plans.plan_for(prop)
    result = Session(property=prop, month=first, plan=plan)
    units = sorted(Unit.objects.filter(property=prop), key=lambda u: sheet.natural_key(u.code))
    if not units:
        return result
    let = set(services.covering_leases(units, first, last).values_list("unit_id", flat=True))
    names = sheet.tenants_on(units, min(max(today, first), last))
    own = {pu.unit_id: pu.amount for pu in plan.units.all()} if plan.pk else {}
    meters = list(Meter.objects.filter(property=prop, utility=Meter.Utility.WATER).prefetch_related("served"))
    live = MeterReading.objects.filter(meter__in=meters, status__in=MeterReading.LIVE).select_related("previous")
    this_month = _latest(live.filter(read_on__range=(first, last)).order_by("-read_on", "-pk"))
    before = _latest(live.filter(read_on__lt=first).order_by("meter_id", "-read_on", "-pk").distinct("meter_id"))
    serving: dict[int, list[Meter]] = {}
    for meter in meters:
        meter.n_served = len(meter.served.all())
        for mu in meter.served.all():
            serving.setdefault(mu.unit_id, []).append(meter)
    needed: dict[int, Meter] = {}
    for unit in units:
        found = serving.get(unit.pk, [])
        billed = sorted((m for m in found if plans.billed_from_readings(m, Method.METERED)),
                        key=lambda m: (m.n_served > 1, m.label))
        meter = billed[0] if billed else (found[0] if found else None)
        row = UnitRow(unit=unit, tenant=names.get(unit.pk), status=NO_METER, meter=meter,
                      shared=bool(meter and meter.n_served > 1))
        if meter is not None:
            row.reading, row.last = this_month.get(meter.pk), before.get(meter.pk)
        if plan.method == Method.FLAT:
            row.flat = own.get(unit.pk, plan.flat_amount)
            row.status = FLAT if row.flat > 0 else FREE
        elif plan.method == Method.INCLUDED:
            row.status = INCLUDED
        elif not billed:
            row.status = (PREPAID if any(m.is_prepaid for m in found) else LANDLORD if found else NO_METER)
        elif unit.pk not in let:
            row.status = VACANT
        elif row.reading is None:
            row.status = MISSING
            needed[meter.pk] = meter
        elif row.reading.status == Status.APPROVED:
            row.status = APPROVED
        else:
            row.status = FLAGGED if row.reading.flags else WAITING
        result.rows.append(row)
    result.to_read = sorted(needed.values(), key=lambda m: sheet.natural_key(m.label))
    return result


def overview(membership: Membership, month: datetime.date, *, today=None) -> list[Session]:
    """Each property the member can see that has units, with its month."""
    props = (services.visible_properties(membership, Property.objects.all()).filter(units__isnull=False)
             .distinct().select_related("water_plan").order_by("name"))
    return [session(p, month, today=today) for p in props]
