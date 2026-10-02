"""Tenant rules (doc 11 §6, doc 13, doc 14 A4/D5)."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from accounts.models import Organization
from accounts.tests.factories import add_member, fresh, make_org
from audit.models import AuditEvent
from tenants import services
from tenants.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner():
    return make_org("Greenview Ltd")


@pytest.fixture
def tenant(owner):
    return services.create_tenant(owner, name="Wanjiku Kamau", phone="0712 345678")


def test_create_normalises_phones_and_starts_as_prospect(owner):
    t = services.create_tenant(owner, name="  Otieno  ", phone="0712345678", alt_phone="+254 733-000111",
                               emergency_contact_phone="0722000222")
    assert (t.name, t.phone, t.alt_phone, t.emergency_contact_phone) == (
        "Otieno", "+254712345678", "+254733000111", "+254722000222")
    assert t.status == Tenant.Status.PROSPECT
    assert AuditEvent.objects.filter(action="tenant.create", object_id=str(t.public_id)).exists()


def test_bad_phone_rejected(owner):
    with pytest.raises(ValidationError) as exc:
        services.create_tenant(owner, name="X", phone="12345")
    assert "phone" in exc.value.message_dict


def test_same_alt_phone_as_phone_is_dropped(owner):
    t = services.create_tenant(owner, name="X", phone="0712345678", alt_phone="+254712345678")
    assert t.alt_phone == ""


def test_shared_phone_allowed_and_found(owner, tenant):
    other = services.create_tenant(owner, name="Wanjiku's husband", phone="+254712345678")
    assert set(services.tenants_with_phone(owner.organization, "+254712345678")) == {tenant, other}
    assert list(services.tenants_with_phone(owner.organization, "+254712345678", exclude_pk=tenant.pk)) == [other]


def test_company_tenant(owner):
    t = services.create_tenant(owner, name="Acme Ltd", phone="0712345678", kind=Tenant.Kind.COMPANY,
                               contact_person="J. Mwangi", id_type=Tenant.IdType.COMPANY_REG, id_number="pvt-123")
    assert (t.kind, t.id_number) == (Tenant.Kind.COMPANY, "PVT-123")


def test_individual_cannot_use_company_registration(owner):
    with pytest.raises(ValidationError) as exc:
        services.create_tenant(owner, name="X", phone="0712345678", id_type=Tenant.IdType.COMPANY_REG,
                               id_number="1")
    assert "id_type" in exc.value.message_dict


def test_id_number_needs_type(owner):
    with pytest.raises(ValidationError) as exc:
        services.create_tenant(owner, name="X", phone="0712345678", id_number="12345678")
    assert "id_type" in exc.value.message_dict


@pytest.mark.parametrize("pin", ["A123", "1234567890A", "B123456789C"])
def test_bad_kra_pin_rejected(owner, pin):
    with pytest.raises(ValidationError) as exc:
        services.create_tenant(owner, name="X", phone="0712345678", kra_pin=pin)
    assert "kra_pin" in exc.value.message_dict


def test_kra_pin_normalised(owner):
    t = services.create_tenant(owner, name="X", phone="0712345678", kra_pin=" a123456789z ")
    assert t.kra_pin == "A123456789Z"


def test_id_number_unique_per_org_even_when_archived(owner):
    t = services.create_tenant(owner, name="A", phone="0712345678", id_type="NATIONAL_ID", id_number="12345678")
    services.archive_tenant(owner, t)
    with pytest.raises(ValidationError) as exc:
        services.create_tenant(owner, name="B", phone="0722345678", id_type="NATIONAL_ID", id_number="12345678")
    assert "id_number" in exc.value.message_dict
    # Another organization may hold the same person.
    other = make_org()
    assert services.create_tenant(other, name="A", phone="0712345678", id_type="NATIONAL_ID",
                                  id_number="12345678").pk


def test_sensitive_values_masked_in_audit(owner, tenant):
    services.update_tenant(owner, tenant, id_type="NATIONAL_ID", id_number="12345678", kra_pin="A123456789Z")
    event = AuditEvent.objects.get(action="tenant.update")
    assert event.changes["id_number"] == ["", "…678"]
    assert event.changes["kra_pin"] == ["", "…89Z"]
    assert "12345678" not in str(event.changes)


def test_sensitive_change_with_same_ending_still_audited(owner, tenant):
    services.update_tenant(owner, tenant, id_type="NATIONAL_ID", id_number="11111678")
    services.update_tenant(owner, tenant, id_number="22222678")
    assert AuditEvent.objects.filter(action="tenant.update").count() == 2


def test_update_tenant_audits_diff_only(owner, tenant):
    services.update_tenant(owner, tenant, name="Wanjiku K.", phone="0712345678")
    event = AuditEvent.objects.get(action="tenant.update")
    assert event.changes == {"name": ["Wanjiku Kamau", "Wanjiku K."]}


def test_member_without_sensitive_cannot_set_id(owner):
    agent = add_member(owner.organization, "leasing_agent", all_properties=True)
    with pytest.raises(PermissionDenied):
        services.create_tenant(agent, name="X", phone="0712345678", id_type="NATIONAL_ID", id_number="1")
    t = services.create_tenant(agent, name="X", phone="0712345678")
    services.update_tenant(owner, t, id_type="NATIONAL_ID", id_number="12345678")
    # Editing other fields and passing the unchanged ID is fine; changing it is not.
    services.update_tenant(agent, t, name="Y", id_type="NATIONAL_ID", id_number="12345678")
    with pytest.raises(PermissionDenied):
        services.update_tenant(agent, t, id_number="99999999")


def test_caretaker_cannot_create(owner):
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    with pytest.raises(PermissionDenied):
        services.create_tenant(caretaker, name="X", phone="0712345678")


def test_read_only_org_cannot_create(owner):
    Organization.objects.filter(pk=owner.organization_id).update(status=Organization.Status.READ_ONLY)
    with pytest.raises(PermissionDenied):
        services.create_tenant(fresh(owner), name="X", phone="0712345678")


def test_archive_and_restore(owner, tenant):
    services.archive_tenant(owner, tenant)
    assert not Tenant.objects.filter(pk=tenant.pk).exists()
    with pytest.raises(ValidationError):
        services.update_tenant(owner, Tenant.all_objects.get(pk=tenant.pk), name="Z")
    services.restore_tenant(owner, Tenant.all_objects.get(pk=tenant.pk))
    assert Tenant.objects.filter(pk=tenant.pk).exists()
    assert [e.action for e in AuditEvent.objects.filter(action__startswith="tenant.").order_by("pk")] == [
        "tenant.create", "tenant.archive", "tenant.restore"]


def test_active_tenant_cannot_be_archived(owner, tenant):
    Tenant.objects.filter(pk=tenant.pk).update(status=Tenant.Status.ACTIVE)
    with pytest.raises(ValidationError):
        services.archive_tenant(owner, Tenant.objects.get(pk=tenant.pk))


def test_status_not_settable_through_services(owner, tenant):
    services.update_tenant(owner, tenant, status=Tenant.Status.ACTIVE)
    assert Tenant.objects.get(pk=tenant.pk).status == Tenant.Status.PROSPECT


# ---------------------------------------------------------------------------
# Visibility and isolation
# ---------------------------------------------------------------------------


def test_scoped_member_sees_only_tenants_they_added(owner, tenant):
    agent = add_member(owner.organization, "leasing_agent")
    mine = services.create_tenant(agent, name="Mine", phone="0799000111")
    assert list(services.visible_tenants(agent)) == [mine]
    assert set(services.visible_tenants(owner)) == {tenant, mine}
    with pytest.raises(PermissionDenied):
        services.update_tenant(agent, tenant, name="Hijack")
    with pytest.raises(PermissionDenied):
        services.archive_tenant(agent, tenant)


def test_other_org_cannot_see_or_touch(tenant):
    intruder = make_org("Intruder")
    assert not services.visible_tenants(intruder).exists()
    for call in (
        lambda: services.update_tenant(intruder, tenant, name="x"),
        lambda: services.archive_tenant(intruder, tenant),
        lambda: services.restore_tenant(intruder, tenant),
    ):
        with pytest.raises(PermissionDenied):
            call()
    assert not services.tenants_with_phone(intruder.organization, tenant.phone).exists()
