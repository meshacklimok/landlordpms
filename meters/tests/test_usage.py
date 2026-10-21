"""Water and electricity used in a month (D-145): each tenant's units and charges, what fell on the landlord, and
the month's totals."""

import datetime
from decimal import Decimal

import pytest
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from meters import services, usage
from properties import services as property_services

from .conftest import let, make_meter, read

pytestmark = pytest.mark.django_db
D = datetime.date
DEC31, JAN30, JAN31 = D(2025, 12, 31), D(2026, 1, 30), D(2026, 1, 31)
JAN = D(2026, 1, 1)


@pytest.fixture
def block(owner, prop):
    """A1 let all month, A2 from 16 Jan, B1 empty, on one tank (30 m³ at 100); A1 also has its own power meter."""
    _l1, a1 = let(owner, prop, code="A1")
    _l2, a2 = let(owner, prop, code="A2", start=D(2026, 1, 16))
    b1 = property_services.create_unit(owner, prop, code="B1")
    tank = make_meter(owner, prop, [a1, a2, b1], label="Tank")
    power = make_meter(owner, prop, [a1], label="A1 power", utility="ELECTRICITY", rate="25")
    for meter in (tank, power):
        services.approve(owner, read(owner, meter, DEC31, 0), today=DEC31)
    return {"tank": tank, "power": power, "units": (a1, a2, b1)}


def figures(section):
    return {row.unit.code: (row.use["water"]["units"], row.use["water"]["amount"],
                            row.use["power"]["units"], row.use["power"]["amount"]) for row in section.rows}


def test_each_tenant_gets_their_part_of_a_shared_meter_and_their_own(owner, block):
    services.approve(owner, read(owner, block["tank"], JAN30, 30), today=JAN31)
    services.approve(owner, read(owner, block["power"], JAN31, 50), today=JAN31)
    [section] = usage.report(owner, JAN)
    # A1 counts a whole room, A2 half (15 of 30 days), B1 is empty: 30 m³ over 1.5.
    assert figures(section) == {"A1": (Decimal("20.000"), Decimal("2000.00"), Decimal("50.000"), Decimal("1250.00")),
                                "A2": (Decimal("10.000"), Decimal("1000.00"), 0, 0)}
    assert section.landlord["water"] == {"units": 0, "amount": 0}
    assert section.bill["water"] == {"units": Decimal("30"), "amount": Decimal("3000.00")}
    assert [row.tenant for row in section.rows] == ["Tenant A1", "Tenant A2"]
    assert section.rows[0].amount == Decimal("3250.00")


def test_empty_rooms_the_landlord_pays_are_the_landlords(owner, block):
    services.approve(owner, read(owner, block["tank"], JAN30, 30), vacant_share="LANDLORD", today=JAN31)
    [section] = usage.report(owner, JAN)
    assert figures(section)["A1"][:2] == (Decimal("10.000"), Decimal("1000.00"))
    assert figures(section)["A2"][:2] == (Decimal("5.000"), Decimal("500.00"))
    assert section.landlord["water"] == {"units": Decimal("15.000"), "amount": Decimal("1500.00")}


def test_a_meter_the_landlord_pays_shows_the_use_and_charges_nobody(owner, prop):
    _lease, unit = let(owner, prop)
    meter = make_meter(owner, prop, [unit], payer="LANDLORD", utility="ELECTRICITY")
    services.approve(owner, read(owner, meter, DEC31, 0), today=DEC31)
    services.approve(owner, read(owner, meter, JAN31, 40), today=JAN31)
    [section] = usage.report(owner, JAN)
    assert figures(section)["A1"] == (0, 0, Decimal("40.000"), 0)
    assert section.landlord["power"]["amount"] == Decimal("4000.00")


def test_the_bill_typed_in_is_what_is_divided(owner, block):
    services.approve(owner, read(owner, block["tank"], JAN30, 30), bill_total="4,500", today=JAN31)
    [section] = usage.report(owner, JAN)
    assert section.bill["water"]["amount"] == Decimal("4500.00")
    assert section.tenants["water"]["amount"] == Decimal("4500.00")


def test_only_approved_readings_taken_in_the_month(owner, block):
    read(owner, block["tank"], JAN30, 30)                                  # waiting
    services.approve(owner, read(owner, block["power"], D(2026, 2, 3), 9), today=D(2026, 2, 3))
    assert usage.report(owner, JAN) == []
    assert len(usage.report(owner, D(2026, 2, 1))) == 1


def test_totals_add_up_every_property(owner, prop, block):
    other = make_property(owner.organization, name="Zeta Court")
    _lease, z1 = let(owner, other, code="Z1")
    meter = make_meter(owner, other, [z1], label="Z1 water")
    services.approve(owner, read(owner, meter, DEC31, 0), today=DEC31)
    services.approve(owner, read(owner, meter, JAN31, 5), today=JAN31)
    services.approve(owner, read(owner, block["tank"], JAN30, 30), today=JAN31)
    sections = usage.report(owner, JAN)
    assert [s.property.name for s in sections] == [prop.name, "Zeta Court"]
    totals = usage.totals(sections)
    assert totals["bill"]["water"]["units"] == Decimal("35")
    assert totals["tenants"]["water"]["amount"] == Decimal("3500.00")
    assert len(usage.report(owner, JAN, other)) == 1


def test_the_page_shows_the_month_and_downloads(client, owner, block):
    services.approve(owner, read(owner, block["tank"], JAN30, 30), today=JAN31)
    login(client, owner)
    url = reverse("meters:usage")
    page = client.get(url, {"month": "2026-01"}).content.decode()
    assert "January 2026" in page and "Tenant A2" in page and "KES 2,000.00" in page
    assert "Download CSV" in page and 'href="?month=2025-12' in page
    response = client.get(url, {"month": "2026-01", "format": "csv"})
    assert response["Content-Disposition"] == 'attachment; filename="meter-usage-2026-01.csv"'
    lines = response.content.decode("utf-8-sig").splitlines()
    assert lines[0].startswith("Property,Room,Tenant,Water m3")
    assert any(",A1,Tenant A1,20.000,2000.00," in line for line in lines)
    assert "No approved readings" in client.get(url, {"month": "2025-11"}).content.decode()


def test_a_tenant_name_never_runs_as_a_formula(client, owner, prop):
    lease, unit = let(owner, prop, code="A1")
    tenant = lease.primary_tenant
    tenant.name = "=HYPERLINK(1)"
    tenant.save(update_fields=["name"])
    meter = make_meter(owner, prop, [unit])
    services.approve(owner, read(owner, meter, DEC31, 0), today=DEC31)
    services.approve(owner, read(owner, meter, JAN31, 3), today=JAN31)
    login(client, owner)
    body = client.get(reverse("meters:usage"), {"month": "2026-01", "format": "csv"}).content.decode("utf-8-sig")
    assert ",'=HYPERLINK(1)," in body


def test_members_see_only_their_properties(client, owner, prop, block):
    services.approve(owner, read(owner, block["tank"], JAN30, 30), today=JAN31)
    elsewhere = make_property(owner.organization, name="Elsewhere")
    caretaker = add_member(owner.organization, "caretaker", properties=[elsewhere])
    assert usage.report(caretaker, JAN) == []
    assert usage.report(make_org(), JAN) == []
    login(client, caretaker)
    assert "Tenant A1" not in client.get(reverse("meters:usage"), {"month": "2026-01"}).content.decode()
