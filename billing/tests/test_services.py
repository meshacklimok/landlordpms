import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from accounts.tests.factories import add_member, fresh, make_org
from billing import services
from billing.models import ChargeType

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner():
    return make_org()


def test_create_rename_archive_restore(owner):
    ct = services.create_charge_type(owner, name="Security", category="SECURITY")
    with pytest.raises(ValidationError):
        services.create_charge_type(owner, name="security", category="SECURITY")  # case-insensitive
    services.rename_charge_type(owner, ct, name="Night guard")
    services.archive_charge_type(owner, ct)
    assert not ChargeType.objects.filter(pk=ct.pk).exists()
    # The name stays taken while archived.
    with pytest.raises(ValidationError):
        services.create_charge_type(owner, name="Night guard", category="OTHER")
    services.restore_charge_type(owner, ct)
    assert ChargeType.objects.filter(pk=ct.pk).exists()


def test_rent_deposit_and_late_fee_are_not_user_types(owner):
    for category in ChargeType.NOT_RECURRING:
        with pytest.raises(ValidationError):
            services.create_charge_type(owner, name=f"x {category}", category=category)


def test_system_types_cannot_be_archived(owner):
    services.ensure_default_charge_types(owner.organization)
    water = ChargeType.objects.get(organization=owner.organization, category="WATER")
    with pytest.raises(ValidationError):
        services.archive_charge_type(owner, water)
    services.rename_charge_type(owner, water, name="Maji")


def test_defaults_do_not_clash_with_existing_names(owner):
    services.create_charge_type(owner, name="Water", category="OTHER")
    services.ensure_default_charge_types(owner.organization)
    assert ChargeType.objects.get(organization=owner.organization, category="WATER").name == "Water (default)"


def test_permissions_and_isolation(owner):
    viewer = fresh(add_member(owner.organization, "viewer", all_properties=True))
    with pytest.raises(PermissionDenied):
        services.create_charge_type(viewer, name="X", category="OTHER")
    ct = services.create_charge_type(owner, name="X", category="OTHER")
    with pytest.raises(PermissionDenied):
        services.archive_charge_type(make_org(), ct)
