"""Lease actions: activate, notice, end/terminate, renew and transfer (doc 11 §7, D-016)."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from accounts.tests.factories import add_member, fresh, make_org, make_property
from audit.models import AuditEvent
from billing.models import ChargeType
from billing.services import ensure_default_charge_types
from leases import services
from leases.models import Lease
from properties import services as property_services
from properties.models import Unit
from tenants import services as tenant_services
from tenants.models import Tenant

pytestmark = pytest.mark.django_db

DAY = datetime.timedelta(days=1)
TODAY = datetime.date.today()
START = TODAY - 100 * DAY
END = TODAY + 30 * DAY


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
def unit_b(owner, prop):
    return property_services.create_unit(owner, prop, code="B1")


@pytest.fixture
def tenant(owner):
    return tenant_services.create_tenant(owner, name="Wanjiku", phone="0712345678")


def draft(actor, unit, tenants, start=START, end=END, rent=15000, **kw):
    return services.create_lease(actor, unit=unit, tenants=tenants, start_date=start, end_date=end, rent=rent, **kw)


def water(org):
    ensure_default_charge_types(org)
    return ChargeType.objects.get(organization=org, category=ChargeType.Category.WATER)


def occupied(unit):
    return Unit.objects.get(pk=unit.pk).get_effective_status_display() == "Occupied"


def status_of(tenant):
    return Tenant.objects.get(pk=tenant.pk).status


# ---------------------------------------------------------------------------
# Activate
# ---------------------------------------------------------------------------


def test_activate_assigns_numbers_in_order(owner, unit, unit_b, tenant):
    first = services.activate_lease(owner, draft(owner, unit, [tenant]))
    second = services.activate_lease(owner, draft(owner, unit_b, [tenant]))
    assert first.status == Lease.Status.ACTIVE and first.activated_at
    assert first.number == f"LSE-{TODAY.year}-000001"
    assert second.number == f"LSE-{TODAY.year}-000002"
    assert status_of(tenant) == Tenant.Status.ACTIVE
    assert occupied(unit)
    assert AuditEvent.objects.filter(action="lease.activate", object_id=str(first.public_id)).exists()
    with pytest.raises(ValidationError):
        services.activate_lease(owner, first)  # already active


def test_activate_refuses_overlap_and_bad_state(owner, unit, tenant):
    services.activate_lease(owner, draft(owner, unit, [tenant]))
    clash = draft(owner, unit, [tenant], start=END, end=None)
    with pytest.raises(ValidationError, match="already has lease"):
        services.activate_lease(owner, clash)
    assert Lease.objects.get(pk=clash.pk).number == ""

    other = property_services.create_unit(owner, unit.property, code="C1")
    past = draft(owner, other, [tenant], start=START, end=TODAY - DAY)
    with pytest.raises(ValidationError, match="end date has passed"):
        services.activate_lease(owner, past)

    archived = tenant_services.create_tenant(owner, name="Gone", phone="0733000111")
    lease = draft(owner, other, [archived], start=TODAY, end=None)
    tenant_services.archive_tenant(owner, archived)
    with pytest.raises(ValidationError, match="archived"):
        services.activate_lease(owner, lease)


def test_activate_needs_the_capability(owner, prop, unit, tenant):
    lease = draft(owner, unit, [tenant])
    agent = fresh(add_member(owner.organization, "leasing_agent", properties=[prop]))
    with pytest.raises(PermissionDenied):
        services.activate_lease(agent, lease)
    with pytest.raises(PermissionDenied):
        services.activate_lease(make_org(), lease)


# ---------------------------------------------------------------------------
# Notice, end and terminate
# ---------------------------------------------------------------------------


def test_notice_to_vacate(owner, unit, tenant):
    lease = services.activate_lease(owner, draft(owner, unit, [tenant], notice_days=30))
    services.give_notice(owner, lease, given_on=TODAY)
    assert lease.notice_given_on == TODAY and lease.move_out_by == TODAY + 30 * DAY
    assert lease.status == Lease.Status.ACTIVE
    with pytest.raises(ValidationError):
        services.give_notice(owner, lease, given_on=TODAY + DAY)
    services.withdraw_notice(owner, lease)
    assert Lease.objects.get(pk=lease.pk).notice_given_on is None
    assert AuditEvent.objects.filter(action="lease.notice_withdraw").exists()


def test_closing_removes_charges_that_never_started(owner, unit, tenant):
    lease = draft(owner, unit, [tenant])
    services.add_charge(owner, lease, charge_type=water(owner.organization), amount=500, active_from=TODAY + 10 * DAY)
    services.activate_lease(owner, lease)
    services.end_lease(owner, lease, ended_on=TODAY - DAY)
    assert not lease.charges.exists()
    event = AuditEvent.objects.filter(action="lease.end").get()
    assert event.changes["charges_removed"][0] == [f"Water 500.00 from {TODAY + 10 * DAY}"]


def test_end_lease_frees_the_unit_and_stops_charges(owner, unit, tenant):
    lease = draft(owner, unit, [tenant])
    charge = services.add_charge(owner, lease, charge_type=water(owner.organization), amount=500)
    services.activate_lease(owner, lease)
    with pytest.raises(ValidationError):
        services.end_lease(owner, lease, ended_on=TODAY + DAY)
    services.end_lease(owner, lease, ended_on=TODAY - DAY)
    assert lease.status == Lease.Status.ENDED and lease.ended_on == TODAY - DAY
    charge.refresh_from_db()
    assert charge.active_to == TODAY - DAY
    assert status_of(tenant) == Tenant.Status.FORMER
    assert not occupied(unit)
    # The unit is free from the day after the move-out, though the contract ran longer.
    services.activate_lease(owner, draft(owner, unit, [tenant], start=TODAY, end=None))
    with pytest.raises(ValidationError):
        services.end_lease(owner, lease, ended_on=TODAY - DAY)  # already closed


def test_terminate_needs_a_reason(owner, unit, tenant):
    lease = services.activate_lease(owner, draft(owner, unit, [tenant]))
    with pytest.raises(ValidationError):
        services.end_lease(owner, lease, ended_on=TODAY, terminate=True)
    services.end_lease(owner, lease, ended_on=TODAY, terminate=True, reason="Rent arrears")
    lease.refresh_from_db()
    assert lease.status == Lease.Status.TERMINATED and lease.end_reason == "Rent arrears"
    # Leaving today still counts as living there today.
    assert occupied(unit) and status_of(tenant) == Tenant.Status.ACTIVE
    assert AuditEvent.objects.filter(action="lease.terminate", object_id=str(lease.public_id)).exists()


def test_ending_needs_the_capability(owner, prop, unit, tenant):
    lease = services.activate_lease(owner, draft(owner, unit, [tenant]))
    agent = fresh(add_member(owner.organization, "leasing_agent", properties=[prop]))
    for call in (lambda: services.end_lease(agent, lease, ended_on=TODAY),
                 lambda: services.give_notice(agent, lease, given_on=TODAY)):
        with pytest.raises(PermissionDenied):
            call()


# ---------------------------------------------------------------------------
# Renew
# ---------------------------------------------------------------------------


def test_renewal_carries_the_lease_over(owner, unit, tenant):
    co = tenant_services.create_tenant(owner, name="Otieno", phone="0722000111")
    lease = draft(owner, unit, [tenant, co], deposit_amount=30000, due_day=5)
    services.add_charge(owner, lease, charge_type=water(owner.organization), amount=500)
    services.add_payer(owner, lease, phone="0799111222", name="Employer")
    services.activate_lease(owner, lease)

    renewal = services.renew_lease(owner, lease, end_date=END + 365 * DAY)
    assert renewal.is_draft and renewal.previous_lease == lease and renewal.unit == unit
    assert renewal.start_date == END + DAY
    assert renewal.rent_on(renewal.start_date) == Decimal("15000")
    assert renewal.due_day == 5 and renewal.deposit_amount == 30000
    assert renewal.primary_tenant == tenant and renewal.lease_tenants.count() == 2
    assert [(c.charge_type.category, c.active_from) for c in renewal.charges.all()] == [("WATER", END + DAY)]
    assert list(renewal.payers.values_list("phone", flat=True)) == ["+254799111222"]
    assert services.overlapping_lease(renewal) is None
    with pytest.raises(ValidationError, match="already exists"):
        services.renew_lease(owner, lease)

    services.activate_lease(owner, renewal)
    lease.refresh_from_db()
    assert lease.status == Lease.Status.RENEWED and lease.ended_on == END
    assert renewal.status == Lease.Status.ACTIVE and renewal.number.endswith("000002")
    # The old lease still covers today, so the unit stays occupied.
    assert occupied(unit) and status_of(tenant) == Tenant.Status.ACTIVE
    assert AuditEvent.objects.filter(action="lease.renewed", object_id=str(lease.public_id)).exists()


def test_early_renewal_cuts_the_old_term(owner, unit, tenant):
    lease = services.activate_lease(owner, draft(owner, unit, [tenant]))
    renewal = services.renew_lease(owner, lease, start_date=TODAY + DAY, end_date=None, rent=17000)
    assert services.overlapping_lease(renewal, ignore=lease) is None
    services.activate_lease(owner, renewal)
    lease.refresh_from_db()
    assert lease.ended_on == TODAY and lease.end_date == END
    assert renewal.rent_on(TODAY + DAY) == 17000


def test_periodic_renewal_needs_a_start(owner, unit, tenant):
    lease = services.activate_lease(owner, draft(owner, unit, [tenant], end=None))
    with pytest.raises(ValidationError):
        services.renew_lease(owner, lease)
    with pytest.raises(ValidationError):
        services.renew_lease(owner, lease, start_date=START)


def test_draft_cannot_be_renewed(owner, unit, tenant):
    with pytest.raises(ValidationError):
        services.renew_lease(owner, draft(owner, unit, [tenant]))


# ---------------------------------------------------------------------------
# Transfer
# ---------------------------------------------------------------------------


def test_transfer_moves_the_tenant(owner, unit, unit_b, tenant):
    unit_b.list_rent = Decimal("20000")
    unit_b.save()
    lease = services.activate_lease(owner, draft(owner, unit, [tenant], deposit_amount=15000))
    with pytest.raises(ValidationError):
        services.transfer_lease(owner, lease, unit=unit, start_date=TODAY)
    new = services.transfer_lease(owner, lease, unit=unit_b, start_date=TODAY)
    assert new.unit == unit_b and new.previous_lease == lease and new.end_date == END
    assert new.rent_on(TODAY) == 20000 and new.deposit_amount == 15000

    services.activate_lease(owner, new)
    lease.refresh_from_db()
    assert lease.status == Lease.Status.ENDED and lease.ended_on == TODAY - DAY
    assert "B1" in lease.end_reason
    assert not occupied(unit) and occupied(unit_b)
    assert status_of(tenant) == Tenant.Status.ACTIVE
    assert AuditEvent.objects.filter(action="lease.transferred", object_id=str(lease.public_id)).exists()


def test_transfer_stays_in_scope(owner, prop, unit, tenant):
    lease = services.activate_lease(owner, draft(owner, unit, [tenant]))
    manager = fresh(add_member(owner.organization, "manager", properties=[prop]))
    elsewhere = property_services.create_unit(owner, make_property(owner.organization), code="Z1")
    with pytest.raises(PermissionDenied):
        services.transfer_lease(manager, lease, unit=elsewhere, start_date=TODAY)
    other_org = make_org()
    foreign = property_services.create_unit(other_org, make_property(other_org.organization), code="F1")
    with pytest.raises(PermissionDenied):
        services.transfer_lease(owner, lease, unit=foreign, start_date=TODAY)


def test_properties_stay_locked_until_move_out(owner, unit, unit_b, tenant):
    lease = services.activate_lease(owner, draft(owner, unit, [tenant]))
    new = services.transfer_lease(owner, lease, unit=unit_b, start_date=TODAY + 10 * DAY)
    services.activate_lease(owner, new)
    # The tenant is still in A1 until the move, so A1 cannot be archived yet.
    with pytest.raises(ValidationError):
        property_services.archive_unit(owner, unit)
