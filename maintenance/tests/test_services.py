"""Maintenance requests (D-068): reporting, triage, the job's life, tenant updates and costs."""

import datetime

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from accounts.tests.factories import add_member, make_org, make_property
from audit.models import AuditEvent
from expenses import services as expense_services
from expenses.models import Expense
from leases import services as lease_services
from maintenance import services
from maintenance.models import MaintenancePhoto, MaintenanceRequest, MaintenanceUpdate
from notifications.models import Message
from properties import services as property_services
from reports.tests.test_income import make_lease

from .conftest import NOW, jpeg, portal_user, report

pytestmark = pytest.mark.django_db

Status = MaintenanceRequest.Status
Priority = MaintenanceRequest.Priority
H = datetime.timedelta(hours=1)
DAY = datetime.timedelta(days=1)
PAID = datetime.date(2026, 3, 2)


def repairs(org):
    return expense_services.categories(org).get(name="Repairs and maintenance")


# --- reporting --------------------------------------------------------------


def test_a_report_gets_a_number_a_due_time_and_a_history(owner, prop, unit):
    req = report(owner, prop, unit=unit, description="Water under the sink", photos=[jpeg()])
    assert req.number == "MNT-2026-000001"
    assert (req.status, req.priority, req.due_at, req.created_at) == (Status.NEW, Priority.NORMAL, NOW + 7 * DAY, NOW)
    assert req.where == "A1" and req.lease is None and req.tenant is None
    assert [u.kind for u in req.updates.all()] == ["REPORTED"]
    assert req.photos.count() == 1 and not req.photos.get().shared_with_tenant
    assert AuditEvent.objects.filter(action="maintenance.report").count() == 1
    assert report(owner, prop).number == "MNT-2026-000002"


@pytest.mark.parametrize("priority, after", [("EMERGENCY", 24 * H), ("HIGH", 3 * DAY), ("LOW", 30 * DAY)])
def test_priority_sets_the_due_time(owner, prop, priority, after):
    assert report(owner, prop, priority=priority).due_at == NOW + after


def test_common_areas_have_no_unit(owner, prop):
    req = report(owner, prop, title="Gate motor broken")
    assert req.unit is None and req.where == "Common areas"


def test_report_checks_the_title_and_unit(owner, prop, org):
    stray = property_services.create_unit(owner, make_property(org), code="Z9")
    with pytest.raises(ValidationError) as exc:
        report(owner, prop, title="  ", unit=stray)
    assert set(exc.value.error_dict) == {"title", "unit"}
    assert not MaintenanceRequest.objects.exists()


def test_from_the_tenant_ties_it_to_the_lease_and_tells_them(owner, prop, unit, lease, tenant):
    req = report(owner, prop, unit=unit, from_tenant=True)
    assert (req.lease, req.tenant) == (lease, tenant)
    sent = Message.objects.filter(type="maintenance_update", tenant=tenant)
    assert sent.exists() and all(m.dedupe_key == f"maintenance_update:{req.pk}:received" for m in sent)


def test_from_the_tenant_needs_a_tenant(owner, prop):
    with pytest.raises(ValidationError) as exc:
        report(owner, prop, from_tenant=True)
    assert "from_tenant" in exc.value.error_dict


def test_staff_who_assign_hear_about_new_requests(owner, org, prop):
    boss = add_member(org, "maintenance_manager", properties=[prop])
    elsewhere = add_member(org, "maintenance_manager", properties=[make_property(org)])
    caretaker = add_member(org, "caretaker", properties=[prop])
    report(caretaker, prop)
    told = set(Message.objects.filter(type="maintenance_reported").values_list("user_id", flat=True))
    assert boss.user_id in told and owner.user_id in told
    assert elsewhere.user_id not in told and caretaker.user_id not in told


def test_reporting_needs_the_capability_and_scope(org, prop):
    with pytest.raises(PermissionDenied):
        report(add_member(org, "accountant", all_properties=True), prop)
    with pytest.raises(PermissionDenied):
        report(add_member(org, "caretaker", properties=[make_property(org)]), prop)
    with pytest.raises(PermissionDenied):
        report(make_org(), prop)


def test_at_most_ten_photos(owner, prop):
    req = report(owner, prop, photos=[jpeg() for _ in range(10)])
    with pytest.raises(ValidationError):
        services.add_note(owner, req, text="", photos=[jpeg()], now=NOW)
    assert MaintenancePhoto.objects.count() == 10


# --- who sees what ----------------------------------------------------------


