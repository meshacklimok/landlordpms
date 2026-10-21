"""A property's water plan (D-157): read here, saved in water.py.

Billing imports this module, so it only touches the models: no services, no billing.

- METERED (the default, and what a property without a plan does): water meters bill from approved readings.
- FLAT: every let unit pays a fixed amount a month on its invoice; water meters are read only to follow the use.
- INCLUDED: water is in the rent. Nothing is billed; water meters are read only to follow the use.

Electricity meters are never affected by the water plan.
"""

from decimal import Decimal

from .models import Meter, WaterPlan, WaterPlanUnit

Method = WaterPlan.Method


def plan_for(prop) -> WaterPlan:
    """The property's plan, or an unsaved metered one."""
    try:
        return prop.water_plan
    except WaterPlan.DoesNotExist:
        return WaterPlan(organization_id=prop.organization_id, property=prop)


def methods(property_ids) -> dict[int, str]:
    """Property pk -> its water method, for many properties at once. Missing ones are metered."""
    ids = list(property_ids)
    found = dict(WaterPlan.objects.filter(property_id__in=ids).values_list("property_id", "method"))
    return {pk: found.get(pk, Method.METERED) for pk in ids}


def flat_amount_for(unit) -> Decimal | None:
    """What the unit pays for water a month on a FLAT plan (its own amount, else the plan's), or None when its
    property's water is not flat. Zero means the unit pays nothing."""
    plan = WaterPlan.objects.filter(property_id=unit.property_id).first()
    if plan is None or plan.method != Method.FLAT:
        return None
    own = WaterPlanUnit.objects.filter(plan=plan, unit_id=unit.pk).values_list("amount", flat=True).first()
    return own if own is not None else plan.flat_amount


def billed_from_readings(meter: Meter, method: str | None = None) -> bool:
    """Whether approving the meter's readings charges tenants: postpaid, tenants pay, and for a water meter, the
    property's water is metered. `method` saves a query when the caller already knows the property's."""
    if meter.is_prepaid or meter.landlord_pays:
        return False
    if meter.utility != Meter.Utility.WATER:
        return True
    if method is None:
        method = methods([meter.property_id])[meter.property_id]
    return method == Method.METERED


def approves_clean(meter: Meter) -> bool:
    """Whether the meter's unflagged readings are approved as they are saved (water meters only)."""
    if meter.utility != Meter.Utility.WATER:
        return False
    return WaterPlan.objects.filter(property_id=meter.property_id, approve_clean=True).exists()
