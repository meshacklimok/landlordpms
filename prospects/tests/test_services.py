import datetime

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from accounts.tests.factories import add_member, make_org, make_property
from audit.models import AuditEvent
from leases import services as lease_services
from notifications.models import Message
from properties import services as property_services
from properties.models import Unit
from properties.selectors import is_available_to_let
from prospects import services
from prospects.models import Prospect, ProspectNote, Viewing
from tenants import services as tenant_services
from tenants.models import Tenant

pytestmark = pytest.mark.django_db
Status = Prospect.Status


def later(days=1, hour=10):
    return timezone.localtime().replace(hour=hour, minute=0, second=0, microsecond=0) + datetime.timedelta(days=days)


# ---------------------------------------------------------------------------
# Adding and editing
# ---------------------------------------------------------------------------


def test_create_normalises_and_assigns_the_actor(owner, prospect):
    assert prospect.phone == "+254712345678"
    assert prospect.status == Status.NEW
    assert prospect.assigned_to == owner.user
    assert prospect.created_by == owner.user
    assert prospect.notes.get().kind == ProspectNote.Kind.CREATED
    assert AuditEvent.objects.filter(action="prospects.create").exists()


def test_create_validates(owner, prop):
    with pytest.raises(ValidationError) as exc:
        services.create_prospect(owner, prop, name=" ", phone="12", email="nope")
    assert set(exc.value.message_dict) == {"name", "phone", "email"}


def test_unit_must_belong_to_the_property(owner, prop, org):
    other = make_property(org)
    u = property_services.create_unit(owner, other, code="Z1")
    with pytest.raises(ValidationError) as exc:
        services.create_prospect(owner, prop, name="A", phone="0712000001", unit=u)
    assert "unit" in exc.value.message_dict


def test_assigning_someone_else_notifies_them(owner, prop, agent):
    p = services.create_prospect(owner, prop, name="A", phone="0712000001", assigned_to=agent.user)
    assert p.assigned_to == agent.user
    assert Message.objects.filter(type="prospect_assigned", user=agent.user).count() == 1


def test_cannot_assign_someone_without_access(owner, prop, org):
    elsewhere = add_member(org, "leasing_agent", properties=[make_property(org)])
    caretaker = add_member(org, "caretaker", properties=[prop])
    for m in (elsewhere, caretaker):
        with pytest.raises(ValidationError):
            services.create_prospect(owner, prop, name="A", phone="0712000001", assigned_to=m.user)


def test_scope(owner, prop, org, prospect):
    other_prop = make_property(org)
    agent_elsewhere = add_member(org, "leasing_agent", properties=[other_prop])
    viewer = add_member(org, "viewer", all_properties=True)
    stranger = make_org()
    assert list(services.visible_prospects(owner)) == [prospect]
    assert not services.visible_prospects(agent_elsewhere).exists()
    assert not services.visible_prospects(viewer).exists()
    assert not services.visible_prospects(stranger).exists()
    with pytest.raises(PermissionDenied):
        services.add_note(agent_elsewhere, prospect, text="hi")
    with pytest.raises(PermissionDenied):
        services.create_prospect(stranger, prop, name="A", phone="0712000001")


def test_update_audits_changes(owner, prospect):
    services.update_prospect(owner, prospect, name="Wanjiku K.", phone="0712345678", budget="15000")
    prospect.refresh_from_db()
    assert prospect.name == "Wanjiku K." and prospect.budget == 15000
    event = AuditEvent.objects.get(action="prospects.edit")
    assert set(event.changes) == {"name", "budget", "unit"}


# ---------------------------------------------------------------------------
# Status follows what happened
# ---------------------------------------------------------------------------


def test_note_contact_and_follow_up(owner, prospect):
    services.add_note(owner, prospect, text="Called, wants to view", contacted=True,
                      next_contact_on=datetime.date(2026, 3, 5))
    prospect.refresh_from_db()
    assert prospect.status == Status.CONTACTED
    assert prospect.next_contact_on == datetime.date(2026, 3, 5)
    with pytest.raises(ValidationError):
        services.add_note(owner, prospect, text="  ")
    services.add_note(owner, prospect, text="No answer")  # a blank date clears the follow-up
    prospect.refresh_from_db()
    assert prospect.next_contact_on is None and prospect.status == Status.CONTACTED


def test_viewing_moves_the_status(owner, prospect, unit, agent):
    v = services.book_viewing(owner, prospect, unit=unit, scheduled_at=later(), with_user=agent.user)
    prospect.refresh_from_db()
    assert prospect.status == Status.VIEWING and prospect.contacted_at is not None
    assert Message.objects.filter(type="prospect_viewing", user=agent.user).count() == 1
    services.viewing_outcome(owner, v, Viewing.Status.DONE, outcome="Liked it")
    prospect.refresh_from_db()
    assert prospect.status == Status.VIEWED
    with pytest.raises(ValidationError):
        services.viewing_outcome(owner, v, Viewing.Status.NO_SHOW)


