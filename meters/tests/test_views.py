"""Meter pages: who sees and does what, and the optional photo (D-057)."""

import datetime

import pytest
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from meters import services
from meters.models import MeterReading

from .conftest import jpeg, let, make_meter, read

pytestmark = pytest.mark.django_db
D = datetime.date


@pytest.fixture
def meter(owner, prop):
    _lease, unit = let(owner, prop)
    meter = make_meter(owner, prop, [unit])
    services.approve(owner, read(owner, meter, D(2025, 12, 31), 100))
    return meter


def detail(meter):
    return reverse("meters:detail", args=[meter.public_id])


def test_list_and_detail_show_the_meter(client, owner, meter):
    login(client, owner)
    assert meter.label in client.get(reverse("meters:list")).content.decode()
    page = client.get(detail(meter)).content.decode()
    assert "100.000 m³" in page and "Photo of the meter (optional)" in page


def test_a_caretaker_records_a_reading_without_a_photo(client, owner, prop, meter):
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    login(client, caretaker)
    response = client.post(detail(meter), {"action": "record", "read_on": "2026-01-31", "value": "110.5"})
    assert response.status_code == 302
    reading = MeterReading.objects.get(meter=meter, read_on=D(2026, 1, 31))
    assert not reading.photo and reading.status == MeterReading.Status.SUBMITTED


def test_a_reading_with_a_photo_is_served_only_inside_the_organization(client, owner, meter):
    login(client, owner)
    client.post(detail(meter), {"action": "record", "read_on": "2026-01-31", "value": "110", "photo": jpeg()})
    reading = MeterReading.objects.get(meter=meter, read_on=D(2026, 1, 31))
    url = reverse("meters:photo", args=[reading.public_id])
    response = client.get(url)
    assert response.status_code == 200 and response["Content-Type"] == "image/jpeg"
    client.logout()
    login(client, make_org())
    assert client.get(url).status_code == 404


def test_the_caretaker_cannot_approve(client, owner, meter):
    reading = read(owner, meter, D(2026, 1, 31), 110)
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    login(client, caretaker)
    assert 'value="approve"' not in client.get(detail(meter)).content.decode()
    assert client.post(detail(meter), {"action": "approve", "reading": reading.public_id}).status_code == 404
    assert client.get(reverse("meters:approvals")).status_code == 403
    reading.refresh_from_db()
    assert reading.status == MeterReading.Status.SUBMITTED


def test_the_accountant_approves_selected_readings(client, owner, meter):
    reading = read(owner, meter, D(2026, 1, 31), 110)
    accountant = add_member(owner.organization, "accountant", all_properties=True)
    login(client, accountant)
    assert "Would bill" in client.get(reverse("meters:approvals")).content.decode()
    client.post(reverse("meters:approvals"), {"action": "approve_selected", "reading": [reading.public_id]})
    reading.refresh_from_db()
    assert reading.status == MeterReading.Status.APPROVED
    assert reading.charges.count() == 1


def test_the_home_page_counts_readings_to_approve(client, owner, meter):
    read(owner, meter, D(2026, 1, 31), 110)
    login(client, owner)
    page = client.get(reverse("accounts:home")).content.decode()
    assert "water reading to approve" in page


def test_another_organizations_meter_is_404(client, meter):
    login(client, make_org())
    assert client.get(detail(meter)).status_code == 404
    assert client.get(reverse("meters:edit", args=[meter.public_id])).status_code == 404


def test_a_member_limited_to_other_properties_does_not_see_the_meter(client, owner, meter):
    elsewhere = make_property(owner.organization)
    manager = add_member(owner.organization, "manager", properties=[elsewhere])
    login(client, manager)
    assert client.get(detail(meter)).status_code == 404
    assert meter.label not in client.get(reverse("meters:list")).content.decode()


def test_the_round_records_several_meters_with_optional_photos(client, owner, prop, meter):
    _lease, u2 = let(owner, prop, code="A2")
    second = make_meter(owner, prop, [u2], label="W2")
    login(client, owner)
    url = reverse("meters:round", args=[prop.public_id])
    assert "A photo is optional" in client.get(url).content.decode()
    response = client.post(url, {"read_on": "2026-01-31", f"value-{meter.pk}": "111",
                                 f"value-{second.pk}": "5", f"photo-{second.pk}": jpeg()})
    assert response.status_code == 302
    assert not MeterReading.objects.get(meter=meter, read_on=D(2026, 1, 31)).photo
    assert MeterReading.objects.get(meter=second).photo


def test_managing_a_meter_through_the_form(client, owner, prop):
    _lease, unit = let(owner, prop)
    login(client, owner)
    response = client.post(reverse("meters:create", args=[prop.public_id]), {
        "label": "Block A", "serial": "", "kind": "POSTPAID", "rate": "120", "minimum_charge": "",
        "split": "EQUAL", f"unit-{unit.pk}": "on", f"weight-{unit.pk}": "1"})
    assert response.status_code == 302
    meter = prop.meters.get()
    assert meter.label == "Block A" and list(meter.units.all()) == [unit]
    # No unit ticked: the form comes back with the reason.
    response = client.post(reverse("meters:edit", args=[meter.public_id]), {
        "label": "Block A", "kind": "POSTPAID", "rate": "120", "split": "EQUAL"})
    assert response.status_code == 200
