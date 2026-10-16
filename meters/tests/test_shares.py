"""Shared meters (D-143): divided among the rooms let, equally, by weight or by each room's own meter, the
utility bill divided instead of use × rate, the landlord paying, and the page that compares the ways."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from accounts.tests.factories import add_member, make_org
from accounts.tests.test_isolation import login
from meters import services, split
from meters.models import MeterReading
from properties import services as property_services

from .conftest import let, make_meter, read

pytestmark = pytest.mark.django_db
D = datetime.date
DEC31, JAN30, JAN31 = D(2025, 12, 31), D(2026, 1, 30), D(2026, 1, 31)


@pytest.fixture
def tank(owner, prop):
    """A1 let all month, A2 let from 16 Jan, B1 empty, all on one tank. 30 m³ over 1–30 Jan at 100 = 3,000."""
    l1, a1 = let(owner, prop, code="A1")
    l2, a2 = let(owner, prop, code="A2", start=D(2026, 1, 16))
    b1 = property_services.create_unit(owner, prop, code="B1")
    meter = make_meter(owner, prop, [a1, a2, b1], label="Tank")
    services.approve(owner, read(owner, meter, DEC31, 0), today=DEC31)
    return {"meter": meter, "l1": l1, "l2": l2, "reading": read(owner, meter, JAN30, 30)}


def amounts(charges):
    return {c.lease_id: c.amount for c in charges}


def test_the_bill_is_divided_among_the_rooms_let_day_by_day(owner, tank):
    charges = services.approve(owner, tank["reading"], today=JAN31)
    # A1 counts 1, A2 half (15 of 30 days), B1 nothing: 3,000 over 1.5.
    assert amounts(charges) == {tank["l1"].pk: Decimal("2000.00"), tank["l2"].pk: Decimal("1000.00")}
    assert "share 1 of 2" in charges[0].description and "divided among the rooms let" in charges[0].description
    reading = MeterReading.objects.get(pk=tank["reading"].pk)
    assert (reading.split, reading.vacant_share, reading.payer) == ("EQUAL", "OCCUPIED", "TENANTS")


def test_the_landlord_can_pay_the_empty_rooms_share(owner, tank):
    charges = services.approve(owner, tank["reading"], vacant_share="LANDLORD", today=JAN31)
    assert amounts(charges) == {tank["l1"].pk: Decimal("1000.00"), tank["l2"].pk: Decimal("500.00")}
    assert "share 1 of 3" in charges[0].description


def test_the_utility_bill_is_divided_instead_of_use_times_rate(owner, tank):
    charges = services.approve(owner, tank["reading"], bill_total="4,500", today=JAN31)
    assert sum(c.amount for c in charges) == Decimal("4500.00")
    assert "bill 4,500.00 for 0.000 → 30.000 m³" in charges[0].description
    reading = MeterReading.objects.get(pk=tank["reading"].pk)
    assert reading.bill_total == Decimal("4500.00")
    services.undo_approval(owner, reading)
    reading.refresh_from_db()
    assert reading.bill_total is None and reading.split == "" and reading.payer == ""


@pytest.mark.parametrize("bad, field", [({"bill_total": "-5"}, "cannot be negative"),
                                         ({"bill_total": "0"}, "amount on the bill"),
                                         ({"split": "RANDOM"}, "how the meter is divided"),
                                         ({"vacant_share": "NOBODY"}, "empty rooms")])
def test_approval_checks_the_choices(owner, tank, bad, field):
    with pytest.raises(ValidationError, match=field):
        services.approve(owner, tank["reading"], today=JAN31, **bad)
    assert MeterReading.objects.get(pk=tank["reading"].pk).status == "SUBMITTED"


def test_a_meter_the_landlord_pays_bills_nobody(owner, prop):
    _lease, unit = let(owner, prop)
    meter = make_meter(owner, prop, [unit], payer="LANDLORD")
    services.approve(owner, read(owner, meter, DEC31, 0), today=DEC31)
    reading = read(owner, meter, JAN31, 20)
    assert services.estimate(reading) == 0
    assert services.approve(owner, reading, today=JAN31) == []
    assert MeterReading.objects.get(pk=reading.pk).payer == "LANDLORD"


def own_meters(owner, prop, readings):
    """A1 and A2 let, each with its own meter the landlord pays (they only divide the shared one), and a shared
    meter split by them: 50 m³ at 100 = 5,000. `readings` is {code: (day, value)} for the own meters."""
    leases, units = {}, {}
    for code in ("A1", "A2"):
        leases[code], units[code] = let(owner, prop, code=code)
        own = make_meter(owner, prop, [units[code]], label=f"Own {code}", payer="LANDLORD")
        services.approve(owner, read(owner, own, DEC31, 0), today=DEC31)
        if code in readings:
            read(owner, own, *readings[code])
    shared = make_meter(owner, prop, [units["A1"], units["A2"]], label="Main", split="USAGE")
    services.approve(owner, read(owner, shared, DEC31, 0), today=DEC31)
    return leases, read(owner, shared, JAN31, 50)


def test_by_each_rooms_own_meter_even_one_read_a_day_late(owner, prop):
    leases, reading = own_meters(owner, prop, {"A1": (JAN31, 10), "A2": (D(2026, 2, 2), 30)})
    charges = services.approve(owner, reading, today=D(2026, 2, 2))
    assert amounts(charges) == {leases["A1"].pk: Decimal("1250.00"), leases["A2"].pk: Decimal("3750.00")}
    assert "unit A1 used 10.000 of 40.000 m³ on its own meter" in charges[0].description


def test_by_own_meter_needs_every_rooms_reading(owner, prop):
    _leases, reading = own_meters(owner, prop, {"A1": (JAN31, 10)})
    with pytest.raises(ValidationError, match="own meter of A2"):
        services.approve(owner, reading, today=JAN31)
    assert services.estimate(reading) == 0
    ways = {w.split: w for w in split.compare(reading)}
    assert not ways["USAGE"].available and ways["EQUAL"].charged == Decimal("5000.00")
    # Approving several at once leaves it waiting instead of failing.
    assert services.approve_many(owner, [reading], today=JAN31) == (0, 1)


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


def shares_url(reading):
    return reverse("meters:shares", args=[reading.public_id])


def test_the_page_compares_the_ways_with_vacant_rooms_at_nothing(client, owner, tank):
    login(client, owner)
    page = client.get(shares_url(tank["reading"])).content.decode()
    assert "Equally" in page and "By each room&#x27;s own meter" in page and "Approve this way" in page
    assert "Vacant" in page and "KES 0.00" in page and "KES 2,000.00" in page
    assert "No reading of the own meter of A1, A2, B1" in page
    page = client.get(shares_url(tank["reading"]), {"vacant": "LANDLORD", "bill": "6000"}).content.decode()
    assert "KES 2,000.00" in page and "KES 6,000.00" in page          # 6,000 over three rooms; B1's to the landlord
    assert "Enter an amount" in client.get(shares_url(tank["reading"]), {"bill": "abc"}).content.decode()


def test_approving_from_the_page_keeps_the_way_chosen(client, owner, prop, tank):
    login(client, owner)
    response = client.post(shares_url(tank["reading"]), {"split": "EQUAL", "vacant": "LANDLORD", "bill": "900"})
    assert response.status_code == 302
    reading = MeterReading.objects.get(pk=tank["reading"].pk)
    assert (reading.status, reading.vacant_share, reading.bill_total) == ("APPROVED", "LANDLORD", Decimal("900.00"))
    assert sorted(c.amount for c in reading.charges.all()) == [Decimal("150.00"), Decimal("300.00")]
    page = client.get(shares_url(reading)).content.decode()
    assert "Who pays what" in page and "Vacant" in page and "KES 450.00" in page
    assert "Divided equally · Landlord pays the empty rooms" in page


def test_a_failed_approval_comes_back_to_the_page_with_the_choices(client, owner, tank):
    login(client, owner)
    response = client.post(shares_url(tank["reading"]), {"split": "USAGE", "vacant": "LANDLORD", "bill": "900"})
    assert response.status_code == 302 and "vacant=LANDLORD" in response.url and "bill=900" in response.url
    assert MeterReading.objects.get(pk=tank["reading"].pk).status == "SUBMITTED"


def test_the_page_is_only_for_members_who_see_the_meter(client, owner, prop, tank):
    login(client, make_org())
    assert client.get(shares_url(tank["reading"])).status_code == 404
    client.logout()
    caretaker = add_member(owner.organization, "caretaker", properties=[prop])
    login(client, caretaker)
    # A caretaker sees and records meters but does not approve.
    page = client.get(shares_url(tank["reading"]))
    assert page.status_code == 200 and "Approve this way" not in page.content.decode()
    assert client.post(shares_url(tank["reading"]), {"split": "EQUAL"}).status_code == 404
    assert MeterReading.objects.get(pk=tank["reading"].pk).status == "SUBMITTED"


def test_the_meter_form_saves_who_pays_and_empty_rooms(client, owner, prop):
    _lease, unit = let(owner, prop)
    login(client, owner)
    form = {"utility": "WATER", "label": "Tank", "serial": "", "kind": "POSTPAID", "rate": "100", "minimum_charge": "",
            "split": "WEIGHTED", "payer": "LANDLORD", "vacant_share": "LANDLORD", f"unit-{unit.pk}": "on",
            f"weight-{unit.pk}": "1"}
    assert client.post(reverse("meters:create", args=[prop.public_id]), form).status_code == 302
    meter = prop.meters.get(label="Tank")
    assert (meter.payer, meter.vacant_share, meter.split) == ("LANDLORD", "LANDLORD", "WEIGHTED")


def test_the_sheet_carries_what_the_preview_divides(client, owner, prop, tank):
    login(client, owner)
    page = client.get(reverse("meters:round", args=[prop.public_id])).content.decode()
    assert 'data-rooms="1.00:1,1.00:1,1.00:0"' in page and 'data-vacant="OCCUPIED"' in page
    assert "data-share-amount" in page