def test_viewing_time_window(owner, prospect, unit):
    for when in (later(days=-8), later(days=91)):
        with pytest.raises(ValidationError) as exc:
            services.book_viewing(owner, prospect, unit=unit, scheduled_at=when)
        assert "scheduled_at" in exc.value.message_dict


def test_no_show_goes_back(owner, prospect, unit):
    v = services.book_viewing(owner, prospect, unit=unit, scheduled_at=later())
    services.viewing_outcome(owner, v, Viewing.Status.NO_SHOW)
    prospect.refresh_from_db()
    assert prospect.status == Status.CONTACTED


# ---------------------------------------------------------------------------
# Holding a unit
# ---------------------------------------------------------------------------


def test_hold_reserves_the_unit(owner, prospect, unit):
    today = timezone.localdate()
    services.hold_unit(owner, prospect, unit=unit, until=today + datetime.timedelta(days=7))
    unit.refresh_from_db()
    prospect.refresh_from_db()
    assert unit.manual_status == Unit.ManualStatus.RESERVED
    assert prospect.status == Status.RESERVED and prospect.held_unit == unit
    assert not is_available_to_let(unit)
    assert services.held_for(unit) == prospect
    assert AuditEvent.objects.filter(action="unit.status", object_id=str(unit.pk)).exists() or \
        AuditEvent.objects.filter(action="unit.status").exists()


def test_hold_limits(owner, prop, prospect, unit, unit2):
    today = timezone.localdate()
    with pytest.raises(ValidationError):
        services.hold_unit(owner, prospect, unit=unit, until=today + datetime.timedelta(days=31))
    with pytest.raises(ValidationError):
        services.hold_unit(owner, prospect, unit=unit, until=today - datetime.timedelta(days=1))
    services.hold_unit(owner, prospect, unit=unit, until=today + datetime.timedelta(days=3))
    other = services.create_prospect(owner, prop, name="B", phone="0712000002")
    with pytest.raises(ValidationError) as exc:
        services.hold_unit(owner, other, unit=unit, until=today + datetime.timedelta(days=3))
    assert "already held" in str(exc.value)


def test_extending_and_moving_a_hold(owner, prospect, unit, unit2):
    today = timezone.localdate()
    services.hold_unit(owner, prospect, unit=unit, until=today + datetime.timedelta(days=3))
    services.hold_unit(owner, prospect, unit=unit, until=today + datetime.timedelta(days=10))
    prospect.refresh_from_db()
    assert prospect.held_until == today + datetime.timedelta(days=10)
    services.hold_unit(owner, prospect, unit=unit2, until=today + datetime.timedelta(days=5))
    unit.refresh_from_db()
    unit2.refresh_from_db()
    assert unit.manual_status == Unit.ManualStatus.NORMAL
    assert unit2.manual_status == Unit.ManualStatus.RESERVED


def test_cannot_hold_an_unavailable_unit(owner, prospect, unit):
    property_services.set_unit_status(owner, unit, Unit.ManualStatus.UNDER_MAINTENANCE)
    with pytest.raises(ValidationError):
        services.hold_unit(owner, prospect, unit=unit, until=timezone.localdate())


def test_release(owner, prospect, unit):
    services.hold_unit(owner, prospect, unit=unit, until=timezone.localdate())
    services.release_unit(owner, prospect)
    unit.refresh_from_db()
    prospect.refresh_from_db()
    assert unit.manual_status == Unit.ManualStatus.NORMAL
    assert prospect.held_unit is None and prospect.status == Status.NEW
    with pytest.raises(ValidationError):
        services.release_unit(owner, prospect)


def test_changing_the_unit_status_by_hand_ends_the_hold(owner, prospect, unit):
    services.hold_unit(owner, prospect, unit=unit, until=timezone.localdate())
    unit.refresh_from_db()
    property_services.set_unit_status(owner, unit, Unit.ManualStatus.UNDER_MAINTENANCE)
    unit.refresh_from_db()
    prospect.refresh_from_db()
    assert prospect.held_unit is None
    assert unit.manual_status == Unit.ManualStatus.UNDER_MAINTENANCE


def test_lease_activation_ends_the_hold(owner, prospect, unit):
    services.hold_unit(owner, prospect, unit=unit, until=timezone.localdate())
    tenant = services.convert(owner, prospect)
    prospect.refresh_from_db()
    assert prospect.status == Status.CONVERTED and prospect.held_unit == unit  # kept until the lease starts
    lease = lease_services.create_lease(owner, unit=unit, tenants=[tenant], start_date=timezone.localdate(),
                                        end_date=None, rent=15000, due_day=5, grace_days=3)
    lease_services.activate_lease(owner, lease)
    prospect.refresh_from_db()
    unit.refresh_from_db()
    assert prospect.held_unit is None and prospect.status == Status.CONVERTED
    assert unit.manual_status == Unit.ManualStatus.NORMAL
    assert prospect.notes.filter(kind=ProspectNote.Kind.RELEASE).exists()