def test_staff_with_view_assigned_see_only_their_jobs(owner, org, prop):
    fundi = add_member(org, "maintenance_staff", properties=[prop])
    mine, other = report(owner, prop, title="Mine"), report(owner, prop, title="Other")
    mine = services.assign(owner, mine, user=fundi.user, now=NOW)
    assert list(services.visible_requests(fundi)) == [mine]
    assert services.sees(fundi, mine) and not services.sees(fundi, other)
    with pytest.raises(PermissionDenied):
        services.act(fundi, other, "start", now=NOW)


def test_a_manager_sees_only_their_properties(owner, org, prop):
    req = report(owner, prop)
    manager = add_member(org, "manager", properties=[make_property(org)])
    assert not services.visible_requests(manager).exists() and not services.sees(manager, req)
    assert not services.visible_requests(make_org()).exists()


# --- assigning --------------------------------------------------------------


def test_assigning_moves_it_on_and_tells_the_assignee_and_supplier(owner, org, prop, fundi):
    staff = add_member(org, "maintenance_staff", properties=[prop])
    req = report(owner, prop, priority="EMERGENCY")
    req = services.assign(owner, req, user=staff.user, supplier=fundi, now=NOW + H)
    assert (req.status, req.assigned_to, req.supplier, req.assigned_at) == (Status.ASSIGNED, staff.user, fundi,
                                                                             NOW + H)
    assert Message.objects.filter(type="maintenance_assigned", user=staff.user).count() == 1
    sms = Message.objects.get(type="maintenance_supplier_job")
    assert sms.to == "+254722000111" and req.number in sms.body
    # Saving the same assignment again says nothing new.
    services.assign(owner, req, user=staff.user, supplier=fundi, now=NOW + 2 * H)
    assert Message.objects.filter(type__in=["maintenance_assigned", "maintenance_supplier_job"]).count() == 2
    assert req.updates.filter(kind="ASSIGNED").count() == 1


def test_supplier_sms_is_optional(owner, prop, fundi):
    services.assign(owner, report(owner, prop), supplier=fundi, sms_supplier=False, now=NOW)
    assert not Message.objects.filter(type="maintenance_supplier_job").exists()


def test_only_staff_who_could_see_the_job_can_take_it(owner, org, prop):
    req = report(owner, prop)
    accountant = add_member(org, "accountant", all_properties=True)
    outsider = add_member(org, "maintenance_staff", properties=[make_property(org)])
    for m in (accountant, outsider):
        with pytest.raises(ValidationError):
            services.assign(owner, req, user=m.user, now=NOW)
    with pytest.raises(ValidationError):
        services.assign(owner, req, now=NOW)


def test_a_caretaker_cannot_assign(org, prop):
    caretaker = add_member(org, "caretaker", properties=[prop])
    req = report(caretaker, prop)
    with pytest.raises(PermissionDenied):
        services.assign(caretaker, req, user=caretaker.user, now=NOW)


def test_a_new_priority_resets_the_due_time(owner, prop):
    req = report(owner, prop)
    req = services.set_priority(owner, req, priority="EMERGENCY", now=NOW + DAY)
    assert req.due_at == NOW + DAY + 24 * H
    req = services.set_priority(owner, req, priority="EMERGENCY", due_at=NOW + 5 * DAY, now=NOW + DAY)
    assert req.due_at == NOW + 5 * DAY
    assert req.updates.filter(kind="PRIORITY").count() == 2


# --- the job's life ---------------------------------------------------------


def test_the_full_life_of_a_job(owner, prop, unit, tenant):
    req = report(owner, prop, unit=unit, from_tenant=True)
    req = services.act(owner, req, "start", now=NOW + H)
    assert (req.status, req.started_at) == (Status.IN_PROGRESS, NOW + H)
    req = services.act(owner, req, "hold", note="Waiting for a new tap", now=NOW + 2 * H)
    req = services.act(owner, req, "start", now=NOW + 3 * H)
    assert req.started_at == NOW + H
    req = services.act(owner, req, "done", now=NOW + 4 * H)
    req = services.act(owner, req, "close", now=NOW + 5 * H)
    assert (req.status, req.done_at, req.closed_at, req.closed_by) == (Status.CLOSED, NOW + 4 * H, NOW + 5 * H,
                                                                         owner.user)
    req = services.act(owner, req, "reopen", note="Still dripping", now=NOW + DAY)
    assert (req.status, req.done_at, req.closed_at) == (Status.NEW, None, None)
    services.act(owner, req, "done", now=NOW + 2 * DAY)
    # "Fixed" goes to the tenant once, however often the job is marked done.
    keys = set(Message.objects.filter(type="maintenance_update", tenant=tenant).values_list("dedupe_key", flat=True))
    assert keys == {f"maintenance_update:{req.pk}:received", f"maintenance_update:{req.pk}:done"}
    statuses = list(req.updates.filter(kind="STATUS").values_list("to_status", flat=True))
    assert statuses == ["IN_PROGRESS", "ON_HOLD", "IN_PROGRESS", "DONE", "CLOSED", "NEW", "DONE"]


