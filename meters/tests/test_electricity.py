"""Electricity sub-meters (D-070): same readings and approval as water, billed in kWh as "Metered electricity"."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from accounts.tests.test_isolation import login
from billing.models import ChargeType
from billing.tests.test_invoicing import bill
from meters import services
from meters.models import Meter, MeterReading

from .conftest import let, make_meter, read

pytestmark = pytest.mark.django_db
D = datetime.date
DEC31, JAN31, FEB, MAR1 = D(2025, 12, 31), D(2026, 1, 31), D(2026, 2, 1), D(2026, 3, 1)
Flag = MeterReading.Flag


def powered(owner, prop, *, value=1200, **kw):
    """A let unit with its own electricity sub-meter and an approved baseline. Returns (lease, meter)."""
    lease, unit = let(owner, prop)
    meter = make_meter(owner, prop, [unit], utility="ELECTRICITY", rate="25", label="A1 power", **kw)
    services.approve(owner, read(owner, meter, DEC31, value), today=DEC31)
    return lease, meter


def test_existing_meters_stay_water(owner, prop):
    _lease, unit = let(owner, prop)
    meter = make_meter(owner, prop, [unit])
    assert meter.utility == Meter.Utility.WATER and meter.unit_label == "m³"


def test_electricity_is_billed_in_kwh_under_its_own_charge_type(owner, prop):
    lease, meter = powered(owner, prop)
    assert meter.is_electricity and meter.unit_label == "kWh"
    [charge] = services.approve(owner, read(owner, meter, JAN31, 1320), today=JAN31)
    assert charge.amount == Decimal("3000.00")                   # 120 kWh × 25
    assert charge.description.startswith("Electricity A1 power: 1,200.000 → 1,320.000 kWh = 120.000 kWh × 25.00")
    line = bill(lease, FEB).lines.get(meter_charge=charge)
    assert line.charge_type.category == ChargeType.Category.METERED_ELECTRICITY
    assert line.charge_type.name == "Metered electricity" and line.charge_type.is_system


def test_water_and_electricity_share_one_invoice_on_separate_charge_types(owner, prop):
    lease, power = powered(owner, prop)
    water = make_meter(owner, prop, [lease.unit], label="A1 water")
    services.approve(owner, read(owner, water, DEC31, 100), today=DEC31)
    services.approve(owner, read(owner, power, JAN31, 1300), today=JAN31)
    services.approve(owner, read(owner, water, JAN31, 110), today=JAN31)
    invoice = bill(lease, FEB)
    categories = set(invoice.lines.filter(meter_charge__isnull=False).values_list("charge_type__category", flat=True))
    assert categories == {ChargeType.Category.METERED_WATER, ChargeType.Category.METERED_ELECTRICITY}


def test_metered_electricity_is_not_a_recurring_charge(owner, prop):
    assert ChargeType.Category.METERED_ELECTRICITY in ChargeType.NOT_RECURRING


def test_the_high_flag_needs_50_kwh_over_the_usual(owner, prop):
    _lease, meter = powered(owner, prop, value=0)
    services.approve(owner, read(owner, meter, JAN31, 10), today=JAN31)     # 10 kWh in 31 days
    # 40 kWh over 28 days is 4 times the usual but only about 31 kWh more: not flagged for electricity.
    assert read(owner, meter, D(2026, 2, 28), 50).flags == []


def test_the_high_flag_still_fires_on_a_real_jump(owner, prop):
    _lease, meter = powered(owner, prop, value=0)
    services.approve(owner, read(owner, meter, JAN31, 30), today=JAN31)
    assert Flag.HIGH in read(owner, meter, D(2026, 2, 28), 200).flags


def test_a_postpaid_electricity_meter_needs_a_price_per_kwh(owner, prop):
    _lease, unit = let(owner, prop)
    with pytest.raises(ValidationError, match="price per kWh"):
        make_meter(owner, prop, [unit], utility="ELECTRICITY", rate="0")
    with pytest.raises(ValidationError, match="water or electricity"):
        make_meter(owner, prop, [unit], utility="GAS")


def test_a_prepaid_token_meter_bills_nothing(owner, prop):
    _lease, unit = let(owner, prop)
    meter = make_meter(owner, prop, [unit], utility="ELECTRICITY", kind="PREPAID", rate="0")
    services.approve(owner, read(owner, meter, DEC31, 10), today=DEC31)
    assert services.approve(owner, read(owner, meter, JAN31, 90), today=JAN31) == []


def test_pages_show_kwh_and_filter_by_utility(client, owner, prop):
    login(client, owner)
    lease, power = powered(owner, prop)
    water = make_meter(owner, prop, [lease.unit], label="A1 water")
    all_meters = client.get(reverse("meters:list")).content.decode()
    assert "A1 power" in all_meters and "A1 water" in all_meters and "1200.000 kWh" in all_meters
    only_power = client.get(reverse("meters:list") + "?utility=ELECTRICITY").content.decode()
    assert "A1 power" in only_power and "A1 water" not in only_power
    page = client.get(reverse("meters:detail", args=[power.public_id])).content.decode()
    assert "1200.000 kWh" in page and "Reading (kWh)" in page and "Price per kWh" in page
    assert "Reading (m³)" in client.get(reverse("meters:detail", args=[water.public_id])).content.decode()
    form = {"utility": "ELECTRICITY", "label": "B power", "serial": "", "kind": "POSTPAID", "rate": "30",
            "minimum_charge": "", "split": "EQUAL", f"unit-{lease.unit.pk}": "on", f"weight-{lease.unit.pk}": "1"}
    assert client.post(reverse("meters:create", args=[prop.public_id]), form).status_code == 302
    assert Meter.objects.get(label="B power").utility == Meter.Utility.ELECTRICITY
