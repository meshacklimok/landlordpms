"""Lease drafting rules, the no-overlap constraint, occupancy and tenant status."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from accounts.tests.factories import add_member, fresh, make_org, make_property
from audit.models import AuditEvent
from billing.models import ChargeType
from billing.services import ensure_default_charge_types, recurring_charge_types
from leases import services
from leases.models import Lease
from properties import services as property_services
from properties.models import Unit
from tenants import services as tenant_services
from tenants.models import Tenant

pytestmark = pytest.mark.django_db

D = datetime.date
TODAY = datetime.date.today()
JAN_1, DEC_31 = D(2026, 1, 1), D(2026, 12, 31)


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def prop(owner):
    return make_property(owner.organization)


@pytest.fixture
def unit(owner, prop):
    return property_services.create_unit(owner, prop, code="A1")


@pytest.fixture
def tenant(owner):
    return tenant_services.create_tenant(owner, name="Wanjiku", phone="0712345678")


def draft(actor, unit, tenants, start=JAN_1, end=DEC_31, rent=15000, **kw):
    return services.create_lease(actor, unit=unit, tenants=tenants, start_date=start, end_date=end, rent=rent, **kw)


def activate(lease):
    # Stand-in for the activation action (step 4).
    Lease.objects.filter(pk=lease.pk).update(status=Lease.Status.ACTIVE)
    lease.refresh_from_db()
    return lease


def water(org):
    ensure_default_charge_types(org)
    return ChargeType.objects.get(organization=org, category=ChargeType.Category.WATER)


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------


def test_create_draft(owner, unit, tenant):
    other = tenant_services.create_tenant(owner, name="Otieno", phone="0722000111")
    lease = draft(owner, unit, [tenant, other], deposit_amount="30000")
    assert lease.status == Lease.Status.DRAFT and lease.number == ""
    assert lease.deposit_amount == Decimal("30000.00")
    links = list(lease.lease_tenants.order_by("-is_primary"))
    assert [(lt.tenant, lt.is_primary) for lt in links] == [(tenant, True), (other, False)]
    assert [(rc.effective_from, rc.amount) for rc in lease.rent_changes.all()] == [(D(2026, 1, 1), Decimal("15000"))]
    assert lease.rent_on(D(2026, 6, 1)) == Decimal("15000")
    assert AuditEvent.objects.filter(action="lease.create", object_id=str(lease.public_id)).exists()
    # A draft does not make anyone a tenant yet.
    tenant.refresh_from_db()
    assert tenant.status == Tenant.Status.PROSPECT


def test_create_needs_a_tenant_and_valid_dates(owner, unit, tenant):
    with pytest.raises(ValidationError) as e:
        draft(owner, unit, [])
    assert "tenants" in e.value.message_dict
    with pytest.raises(ValidationError) as e:
        draft(owner, unit, [tenant], start=D(2026, 5, 1), end=D(2026, 4, 1))
    assert "end_date" in e.value.message_dict
    with pytest.raises(ValidationError) as e:
        draft(owner, unit, [tenant], rent=0)
    assert "rent" in e.value.message_dict
    with pytest.raises(ValidationError) as e:
        draft(owner, unit, [tenant], due_day=29)
    assert "due_day" in e.value.message_dict
    assert not Lease.objects.exists()


def test_periodic_lease_has_no_end(owner, unit, tenant):
    lease = draft(owner, unit, [tenant], end=None)
    assert lease.end_date is None


def test_cannot_let_archived_or_inactive_unit(owner, unit, tenant):
    property_services.set_unit_status(owner, unit, Unit.ManualStatus.INACTIVE)
    with pytest.raises(ValidationError):
        draft(owner, unit, [tenant])
    property_services.set_unit_status(owner, unit, Unit.ManualStatus.NORMAL)
    property_services.archive_unit(owner, unit)
    with pytest.raises(ValidationError):
        draft(owner, unit, [tenant])


def test_archived_tenant_rejected(owner, unit, tenant):
    tenant_services.archive_tenant(owner, tenant)
    with pytest.raises(ValidationError):
        draft(owner, unit, [tenant])


def test_permissions(owner, prop, unit, tenant):
    org = owner.organization
    viewer = fresh(add_member(org, "viewer", all_properties=True))
    with pytest.raises(PermissionDenied):
        draft(viewer, unit, [tenant])
    other_prop = make_property(org)
    agent = fresh(add_member(org, "leasing_agent", properties=[other_prop]))
    own = tenant_services.create_tenant(agent, name="Agent's tenant", phone="0733000111")
    with pytest.raises(PermissionDenied):
        draft(agent, unit, [own])
    # The agent's property is fine, but a tenant they cannot see is not.
    other_unit = property_services.create_unit(owner, other_prop, code="B1")
    with pytest.raises(PermissionDenied):
        draft(agent, other_unit, [tenant])
    assert draft(agent, other_unit, [own]).status == Lease.Status.DRAFT


def test_other_org_records_rejected(owner, unit, tenant):
    other = make_org()
    foreign_tenant = tenant_services.create_tenant(other, name="Foreign", phone="0744000111")
    with pytest.raises(PermissionDenied):
        draft(owner, unit, [foreign_tenant])
    with pytest.raises(PermissionDenied):
        draft(other, unit, [foreign_tenant])


def test_update_draft_moves_rent_and_charges(owner, unit, tenant):
    lease = draft(owner, unit, [tenant])
    services.add_charge(owner, lease, charge_type=water(owner.organization), amount=500)
    services.update_draft_lease(owner, lease, start_date=D(2026, 2, 1), rent="16000", grace_days=5)
    lease.refresh_from_db()
    assert lease.grace_days == 5
    assert [(rc.effective_from, rc.amount) for rc in lease.rent_changes.all()] == [(D(2026, 2, 1), Decimal("16000"))]
    assert lease.charges.get().active_from == D(2026, 2, 1)
    event = AuditEvent.objects.filter(action="lease.update").latest("pk")
    assert event.changes["rent"] == ["15000.00", "16000.00"]
    assert event.changes["grace_days"] == [3, 5]


def test_only_drafts_can_be_edited_or_deleted(owner, unit, tenant):
    lease = activate(draft(owner, unit, [tenant]))
    with pytest.raises(ValidationError):
        services.update_draft_lease(owner, lease, grace_days=1)
    with pytest.raises(ValidationError):
        services.delete_draft_lease(owner, lease)
    other = tenant_services.create_tenant(owner, name="Other", phone="0722000111")
    with pytest.raises(ValidationError):
        services.add_lease_tenant(owner, lease, other)


def test_delete_draft(owner, unit, tenant):
    lease = draft(owner, unit, [tenant])
    services.add_charge(owner, lease, charge_type=water(owner.organization), amount=500)
    services.add_payer(owner, lease, phone="0799111222")
    services.delete_draft_lease(owner, lease)
    assert not Lease.all_objects.exists()
    assert AuditEvent.objects.filter(action="lease.delete_draft").exists()


def test_co_tenants(owner, unit, tenant):
    lease = draft(owner, unit, [tenant])
    other = tenant_services.create_tenant(owner, name="Other", phone="0722000111")
    services.add_lease_tenant(owner, lease, other)
    with pytest.raises(ValidationError):
        services.add_lease_tenant(owner, lease, other)
    with pytest.raises(ValidationError):
        services.remove_lease_tenant(owner, lease, tenant)  # primary
    services.set_primary_tenant(owner, lease, other)
    assert lease.lease_tenants.get(is_primary=True).tenant == other
    services.remove_lease_tenant(owner, lease, tenant)
    assert [lt.tenant for lt in lease.lease_tenants.all()] == [other]


# ---------------------------------------------------------------------------
# Rent changes
# ---------------------------------------------------------------------------


def test_rent_change_on_active_lease(owner, unit, tenant):
    lease = draft(owner, unit, [tenant])
    with pytest.raises(ValidationError):
        services.add_rent_change(owner, lease, effective_from=D(2026, 7, 1), amount=17000)
    activate(lease)
    services.add_rent_change(owner, lease, effective_from=D(2026, 7, 1), amount=17000, reason="Annual review")
    assert lease.rent_on(D(2026, 6, 30)) == Decimal("15000")
    assert lease.rent_on(D(2026, 7, 1)) == Decimal("17000")
    assert lease.rent_changes.count() == 2
    for bad in (D(2026, 1, 1), D(2027, 1, 1), D(2026, 7, 1)):
        with pytest.raises(ValidationError):
            services.add_rent_change(owner, lease, effective_from=bad, amount=18000)
    agent = fresh(add_member(owner.organization, "leasing_agent", all_properties=True))
    with pytest.raises(PermissionDenied):
        services.add_rent_change(agent, lease, effective_from=D(2026, 9, 1), amount=18000)


# ---------------------------------------------------------------------------
# Charges and payers
# ---------------------------------------------------------------------------


def test_default_charge_types_seeded_once(owner):
    org = owner.organization
    ensure_default_charge_types(org)
    ensure_default_charge_types(org)
    assert ChargeType.objects.for_org(org).filter(is_system=True).count() == 5
    categories = set(recurring_charge_types(org).values_list("category", flat=True))
    assert ChargeType.Category.RENT not in categories and ChargeType.Category.WATER in categories


def test_charges(owner, unit, tenant):
    org = owner.organization
    lease = draft(owner, unit, [tenant])
    ensure_default_charge_types(org)
    rent_type = ChargeType.objects.get(organization=org, category=ChargeType.Category.RENT)
    with pytest.raises(ValidationError):
        services.add_charge(owner, lease, charge_type=rent_type, amount=100)
    services.add_charge(owner, lease, charge_type=water(org), amount=500)
    with pytest.raises(ValidationError):
        services.add_charge(owner, lease, charge_type=water(org), amount=600)  # overlaps
    with pytest.raises(ValidationError):
        services.add_charge(owner, lease, charge_type=water(org), amount=600, active_from=D(2025, 12, 1))
    charge = lease.charges.get()
    assert charge.active_from == lease.start_date and charge.is_active_on(D(2026, 3, 1))


def test_charge_permissions_follow_lease_state(owner, unit, tenant):
    org = owner.organization
    agent = fresh(add_member(org, "leasing_agent", all_properties=True))
    lease = draft(owner, unit, [tenant])
    charge = services.add_charge(agent, lease, charge_type=water(org), amount=500)
    activate(lease)
    with pytest.raises(PermissionDenied):
        services.end_charge(agent, charge, active_to=D(2026, 6, 30))
    services.end_charge(owner, charge, active_to=D(2026, 6, 30))
    charge.refresh_from_db()
    assert charge.active_to == D(2026, 6, 30)
    # A new one can start after the old one ended.
    services.add_charge(owner, lease, charge_type=water(org), amount=700, active_from=D(2026, 7, 1))


def test_ending_a_draft_charge_removes_it(owner, unit, tenant):
    lease = draft(owner, unit, [tenant])
    charge = services.add_charge(owner, lease, charge_type=water(owner.organization), amount=500)
    services.end_charge(owner, charge, active_to=TODAY)
    assert not lease.charges.exists()


def test_moving_a_charge_end_cannot_overlap_the_next_one(owner, unit, tenant):
    lease = activate(draft(owner, unit, [tenant]))
    kind = water(owner.organization)
    first = services.add_charge(owner, lease, charge_type=kind, amount=500, active_to=D(2026, 6, 30))
    services.add_charge(owner, lease, charge_type=kind, amount=700, active_from=D(2026, 7, 1))
    with pytest.raises(ValidationError, match="already has"):
        services.end_charge(owner, first, active_to=D(2026, 8, 31))
    first.refresh_from_db()
    assert first.active_to == D(2026, 6, 30)
    services.end_charge(owner, first, active_to=D(2026, 6, 15))


def test_draft_dates_must_still_hold_its_charges(owner, unit, tenant):
    lease = draft(owner, unit, [tenant])
    kind = water(owner.organization)
    services.add_charge(owner, lease, charge_type=kind, amount=500, active_to=D(2026, 3, 31))
    services.add_charge(owner, lease, charge_type=kind, amount=600, active_from=D(2026, 11, 1))
    with pytest.raises(ValidationError) as exc:
        services.update_draft_lease(owner, lease, start_date=D(2026, 4, 1))
    assert "start_date" in exc.value.message_dict
    with pytest.raises(ValidationError) as exc:
        services.update_draft_lease(owner, Lease.objects.get(pk=lease.pk), end_date=D(2026, 10, 31))
    assert "end_date" in exc.value.message_dict
    assert Lease.objects.get(pk=lease.pk).start_date == JAN_1


def test_primary_tenant_and_payers_on_an_issued_lease_need_activate(owner, unit, tenant):
    agent = fresh(add_member(owner.organization, "leasing_agent", all_properties=True))
    other = tenant_services.create_tenant(owner, name="Otieno", phone="0722000111")
    lease = draft(owner, unit, [tenant, other])
    services.set_primary_tenant(agent, lease, other)
    payer = services.add_payer(agent, lease, phone="0799111222")
    activate(lease)
    assert not services.can_manage_parties(agent, lease) and services.can_manage_parties(owner, lease)
    with pytest.raises(PermissionDenied):
        services.set_primary_tenant(agent, lease, tenant)
    with pytest.raises(PermissionDenied):
        services.add_payer(agent, lease, phone="0799333444")
    with pytest.raises(PermissionDenied):
        services.remove_payer(agent, payer)
    services.set_primary_tenant(owner, lease, tenant)
    services.add_payer(owner, lease, phone="0799333444")


def test_payers(owner, unit, tenant):
    lease = draft(owner, unit, [tenant])
    payer = services.add_payer(owner, lease, phone="0799 111 222", name="Employer")
    assert payer.phone == "+254799111222"
    with pytest.raises(ValidationError):
        services.add_payer(owner, lease, phone="+254799111222")
    with pytest.raises(ValidationError):
        services.add_payer(owner, lease, phone="0712345678")  # the tenant's own phone
    with pytest.raises(ValidationError):
        services.add_payer(owner, lease, phone="12")
    services.remove_payer(owner, payer)
    assert not lease.payers.exists()


# ---------------------------------------------------------------------------
# D-016: no overlapping active leases
# ---------------------------------------------------------------------------


def test_database_refuses_overlapping_leases(owner, unit, tenant):
    first = activate(draft(owner, unit, [tenant], start=D(2026, 1, 1), end=D(2026, 12, 31)))
    second = draft(owner, unit, [tenant], start=D(2026, 12, 31), end=None)
    assert services.overlapping_lease(second) == first
    with pytest.raises(IntegrityError), transaction.atomic():
        Lease.objects.filter(pk=second.pk).update(status=Lease.Status.ACTIVE)
    # The day after is fine.
    services.update_draft_lease(owner, second, start_date=D(2027, 1, 1))
    assert services.overlapping_lease(second) is None
    activate(second)


def test_open_ended_active_lease_blocks_later_ones(owner, unit, tenant):
    activate(draft(owner, unit, [tenant], start=D(2026, 1, 1), end=None))
    later = draft(owner, unit, [tenant], start=D(2030, 1, 1), end=D(2030, 12, 31))
    with pytest.raises(IntegrityError), transaction.atomic():
        activate(later)


def test_overlap_is_per_unit(owner, prop, unit, tenant):
    other_unit = property_services.create_unit(owner, prop, code="A2")
    activate(draft(owner, unit, [tenant]))
    activate(draft(owner, other_unit, [tenant]))


# ---------------------------------------------------------------------------
# Occupancy, tenant status and visibility
# ---------------------------------------------------------------------------


def test_occupancy(owner, prop, unit, tenant):
    lease = draft(owner, unit, [tenant], start=TODAY - datetime.timedelta(days=10), end=None)
    assert unit.get_effective_status_display() == "Available"
    activate(lease)
    assert Unit.objects.get(pk=unit.pk).get_effective_status_display() == "Occupied"
    annotated = services.with_occupancy(Unit.objects.filter(property=prop)).get(pk=unit.pk)
    assert annotated.is_occupied is True


def test_future_lease_does_not_occupy(owner, unit, tenant):
    activate(draft(owner, unit, [tenant], start=TODAY + datetime.timedelta(days=5), end=None))
    assert unit.get_effective_status_display() == "Available"


def test_sync_tenant_status(owner, unit, tenant):
    lease = draft(owner, unit, [tenant])
    assert services.sync_tenant_status(tenant) == Tenant.Status.PROSPECT
    activate(lease)
    assert services.sync_tenant_status(tenant) == Tenant.Status.ACTIVE
    assert Tenant.objects.get(pk=tenant.pk).status == Tenant.Status.ACTIVE
    Lease.objects.filter(pk=lease.pk).update(status=Lease.Status.ENDED)
    assert services.sync_tenant_status(tenant) == Tenant.Status.FORMER


def test_scoped_member_sees_tenants_on_their_leases(owner, prop, unit, tenant):
    caretaker = fresh(add_member(owner.organization, "caretaker", properties=[prop]))
    assert not tenant_services.visible_tenants(caretaker).filter(pk=tenant.pk).exists()
    draft(owner, unit, [tenant])
    assert tenant_services.visible_tenants(caretaker).filter(pk=tenant.pk).exists()
    elsewhere = fresh(add_member(owner.organization, "caretaker", properties=[make_property(owner.organization)]))
    assert not tenant_services.visible_tenants(elsewhere).filter(pk=tenant.pk).exists()


def test_visible_leases_follow_property_scope(owner, prop, unit, tenant):
    lease = draft(owner, unit, [tenant])
    inside = fresh(add_member(owner.organization, "viewer", properties=[prop]))
    outside = fresh(add_member(owner.organization, "viewer", properties=[make_property(owner.organization)]))
    assert list(services.visible_leases(inside)) == [lease]
    assert not services.visible_leases(outside).exists()
    assert not services.visible_leases(make_org()).exists()


# ---------------------------------------------------------------------------
# Guards on units and properties
# ---------------------------------------------------------------------------


def test_unit_with_open_lease_cannot_be_archived(owner, prop, unit, tenant):
    lease = draft(owner, unit, [tenant])
    with pytest.raises(ValidationError):
        property_services.archive_unit(owner, unit)
    with pytest.raises(ValidationError):
        property_services.archive_property(owner, prop)
    services.delete_draft_lease(owner, lease)
    property_services.archive_unit(owner, unit)


def test_unit_code_frozen_once_lease_issued(owner, unit, tenant):
    lease = draft(owner, unit, [tenant])
    property_services.update_unit(owner, unit, code="A1X")  # drafts don't count
    activate(lease)
    with pytest.raises(ValidationError) as e:
        property_services.update_unit(owner, unit, code="A9")
    assert "code" in e.value.message_dict
    property_services.update_unit(owner, Unit.objects.get(pk=unit.pk), type_label="2 bedroom")