def test_reopening_an_assigned_job_goes_back_to_assigned(owner, prop, fundi):
    req = services.assign(owner, report(owner, prop), supplier=fundi, sms_supplier=False, now=NOW)
    req = services.act(owner, req, "close", now=NOW)
    assert services.act(owner, req, "reopen", note="Came back", now=NOW).status == Status.ASSIGNED


@pytest.mark.parametrize("action", ["hold", "cancel"])
def test_hold_and_cancel_need_a_reason(owner, prop, action):
    req = services.assign(owner, report(owner, prop), user=owner.user, now=NOW)
    with pytest.raises(ValidationError) as exc:
        services.act(owner, req, action, note="  ", now=NOW)
    assert "note" in exc.value.error_dict


def test_actions_only_from_the_right_status(owner, prop):
    req = services.act(owner, report(owner, prop), "cancel", note="Reported twice", now=NOW)
    assert (req.status, req.cancel_reason) == (Status.CANCELLED, "Reported twice")
    for action in ("start", "done", "close", "reopen"):
        with pytest.raises(ValidationError):
            services.act(owner, req, action, note="x", now=NOW)
    assert services.allowed_actions(owner, req) == []


def test_maintenance_staff_update_but_do_not_close(owner, org, prop):
    staff = add_member(org, "maintenance_staff", properties=[prop])
    req = services.assign(owner, report(owner, prop), user=staff.user, now=NOW)
    assert services.allowed_actions(staff, req) == ["start", "hold", "done"]
    req = services.act(staff, req, "done", now=NOW)
    with pytest.raises(PermissionDenied):
        services.act(staff, req, "close", now=NOW)


def test_cancelling_tells_the_tenant(owner, prop, unit, tenant):
    req = report(owner, prop, unit=unit, from_tenant=True)
    services.act(owner, req, "cancel", note="Fixed by the tenant", now=NOW)
    assert Message.objects.filter(type="maintenance_update", tenant=tenant,
                                  dedupe_key=f"maintenance_update:{req.pk}:cancelled").exists()


# --- notes ------------------------------------------------------------------


def test_only_shared_notes_reach_the_tenant(owner, prop, unit, tenant):
    req = report(owner, prop, unit=unit, from_tenant=True)
    before = Message.objects.filter(type="maintenance_update").count()
    services.add_note(owner, req, text="Fundi says the pipe is rusty", now=NOW)
    assert Message.objects.filter(type="maintenance_update").count() == before
    note = services.add_note(owner, req, text="The plumber comes tomorrow at 10", share=True, now=NOW)
    assert note.shared_with_tenant
    assert Message.objects.filter(dedupe_key=f"maintenance_update:{req.pk}:note:{note.pk}").exists()


def test_a_note_needs_text_or_photos(owner, prop):
    with pytest.raises(ValidationError):
        services.add_note(owner, report(owner, prop), text=" ", now=NOW)


def test_sharing_without_a_tenant_shares_nothing(owner, prop):
    note = services.add_note(owner, report(owner, prop), text="Done soon", share=True, now=NOW)
    assert not note.shared_with_tenant


# --- costs ------------------------------------------------------------------


def test_a_cost_is_an_expense_linked_to_the_request(owner, org, prop, fundi):
    req = report(owner, prop)
    e = services.record_cost(owner, req, category=repairs(org), description="New tap", amount="1500",
                             paid_on=PAID, method="CASH", supplier=fundi)
    assert (e.maintenance_request, e.property, e.status) == (req, prop, Expense.Status.APPROVED)
    manager = add_member(org, "maintenance_manager", properties=[prop])
    services.record_cost(manager, req, category=repairs(org), description="Labour", amount="800",
                         paid_on=PAID, method="CASH")
    costs = services.costs(req)
    assert (costs.approved, costs.waiting, len(costs.expenses)) == (1500, 800, 2)
    assert services.cost_totals(MaintenanceRequest.objects.all()) == {req.pk: 1500}
    assert req.updates.filter(kind="COST").count() == 2


