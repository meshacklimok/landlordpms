"""Property, building and unit rules (doc 11 §5, §23; D-029)."""

from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from accounts.models import Organization
from accounts.permissions import can, visible_properties
from accounts.tests.factories import add_member, fresh, make_org, make_property
from audit.models import AuditEvent
from properties import services
from properties.models import Building, Property, Unit

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner():
    return make_org("Greenview Ltd")


@pytest.fixture
def prop(owner):
    return services.create_property(owner, name="Greenview Apartments", code="gv")


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


def test_create_property_normalises_code_and_audits(owner):
    p = services.create_property(owner, name="  Greenview  ", code=" g v ", county="NAIROBI", area="Kilimani")
    assert (p.name, p.code, p.county) == ("Greenview", "GV", "NAIROBI")
    assert p.address_line == "Kilimani, Nairobi"
    assert AuditEvent.objects.filter(action="property.create", object_id=str(p.public_id)).exists()


@pytest.mark.parametrize("code", ["", "G-V", "TOO-LONG-CODE", "G V!"])
def test_create_property_rejects_bad_codes(owner, code):
    with pytest.raises(ValidationError) as exc:
        services.create_property(owner, name="X", code=code)
    assert "code" in exc.value.message_dict


def test_property_code_unique_per_org_even_when_archived(owner, prop):
    services.archive_property(owner, prop)
    with pytest.raises(ValidationError) as exc:
        services.create_property(owner, name="Other", code="GV")
    assert "code" in exc.value.message_dict


def test_same_property_code_allowed_in_another_org(prop):
    other = make_org()
    assert services.create_property(other, name="Elsewhere", code="GV").code == "GV"


def test_property_code_cannot_change(owner, prop):
    with pytest.raises(ValidationError):
        services.update_property(owner, prop, code="NEW")
    services.update_property(owner, prop, name="Greenview Court", code="gv")  # same code is fine
    assert Property.objects.get(pk=prop.pk).name == "Greenview Court"


def test_single_house_gets_main_unit(owner):
    p = services.create_property(owner, name="Karen House", code="KH", category=Property.Category.SINGLE_HOUSE)
    unit = Unit.objects.get(property=p)
    assert (unit.code, unit.unit_type, unit.payment_reference) == ("MAIN", Unit.Type.HOUSE, "KH-MAIN")


def test_scoped_manager_can_see_property_they_create(owner):
    manager = add_member(owner.organization, "manager")
    p = services.create_property(manager, name="New Block", code="NB")
    assert list(visible_properties(fresh(manager))) == [p]


def test_caretaker_cannot_create_property(owner):
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    with pytest.raises(PermissionDenied):
        services.create_property(caretaker, name="X", code="X")


def test_read_only_org_cannot_create_property(owner):
    Organization.objects.filter(pk=owner.organization_id).update(status=Organization.Status.READ_ONLY)
    with pytest.raises(PermissionDenied):
        services.create_property(fresh(owner), name="X", code="X")


def test_archive_and_restore_property_takes_units_and_buildings(owner, prop):
    block = services.create_building(owner, prop, name="Block A")
    services.create_unit(owner, prop, code="A1", building=block)
    services.create_unit(owner, prop, code="A2")
    separately = services.create_unit(owner, prop, code="A3")
    services.archive_unit(owner, separately)

    services.archive_property(owner, prop)
    assert not Property.objects.filter(pk=prop.pk).exists()
    assert not Unit.objects.filter(property=prop).exists()
    assert not Building.objects.filter(property=prop).exists()

    services.restore_property(owner, Property.all_objects.get(pk=prop.pk))
    assert set(Unit.objects.filter(property=prop).values_list("code", flat=True)) == {"A1", "A2"}
    assert Building.objects.filter(property=prop).count() == 1


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


def test_unit_gets_payment_reference(owner, prop):
    unit = services.create_unit(owner, prop, code="a102", unit_type=Unit.Type.BEDSITTER, list_rent=Decimal("8500"))
    assert (unit.code, unit.payment_reference) == ("A102", "GV-A102")
    assert unit.get_effective_status_display() == "Available"


def test_unit_code_unique_per_property_even_when_archived(owner, prop):
    unit = services.create_unit(owner, prop, code="A1")
    services.archive_unit(owner, unit)
    with pytest.raises(ValidationError) as exc:
        services.create_unit(owner, prop, code="a1")
    assert "code" in exc.value.message_dict


def test_same_unit_code_in_two_properties(owner, prop):
    other = services.create_property(owner, name="Riverside", code="RS")
    services.create_unit(owner, prop, code="A1")
    assert services.create_unit(owner, other, code="A1").payment_reference == "RS-A1"


