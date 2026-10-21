"""Are the meters read for a month's invoices? (D-146) What the Bill a month page tells the person billing."""

import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.tests.factories import add_member, make_property
from accounts.tests.test_isolation import login
from meters import readiness, services
from properties import services as property_services

from .conftest import let, make_meter, read

pytestmark = pytest.mark.django_db
D = datetime.date
DEC31, JAN31, FEB1 = D(2025, 12, 31), D(2026, 1, 31), D(2026, 2, 1)
JAN, FEB, MAR = D(2026, 1, 1), D(2026, 2, 1), D(2026, 3, 1)


@pytest.fixture
def block(owner, prop):
    """Three let rooms, each with its own water meter, all with an approved opening reading on 31 Dec."""
    meters = {}
    for code in ("A1", "A2", "A3"):
        _lease, unit = let(owner, prop, code=code)
        meters[code] = make_meter(owner, prop, [unit], label=f"W-{code}")
        services.approve(owner, read(owner, meters[code], DEC31, 0), today=DEC31)
    return meters


def names(meters):
    return sorted(m.label for m in meters)


def test_a_meter_read_and_approved_in_the_month_before_is_ready(owner, block):
    services.approve(owner, read(owner, block["A1"], JAN31, 12), today=JAN31)
    got = readiness.check(owner, FEB)
    # Readings taken in January are billed on the February invoices.
    assert (names(got.ready), names(got.waiting), names(got.missing)) == (["W-A1"], [], ["W-A2", "W-A3"])
    assert got.total == 3 and not got.complete and got.since == JAN


def test_a_reading_not_approved_yet_is_waiting(owner, block):
    read(owner, block["A2"], JAN31, 9)
    services.approve(owner, read(owner, block["A1"], JAN31, 12), today=JAN31)
    got = readiness.check(owner, FEB)
    assert (names(got.ready), names(got.waiting), names(got.missing)) == (["W-A1"], ["W-A2"], ["W-A3"])


def test_all_read_is_complete(owner, block):
    for meter in block.values():
        services.approve(owner, read(owner, meter, JAN31, 10), today=JAN31)
    got = readiness.check(owner, FEB)
    assert got.complete and len(got.ready) == 3 and got.problems == []


def test_last_months_reading_does_not_count_for_the_next_month(owner, block):
    for meter in block.values():
        services.approve(owner, read(owner, meter, JAN31, 10), today=JAN31)
    # March invoices need February readings; the January ones are used up.
    assert names(readiness.check(owner, MAR).missing) == ["W-A1", "W-A2", "W-A3"]


def test_only_meters_of_rooms_that_are_let_are_listed(owner, prop, block):
    empty = property_services.create_unit(owner, prop, code="B1")
    make_meter(owner, prop, [empty], label="W-B1")
    prepaid_unit = let(owner, prop, code="C1")[1]
    make_meter(owner, prop, [prepaid_unit], label="Token", kind="PREPAID")
    got = readiness.check(owner, FEB)
    assert got.total == 3 and "W-B1" not in names(got.missing) and "Token" not in names(got.missing)


def test_problems_are_grouped_by_property(owner, prop, block):
    other = make_property(owner.organization, name="Zeta Court")
    _lease, unit = let(owner, other, code="Z1")
    make_meter(owner, other, [unit], label="W-Z1")
    rows = readiness.check(owner, FEB).problems
    assert [(p.name, names(w), names(m)) for p, w, m in rows] == [
        (prop.name, [], ["W-A1", "W-A2", "W-A3"]), ("Zeta Court", [], ["W-Z1"])]


def test_a_member_sees_only_the_meters_of_their_properties(owner, prop, block):
    other = make_property(owner.organization, name="Zeta Court")
    _lease, unit = let(owner, other, code="Z1")
    make_meter(owner, other, [unit], label="W-Z1")
    manager = add_member(owner.organization, "manager", properties=[other])
    assert names(readiness.check(manager, FEB).missing) == ["W-Z1"]


def test_a_meter_the_landlord_pays_is_never_waited_for(owner, prop, block):
    _lease, unit = let(owner, prop, code="F1")
    make_meter(owner, prop, [unit], label="Free", payer="LANDLORD")
    got = readiness.check(owner, FEB)
    assert names(got.free) == ["Free"] and "Free" not in names(got.missing) and got.total == 3


def test_free_water_everywhere_leaves_nothing_to_wait_for(owner, prop):
    for code in ("A1", "A2"):
        _lease, unit = let(owner, prop, code=code)
        make_meter(owner, prop, [unit], label=f"W-{code}", payer="LANDLORD")
    got = readiness.check(owner, FEB)
    assert got.total == 0 and got.complete and len(got.free) == 2


def test_the_bill_a_month_page_lists_what_is_missing(client, owner, block):
    today = timezone.localdate()
    this_month = today.replace(day=1)
    login(client, owner)
    page = client.get(reverse("billing:generate")).content.decode()
    assert f'id="meters-{this_month:%Y-%m}"' in page and "Not all read" in page
    assert "W-A1, W-A2, W-A3" in page and "Not read:" in page


def test_the_page_says_when_every_meter_is_read(client, owner, block):
    today = timezone.localdate()
    login(client, owner)
    for meter in block.values():
        # Read on the last day of last month: it is billed on this month's invoices. The long gap since December
        # flags the reading, so it is approved with a note.
        services.approve(owner, read(owner, meter, today.replace(day=1) - datetime.timedelta(days=1), 10),
                         note="Checked", today=today)
    page = client.get(reverse("billing:generate")).content.decode()
    assert f'id="meters-{today:%Y-%m}"' in page and "All read" in page


def test_a_member_without_meter_access_sees_no_meter_card(client, owner, block):
    accountant = add_member(owner.organization, "accountant", all_properties=True)
    client.logout()
    login(client, accountant)
    response = client.get(reverse("billing:generate"))
    if response.status_code == 200:  # an accountant who may bill but not see meters
        assert "Meters for the" not in response.content.decode()