def test_costs_need_both_capabilities(org, prop):
    caretaker = add_member(org, "caretaker", properties=[prop])
    req = report(caretaker, prop)
    assert not services.can_record_cost(caretaker, req) and not services.can_see_costs(caretaker, req)
    with pytest.raises(PermissionDenied):
        services.record_cost(caretaker, req, category=repairs(org), description="x", amount="1",
                             paid_on=PAID, method="CASH")


def test_an_expense_cannot_point_at_a_repair_on_another_property(owner, org, prop):
    req = report(owner, prop)
    with pytest.raises(ValidationError):
        expense_services.record_expense(owner, make_property(org), category=repairs(org), description="x",
                                        amount="1", paid_on=PAID, method="CASH", maintenance_request=req)


# --- lists ------------------------------------------------------------------


def test_summary_and_home_lists(owner, org, prop):
    staff = add_member(org, "maintenance_staff", properties=[prop])
    late = report(owner, prop, priority="EMERGENCY")
    mine = services.assign(owner, report(owner, prop), user=staff.user, now=NOW)
    services.act(owner, report(owner, prop), "close", now=NOW + 2 * DAY)
    now = NOW + 2 * DAY
    s = services.summary(services.visible_requests(owner), now=now)
    assert (s.open, s.overdue, s.closed_recently, s.avg_days_to_close) == (2, 1, 1, 2.0)
    assert list(services.to_assign(owner)) == [late]
    assert list(services.my_jobs(staff)) == [mine]
    assert list(services.overdue(owner, now)) == [late]
    assert not services.overdue(staff, now).exists()


def test_open_on_unit_leaves_out_closed_ones(owner, prop, unit):
    open_ = report(owner, prop, unit=unit)
    services.act(owner, report(owner, prop, unit=unit), "close", now=NOW)
    assert list(services.open_on_unit(owner, unit)) == [open_]


# --- portal -----------------------------------------------------------------


def test_a_tenant_reports_from_the_portal(owner, prop, lease, tenant):
    user = portal_user(owner, lease)
    req = services.portal_report(user, lease, title="No water", emergency=True, photos=[jpeg()], now=NOW)
    assert (req.source, req.priority, req.lease, req.tenant, req.unit) == ("PORTAL", "EMERGENCY", lease, tenant,
                                                                            lease.unit)
    assert req.photos.get().shared_with_tenant
    assert Message.objects.filter(type="maintenance_reported", user=owner.user).exists()
    comment = services.portal_comment(user, req, text="I am home after 5", now=NOW)
    assert comment.kind == "COMMENT" and comment.shared_with_tenant


def test_portal_limits(owner, prop, lease):
    user = portal_user(owner, lease)
    with pytest.raises(ValidationError):
        services.portal_report(user, lease, title="Leak", photos=[jpeg() for _ in range(4)], now=NOW)
    for i in range(services.MAX_PORTAL_PER_DAY):
        services.portal_report(user, lease, title=f"Problem {i}", now=NOW)
    with pytest.raises(ValidationError):
        services.portal_report(user, lease, title="One more", now=NOW)
    assert services.portal_report(user, lease, title="Next day", now=NOW + DAY)


def test_the_portal_only_takes_the_users_active_lease(owner, prop, lease):
    user = portal_user(owner, lease)
    neighbour = make_lease(owner, prop, code="B7")
    with pytest.raises(PermissionDenied):
        services.portal_report(user, neighbour, title="Not mine", now=NOW)
    lease_services.end_lease(owner, lease, ended_on=datetime.date(2025, 6, 30), reason="Moved")
    lease.refresh_from_db()
    with pytest.raises(PermissionDenied):
        services.portal_report(user, lease, title="After moving out", now=NOW)


def test_comments_only_on_the_users_open_requests(owner, prop, lease):
    user = portal_user(owner, lease)
    req = services.portal_report(user, lease, title="Leak", now=NOW)
    neighbour = portal_user(owner, make_lease(owner, prop, code="C3"))
    with pytest.raises(PermissionDenied):
        services.portal_comment(neighbour, req, text="Hi", now=NOW)
    req = services.act(owner, req, "close", now=NOW)
    with pytest.raises(ValidationError):
        services.portal_comment(user, req, text="Thanks", now=NOW)
    assert not MaintenanceUpdate.objects.filter(kind="COMMENT").exists()
