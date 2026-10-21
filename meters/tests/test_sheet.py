"""The reading sheet: rooms in order with who lives there, units used instead of the reading, and lines pasted
or uploaded to fill it in (D-143)."""

import datetime
import json
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from meters import services, sheet
from meters.models import MeterReading
from properties import services as property_services

from .conftest import let, make_meter, read

pytestmark = pytest.mark.django_db
D = datetime.date


@pytest.fixture
def block(owner, prop):
    """A10 and A2 let with their own meters, B1 empty, and one meter shared by A2 and B1."""
    _l, a10 = let(owner, prop, code="A10")
    _l, a2 = let(owner, prop, code="A2")
    b1 = property_services.create_unit(owner, prop, code="B1")
    meters = {"A10": make_meter(owner, prop, [a10], label="W-A10"),
              "A2": make_meter(owner, prop, [a2], label="W-A2"),
              "B1": make_meter(owner, prop, [b1], label="W-B1"),
              "shared": make_meter(owner, prop, [a2, b1], label="Tank")}
    for meter in meters.values():
        services.approve(owner, read(owner, meter, D(2025, 12, 31), 100))
    return meters


def sheet_url(prop):
    return reverse("meters:round", args=[prop.public_id])


def import_url(prop):
    return reverse("meters:round_import", args=[prop.public_id])


def test_natural_order_puts_a2_before_a10():
    assert sorted(["A10", "b1", "A2", "A1"], key=sheet.natural_key) == ["A1", "A2", "A10", "b1"]


def test_the_sheet_lists_rooms_in_order_with_the_tenant_or_vacant(client, owner, prop, block):
    login(client, owner)
    page = client.get(sheet_url(prop)).content.decode()
    assert page.index("W-A2") < page.index("W-A10") < page.index("W-B1")
    assert "Tenant A2" in page and "Tenant A10" in page
    assert "Vacant" in page and "Tank · shared by 2" in page
    assert 'data-prev="100.000"' in page and 'data-rate="100.00"' in page


def test_meters_read_this_month_are_marked_on_the_sheet(client, owner, prop, block, settings):
    today = datetime.date.today()
    read(owner, block["A2"], today, 120)  # waiting for approval still counts as read
    login(client, owner)
    response = client.get(sheet_url(prop))
    page = response.content.decode()
    assert response.context["done_count"] == 1
    assert page.count('data-done="1"') == 1
    assert "1 of 4 meter was already read this month" in page


def test_units_used_become_the_last_reading_plus_the_units(client, owner, prop, block):
    login(client, owner)
    meter = block["A2"]
    response = client.post(sheet_url(prop), {"read_on": "2026-01-31", f"units-{meter.pk}": "14",
                                             f"byunits-{meter.pk}": "1", f"value-{meter.pk}": "999"})
    assert response.status_code == 302
    reading = MeterReading.objects.get(meter=meter, read_on=D(2026, 1, 31))
    assert reading.value == Decimal("114.000") and reading.note == "Typed as units used."


def test_the_reading_wins_when_it_was_typed_last(client, owner, prop, block):
    login(client, owner)
    meter = block["A2"]
    client.post(sheet_url(prop), {"read_on": "2026-01-31", f"units-{meter.pk}": "14", f"byunits-{meter.pk}": "",
                                  f"value-{meter.pk}": "120"})
    reading = MeterReading.objects.get(meter=meter, read_on=D(2026, 1, 31))
    assert reading.value == Decimal("120.000") and reading.note == ""


def test_units_used_keep_the_note_and_need_an_earlier_reading(owner, prop):
    _l, unit = let(owner, prop)
    meter = make_meter(owner, prop, [unit])
    with pytest.raises(services.ValidationError, match="no earlier reading"):
        services.record_reading(owner, meter, read_on=D(2026, 1, 1), value="", units_used="5")
    read(owner, meter, D(2026, 1, 1), 50)
    reading = services.record_reading(owner, meter, read_on=D(2026, 1, 31), value="", units_used="7.5",
                                      note="tap dripping")
    assert reading.value == Decimal("57.500") and reading.note == "tap dripping · Typed as units used."
    with pytest.raises(services.ValidationError, match="no earlier reading"):
        services.record_reading(owner, meter, read_on=D(2026, 2, 28), value="", units_used="5", replaced=True,
                                note="new meter")


def test_units_used_add_to_the_newest_reading_even_one_recorded_after_the_page_opened(client, owner, prop, block):
    meter = block["A2"]
    login(client, owner)
    client.get(sheet_url(prop))
    read(owner, meter, D(2026, 1, 15), 105)
    client.post(sheet_url(prop), {"read_on": "2026-01-31", f"units-{meter.pk}": "10", f"byunits-{meter.pk}": "1"})
    assert MeterReading.objects.get(meter=meter, read_on=D(2026, 1, 31)).value == Decimal("115.000")


def test_a_bad_row_comes_back_with_what_was_typed(client, owner, prop, block):
    login(client, owner)
    meter = block["A2"]
    response = client.post(sheet_url(prop), {"read_on": "2026-01-31", f"value-{meter.pk}": "abc",
                                             f"units-{meter.pk}": "3"})
    page = response.content.decode()
    assert response.status_code == 200 and "Nothing was saved" in page
    assert 'value="abc"' in page and "sheet-error" in page
    assert not MeterReading.objects.filter(read_on=D(2026, 1, 31)).exists()