def test_lapsed_holds(owner, prospect, unit):
    today = timezone.localdate()
    services.hold_unit(owner, prospect, unit=unit, until=today)
    assert not services.lapsed_holds(owner, today).exists()
    assert list(services.lapsed_holds(owner, today + datetime.timedelta(days=1))) == [prospect]


# ---------------------------------------------------------------------------
# Closing
# ---------------------------------------------------------------------------


def test_convert_creates_a_tenant(owner, prospect):
    tenant = services.convert(owner, prospect)
    assert tenant.phone == prospect.phone and tenant.name == prospect.name
    prospect.refresh_from_db()
    assert prospect.tenant == tenant and prospect.converted_at is not None
    with pytest.raises(ValidationError):
        services.convert(owner, prospect)


def test_convert_links_an_existing_tenant(owner, prospect):
    existing = tenant_services.create_tenant(owner, name="Wanjiku", phone="0712345678")
    assert services.convert(owner, prospect, tenant=existing) == existing
    assert Tenant.objects.filter(phone=prospect.phone).count() == 1


def test_convert_needs_tenants_manage(org, prop, prospect):
    agent = add_member(org, "leasing_agent", properties=[prop])
    assert services.can_convert(agent, prospect)  # leasing agents add tenants
    viewer = add_member(org, "viewer", all_properties=True)
    assert not services.can_convert(viewer, prospect)


def test_lose_releases_and_cancels(owner, prospect, unit):
    services.hold_unit(owner, prospect, unit=unit, until=timezone.localdate())
    v = services.book_viewing(owner, prospect, unit=unit, scheduled_at=later())
    with pytest.raises(ValidationError):
        services.lose(owner, prospect, reason=Prospect.LostReason.OTHER)
    services.lose(owner, prospect, reason=Prospect.LostReason.PRICE)
    prospect.refresh_from_db()
    v.refresh_from_db()
    unit.refresh_from_db()
    assert prospect.status == Status.LOST and prospect.held_unit is None
    assert v.status == Viewing.Status.CANCELLED
    assert unit.manual_status == Unit.ManualStatus.NORMAL
    with pytest.raises(ValidationError):
        services.hold_unit(owner, prospect, unit=unit, until=timezone.localdate())
    services.reopen(owner, prospect)
    prospect.refresh_from_db()
    assert prospect.status == Status.CONTACTED and prospect.lost_reason == ""


# ---------------------------------------------------------------------------
# Enquiries from the vacancy link
# ---------------------------------------------------------------------------


def test_public_enquiry_creates_and_notifies(owner, org, prop, unit, agent):
    property_services.share_unit(agent, unit)
    unit.refresh_from_db()
    p = services.public_enquiry(unit, name="Otieno", phone="0722111222", message="Can I view Saturday?")
    assert p.source == Prospect.Source.VACANCY_LINK and p.created_by is None
    assert p.assigned_to == agent.user and p.unit == unit
    assert Message.objects.filter(type="prospect_new", user=agent.user).count() == 1
    assert "Saturday" in p.notes.get().text
    again = services.public_enquiry(unit, name="Otieno", phone="+254722111222")
    assert again == p and p.notes.count() == 2
    assert Prospect.objects.count() == 1
    assert Message.objects.filter(type="prospect_new", user=agent.user).count() == 2


def test_enquiry_without_a_sharer_goes_to_staff(owner, org, prop, unit, agent):
    property_services.share_unit(owner, unit)
    owner.is_active = False
    owner.save(update_fields=["is_active"])
    unit.refresh_from_db()
    p = services.public_enquiry(unit, name="Otieno", phone="0722111222")
    assert p.assigned_to is None
    assert Message.objects.filter(type="prospect_new", user=agent.user).exists()


def test_enquiry_validates(unit):
    with pytest.raises(ValidationError):
        services.public_enquiry(unit, name="", phone="abc")


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------


def test_follow_ups_and_summary(owner, prop, prospect):
    today = timezone.localdate()
    other = services.create_prospect(owner, prop, name="B", phone="0712000002")
    services.add_note(owner, other, text="Call next week", contacted=True,
                      next_contact_on=today + datetime.timedelta(days=7))
    assert list(services.follow_ups_due(owner, today)) == [prospect]
    assert other in services.follow_ups_due(owner, today + datetime.timedelta(days=7))
    services.lose(owner, other, reason=Prospect.LostReason.PRICE)
    services.convert(owner, prospect)
    s = services.summary(services.visible_prospects(owner), today)
    assert (s.open, s.to_call, s.converted, s.lost, s.conversion) == (0, 0, 1, 1, 50)