def test_changing_unit_code_changes_payment_reference(owner, prop):
    unit = services.create_unit(owner, prop, code="A1")
    services.update_unit(owner, unit, code="B1")
    unit.refresh_from_db()
    assert unit.payment_reference == "GV-B1"
    event = AuditEvent.objects.get(action="unit.update")
    assert event.changes["payment_reference"] == ["GV-A1", "GV-B1"]


@pytest.mark.parametrize("code", ["", "-A1", "A1-", "A 1!", "ABCDEFGHIJK"])
def test_bad_unit_codes_rejected(owner, prop, code):
    with pytest.raises(ValidationError):
        services.create_unit(owner, prop, code=code)


def test_unit_building_must_be_in_same_property(owner, prop):
    other = services.create_property(owner, name="Riverside", code="RS")
    foreign_block = services.create_building(owner, other, name="Block A")
    with pytest.raises(ValidationError) as exc:
        services.create_unit(owner, prop, code="A1", building=foreign_block)
    assert "building" in exc.value.message_dict


def test_negative_asking_rent_rejected(owner, prop):
    with pytest.raises(ValidationError):
        services.create_unit(owner, prop, code="A1", list_rent=Decimal("-1"))


def test_set_unit_status(owner, prop):
    unit = services.create_unit(owner, prop, code="A1")
    services.set_unit_status(owner, unit, Unit.ManualStatus.UNDER_MAINTENANCE)
    assert Unit.objects.get(pk=unit.pk).get_effective_status_display() == "Under maintenance"
    with pytest.raises(ValidationError):
        services.set_unit_status(owner, unit, "OCCUPIED")


def test_cannot_add_unit_to_archived_property(owner, prop):
    services.archive_property(owner, prop)
    with pytest.raises(ValidationError):
        services.create_unit(owner, Property.all_objects.get(pk=prop.pk), code="A1")


def test_restore_unit_needs_live_building(owner, prop):
    block = services.create_building(owner, prop, name="Block A")
    unit = services.create_unit(owner, prop, code="A1", building=block)
    services.archive_unit(owner, unit)
    services.archive_building(owner, block)
    with pytest.raises(ValidationError):
        services.restore_unit(owner, Unit.all_objects.get(pk=unit.pk))


# ---------------------------------------------------------------------------
# Buildings
# ---------------------------------------------------------------------------


def test_building_name_unique_per_property(owner, prop):
    services.create_building(owner, prop, name="Block A")
    with pytest.raises(ValidationError):
        services.create_building(owner, prop, name="block a")


def test_cannot_archive_building_with_units(owner, prop):
    block = services.create_building(owner, prop, name="Block A")
    services.create_unit(owner, prop, code="A1", building=block)
    with pytest.raises(ValidationError):
        services.archive_building(owner, block)


# ---------------------------------------------------------------------------
# Scope and isolation
# ---------------------------------------------------------------------------


def test_scoped_member_cannot_touch_other_properties(owner, prop):
    other = services.create_property(owner, name="Riverside", code="RS")
    manager = add_member(owner.organization, "manager", properties=[other])
    assert can(manager, "units.manage", other)
    with pytest.raises(PermissionDenied):
        services.create_unit(manager, prop, code="A1")
    with pytest.raises(PermissionDenied):
        services.update_property(manager, prop, name="Hijack")


@pytest.mark.parametrize(
    "call",
    [
        lambda a, p: services.update_property(a, p, name="x"),
        lambda a, p: services.archive_property(a, p),
        lambda a, p: services.create_building(a, p, name="x"),
        lambda a, p: services.create_unit(a, p, code="X1"),
    ],
)
def test_other_org_cannot_touch_property(prop, call):
    intruder = make_org("Intruder")
    with pytest.raises(PermissionDenied):
        call(intruder, prop)


def test_other_org_cannot_touch_unit(owner, prop):
    unit = services.create_unit(owner, prop, code="A1")
    intruder = make_org("Intruder")
    for call in (
        lambda: services.update_unit(intruder, unit, code="Z1"),
        lambda: services.set_unit_status(intruder, unit, Unit.ManualStatus.INACTIVE),
        lambda: services.archive_unit(intruder, unit),
    ):
        with pytest.raises(PermissionDenied):
            call()


def test_unit_cannot_use_building_from_other_org(owner, prop):
    intruder = make_org("Intruder")
    foreign = services.create_building(intruder, make_property(intruder.organization), name="Block A")
    with pytest.raises(PermissionDenied):
        services.create_unit(owner, prop, code="A1", building=foreign)