def test_offline_rounds_carry_units_used(client, owner, prop, block):
    login(client, owner)
    meter = block["A2"]
    body = {"id": "3f2a9c1e-0b7d-4f7a-9e55-1c2d3e4f5a6b", "read_on": "2026-01-31",
            "entries": [{"meter": str(meter.public_id), "value": "114", "units_used": "9", "note": ""}]}
    response = client.post(reverse("meters:offline_round", args=[prop.public_id]), json.dumps(body),
                           content_type="application/json")
    assert response.status_code == 201
    assert MeterReading.objects.get(meter=meter, read_on=D(2026, 1, 31)).value == Decimal("109.000")


# ---------------------------------------------------------------------------
# Pasted lines
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("line, room, number", [
    ("A1, 134", "A1", Decimal("134")),
    ("A1 134.5", "A1", Decimal("134.5")),
    ("A1\t1,234", "A1", Decimal("1234")),
    ("Room 4: 88", "Room 4", Decimal("88")),
    ("A1;Jane Wanjiru;12.25", "A1", Decimal("12.25")),
    ('"B 2","Peter","90"', "B 2", Decimal("90")),
])
def test_lines_are_read_in_the_usual_ways(line, room, number):
    assert sheet.parse_lines(line) == [(room, number, line)]


@pytest.mark.parametrize("line", ["Room,Reading", "A1", "A1, twelve", "134"])
def test_lines_without_a_room_and_a_number_are_left_out(line):
    assert sheet.parse_lines(line)[0][1] is None or sheet.parse_lines(line)[0][0] == ""


def test_pasted_lines_fill_rows_by_room_code_or_label(client, owner, prop, block):
    login(client, owner)
    text = "Room,Reading\na2, 120\nA10 130\ntank 140\nZ9, 5\nA2, 121"
    body = client.post(import_url(prop), {"text": text, "mode": "reading"}).json()
    got = {m["pk"]: (m["value"], m.get("repeated", False)) for m in body["matches"]}
    # A2 serves two meters (its own and the tank), so "a2" is ambiguous; the label picks the tank.
    assert got == {block["A10"].pk: ("130", False), block["shared"].pk: ("140", False)}
    reasons = {s["line"]: s["reason"] for s in body["skipped"]}
    assert "No room and number" in reasons["Room,Reading"] and "No meter is set up for Z9" in reasons["Z9, 5"]
    assert "more than one meter" in reasons["a2, 120"]
    assert not MeterReading.objects.filter(read_on__gt=D(2025, 12, 31)).exists()


@pytest.mark.parametrize("line", ["Rm A10 = 130", "Room A10, 130", "Hse. A10 130", "No. A10 130", "#A10 130"])
def test_a_word_written_before_the_room_is_ignored(client, owner, prop, block, line):
    # Found in the first real scan: the model read "Rm A3 = 190" correctly, but no room is coded "Rm A3".
    login(client, owner)
    body = client.post(import_url(prop), {"text": line, "mode": "reading"}).json()
    assert [(m["pk"], m["value"]) for m in body["matches"]] == [(block["A10"].pk, "130")]


def test_a_room_typed_twice_keeps_the_last_number(client, owner, prop, block):
    login(client, owner)
    body = client.post(import_url(prop), {"text": "A10, 130\nA10, 131", "mode": "units"}).json()
    assert body["matches"] == [{"meter": str(block["A10"].public_id), "pk": block["A10"].pk, "room": "A10",
                                "value": "131", "mode": "units", "line": "A10, 131", "repeated": True}]


def test_a_csv_file_and_the_utility_filter(client, owner, prop, block):
    _l, a200 = let(owner, prop, code="A200")
    power = make_meter(owner, prop, [a200], label="E-A20", utility="ELECTRICITY")
    water = make_meter(owner, prop, [a200], label="W-A20")
    login(client, owner)
    upload = SimpleUploadedFile("r.csv", "﻿Room,Reading\nA200,55\n".encode(), content_type="text/csv")
    body = client.post(import_url(prop), {"file": upload, "utility": "ELECTRICITY"}).json()
    assert [m["pk"] for m in body["matches"]] == [power.pk]
    body = client.post(import_url(prop), {"text": "A200,55", "utility": "WATER"}).json()
    assert [m["pk"] for m in body["matches"]] == [water.pk]


def test_import_needs_lines_and_a_post(client, owner, prop, block):
    login(client, owner)
    assert client.post(import_url(prop), {"text": "  "}).status_code == 400
    assert client.get(import_url(prop)).status_code == 405
    big = SimpleUploadedFile("r.csv", b"A1,1\n" * 60000, content_type="text/csv")
    assert client.post(import_url(prop), {"file": big}).status_code == 400


def test_import_is_only_for_members_who_record_on_the_property(client, owner, prop, block):
    login(client, make_org())
    assert client.post(import_url(prop), {"text": "A2, 1"}).status_code == 404
    client.logout()
    elsewhere = make_property(owner.organization)
    caretaker = add_member(owner.organization, "caretaker", properties=[elsewhere])
    login(client, caretaker)
    assert client.post(import_url(prop), {"text": "A2, 1"}).status_code == 404
