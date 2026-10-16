"""The garbage collector's day sheet rules (D-161). The JSON screen is covered in accounts/tests/test_web_api_screens.py."""

import datetime

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from accounts.models import Organization
from accounts.tests.factories import make_org, make_property
from leases import pickups
from leases import services as lease_services
from leases.models import Pickup
from properties import services as property_services
from tenants import services as tenant_services

pytestmark = pytest.mark.django_db


def _contract(owner, prop, code, days_ago=40):
    unit = property_services.create_unit(owner, prop, code=code)
    tenant = tenant_services.create_tenant(owner, name=f"Customer {code}", phone="0712345602")
    start = timezone.localdate() - datetime.timedelta(days=days_ago)
    lease = lease_services.create_lease(owner, unit=unit, tenants=[tenant], start_date=start, end_date=None, rent=500)
    return lease_services.activate_lease(owner, lease)


@pytest.fixture
def route():
    owner = make_org()
    owner.organization.org_type = Organization.Type.WASTE
    owner.organization.save(update_fields=["org_type"])
    prop = make_property(owner.organization)
    return owner, prop


def test_only_contracts_running_on_the_day_are_on_the_sheet(route):
    owner, prop = route
    old = _contract(owner, prop, "A1", days_ago=40)
    new = _contract(owner, prop, "A2", days_ago=3)
    today = timezone.localdate()
    assert [lease.pk for lease in pickups.route_leases(prop, today)] == [old.pk, new.pk]
    assert [lease.pk for lease in pickups.route_leases(prop, today - datetime.timedelta(days=10))] == [old.pk]


def test_marks_are_saved_changed_and_cleared(route):
    owner, prop = route
    lease = _contract(owner, prop, "A1")
    today = timezone.localdate()
    assert pickups.record(owner, prop, today, {lease.pk: "DONE"})["DONE"] == 1
    assert pickups.outcomes(prop, today)[lease.pk].outcome == "DONE"
    pickups.record(owner, prop, today, {lease.pk: "MISSED"})
    assert Pickup.objects.filter(lease=lease, day=today).count() == 1
    assert pickups.outcomes(prop, today)[lease.pk].outcome == "MISSED"
    assert pickups.record(owner, prop, today, {lease.pk: ""})["cleared"] == 1
    assert not Pickup.objects.filter(lease=lease).exists()


def test_a_contract_that_is_not_on_the_route_that_day_is_ignored(route):
    owner, prop = route
    new = _contract(owner, prop, "A1", days_ago=3)
    long_ago = timezone.localdate() - datetime.timedelta(days=10)
    pickups.record(owner, prop, long_ago, {new.pk: "DONE"})
    assert not Pickup.objects.exists()


def test_the_day_must_be_neither_in_the_future_nor_too_long_ago(route):
    owner, prop = route
    _contract(owner, prop, "A1", days_ago=200)
    today = timezone.localdate()
    with pytest.raises(ValidationError):
        pickups.record(owner, prop, today + datetime.timedelta(days=1), {})
    with pytest.raises(ValidationError):
        pickups.record(owner, prop, today - datetime.timedelta(days=pickups.LOOK_BACK_DAYS + 1), {})
    pickups.record(owner, prop, today - datetime.timedelta(days=pickups.LOOK_BACK_DAYS), {})


def test_only_garbage_collectors_record_pickups():
    owner = make_org()  # a company, not a collector
    prop = make_property(owner.organization)
    with pytest.raises(ValidationError):
        pickups.record(owner, prop, timezone.localdate(), {})


def test_recording_needs_the_meters_record_capability(route):
    from accounts.tests.factories import add_member

    owner, prop = route
    viewer = add_member(owner.organization, "viewer")
    with pytest.raises(PermissionDenied):
        pickups.record(viewer, prop, timezone.localdate(), {})


def test_missed_pickups_are_counted_worst_first(route):
    owner, prop = route
    one, two = _contract(owner, prop, "A1"), _contract(owner, prop, "A2")
    today = timezone.localdate()
    for back, marks in ((0, {one.pk: "MISSED", two.pk: "MISSED"}), (1, {one.pk: "MISSED", two.pk: "DONE"}), (2, {one.pk: "DONE"})):
        pickups.record(owner, prop, today - datetime.timedelta(days=back), marks)
    rows = pickups.missed_by_contract(prop, today - datetime.timedelta(days=5), today)
    assert [(lease.pk, missed, done) for lease, missed, done in rows] == [(one.pk, 2, 1), (two.pk, 1, 1)]
