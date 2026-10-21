"""Condition reports (D-047): the register, the report lifecycle, comparison and the deposit link."""

import datetime

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from accounts.tests.factories import add_member, make_org, make_property
from audit.models import AuditEvent
from billing import deposits
from billing.models import DepositEntry
from billing.tests.test_invoicing import JAN, make_lease
from inspections import services
from inspections.defaults import default_items
from inspections.models import Condition, ConditionReport, UnitItem
from leases import services as lease_services
from leases.models import Lease

pytestmark = pytest.mark.django_db

IN, OUT = ConditionReport.Kind.MOVE_IN, ConditionReport.Kind.MOVE_OUT
RENT = DepositEntry.DepositType.RENT


def assess(owner, report, condition=Condition.GOOD, **overrides):
    """Gives every line a condition (overrides by item name) and saves."""
    lines = {ln.pk: (overrides.get(ln.name, condition), "") for ln in report.lines.all()}
    return services.save_details(owner, report, inspected_on=report.inspected_on, lines=lines)


def completed(owner, lease, kind, condition=Condition.GOOD, **overrides):
    report = services.start(owner, lease, kind)
    assess(owner, report, condition, **overrides)
    return services.complete(owner, report)


# --- The register -------------------------------------------------------------------------------


def test_starting_a_report_seeds_the_register_and_copies_it(owner, prop):
    lease = make_lease(owner, prop)
    report = services.start(owner, lease, IN)
    expected = default_items(lease.unit.unit_type)
    assert UnitItem.objects.filter(unit=lease.unit).count() == len(expected)
    assert [(ln.area, ln.name) for ln in report.lines.order_by("sort_order")] == expected
    assert report.status == ConditionReport.Status.DRAFT and report.inspected_by == owner.user
    assert AuditEvent.objects.filter(action="inspections.start").exists()


def test_register_edits_do_not_change_reports_already_made(owner, prop):
    lease = make_lease(owner, prop)
    report = services.start(owner, lease, IN)
    item = UnitItem.objects.filter(unit=lease.unit).first()
    services.update_item(owner, item, name="Renamed", area=item.area, quantity=2)
    services.archive_item(owner, UnitItem.objects.filter(unit=lease.unit).last())
    line = report.lines.get(item=item)
    assert line.name != "Renamed" and line.quantity == 1
    assert report.lines.count() == len(default_items(lease.unit.unit_type))


def test_archived_items_are_left_out_of_new_reports(owner, prop):
    lease = make_lease(owner, prop)
    services.fill_defaults(owner, lease.unit)
    gone = UnitItem.objects.filter(unit=lease.unit).first()
    services.archive_item(owner, gone)
    report = services.start(owner, lease, IN)
    assert not report.lines.filter(item=gone).exists()


def test_defaults_fill_only_a_register_that_never_had_items(owner, prop):
    lease = make_lease(owner, prop)
    services.add_item(owner, lease.unit, name="Water tank")
    assert UnitItem.objects.filter(unit=lease.unit).count() == 1  # adding does not pull in the defaults
    assert services.fill_defaults(owner, lease.unit) is False
    report = services.start(owner, lease, IN)
    assert [ln.name for ln in report.lines.all()] == ["Water tank"]


def test_an_item_found_on_the_day_joins_the_report_and_the_register(owner, prop):
    lease = make_lease(owner, prop)
    report = services.start(owner, lease, IN)
    line = services.add_line(owner, report, name="Solar heater", area="Roof", quantity=1)
    assert line.item.unit == lease.unit and UnitItem.objects.filter(name="Solar heater").exists()


# --- Starting -----------------------------------------------------------------------------------


def test_one_report_per_lease_and_kind(owner, prop):
    lease = make_lease(owner, prop)
    services.start(owner, lease, IN)
    with pytest.raises(ValidationError):
        services.start(owner, lease, IN)
    services.start(owner, lease, OUT)  # the other kind is fine


def test_a_cancelled_report_can_be_replaced(owner, prop):
    lease = make_lease(owner, prop)
    first = services.start(owner, lease, IN)
    services.cancel(owner, first, reason="Wrong date")
    first.refresh_from_db()
    assert first.status == ConditionReport.Status.CANCELLED and first.cancel_reason == "Wrong date"
    second = services.start(owner, lease, IN)
    assert services.current(lease, IN) == second


def test_reports_need_an_issued_lease(owner, prop):
    lease = make_lease(owner, prop)
    from properties import services as property_services
    from tenants import services as tenant_services

    unit = property_services.create_unit(owner, prop, code="B2")
    tenant = tenant_services.create_tenant(owner, name="Draft", phone="0712999888")
    draft = lease_services.create_lease(owner, unit=unit, tenants=[tenant], start_date=JAN, end_date=None, rent=1)
    for kind in (IN, OUT):
        assert services.start_problem(draft, kind)
        with pytest.raises(ValidationError):
            services.start(owner, draft, kind)
    lease_services.end_lease(owner, lease, ended_on=timezone.localdate())
    lease.refresh_from_db()
    assert services.start_problem(lease, IN)
    assert services.start_problem(lease, OUT) == ""
    assert services.start(owner, lease, OUT).kind == OUT


def test_the_inspection_date_cannot_be_in_the_future(owner, prop):
    lease = make_lease(owner, prop)
    tomorrow = timezone.localdate() + datetime.timedelta(days=1)
    with pytest.raises(ValidationError):
        services.start(owner, lease, IN, inspected_on=tomorrow)
    report = services.start(owner, lease, IN)
    with pytest.raises(ValidationError):
        services.save_details(owner, report, inspected_on=tomorrow)


# --- Completing ---------------------------------------------------------------------------------


def test_every_line_needs_a_condition_before_completing(owner, prop):
    lease = make_lease(owner, prop)
    report = services.start(owner, lease, IN)
    with pytest.raises(ValidationError):
        services.complete(owner, report)
    assess(owner, report, Condition.NOT_CHECKED)  # "not checked" is an honest answer
    report = services.complete(owner, report)
    assert report.status == ConditionReport.Status.COMPLETED and report.completed_by == owner.user


def test_a_completed_report_is_locked(owner, prop):
    lease = make_lease(owner, prop)
    report = completed(owner, lease, IN)
    with pytest.raises(ValidationError):
        services.save_details(owner, report, inspected_on=report.inspected_on, notes="changed")
    with pytest.raises(ValidationError):
        services.add_line(owner, report, name="Late item")


def test_an_unknown_condition_is_refused(owner, prop):
    lease = make_lease(owner, prop)
    report = services.start(owner, lease, IN)
    line = report.lines.first()
    with pytest.raises(ValidationError):
        services.save_details(owner, report, inspected_on=report.inspected_on, lines={line.pk: ("SHINY", "")})


# --- Comparing ----------------------------------------------------------------------------------


def test_move_out_is_compared_with_move_in(owner, prop):
    lease = make_lease(owner, prop)
    before = completed(owner, lease, IN)
    name = before.lines.first().name
    out = services.start(owner, lease, OUT)
    assess(owner, out, Condition.GOOD, **{name: Condition.DAMAGED})
    assert services.baseline(out) == before
    table = services.rows(out, before)
    assert [r["line"].name for r in table if r["worse"]] == [name]
    assert name in services.deduction_reason(out, table)


def test_better_or_unchecked_is_not_worse():
    assert services.is_worse(Condition.GOOD, Condition.MISSING)
    assert not services.is_worse(Condition.POOR, Condition.FAIR)
    assert not services.is_worse(Condition.GOOD, Condition.NOT_CHECKED)
    assert not services.is_worse(Condition.NOT_CHECKED, Condition.DAMAGED)


def test_a_renewal_compares_with_the_first_move_in_on_the_same_unit(owner, prop):
    first = make_lease(owner, prop)
    move_in = completed(owner, first, IN)
    renewal = lease_services.renew_lease(owner, first, start_date=timezone.localdate())
    renewal = lease_services.activate_lease(owner, renewal)
    out = services.start(owner, renewal, OUT)
    assert services.baseline(out) == move_in


def test_no_baseline_without_a_completed_move_in(owner, prop):
    lease = make_lease(owner, prop)
    services.start(owner, lease, IN)  # still a draft
    assert services.baseline(services.start(owner, lease, OUT)) is None


# --- The deposit --------------------------------------------------------------------------------


def test_a_deduction_cites_the_move_out_report(owner, prop):
    lease = make_lease(owner, prop)
    deposits.record_received(owner, lease, amount=30000, entry_date=JAN)
    completed(owner, lease, IN)
    out = completed(owner, lease, OUT, Condition.DAMAGED)
    assert services.can_deduct(owner, out)
    entry = services.deduct(owner, out, amount=5000, deposit_type=RENT, reason="Broken door")
    assert entry.condition_report == out and deposits.held(lease) == 25000
    with pytest.raises(ValidationError):  # the evidence stays
        services.cancel(owner, out, reason="Oops")


def test_only_a_completed_move_out_of_the_same_lease_can_be_cited(owner, prop):
    lease = make_lease(owner, prop)
    other = make_lease(owner, prop, code="B12")
    deposits.record_received(owner, lease, amount=30000, entry_date=JAN)
    move_in = completed(owner, lease, IN)
    draft_out = services.start(owner, lease, OUT)
    foreign = completed(owner, other, OUT)
    assert not services.can_deduct(owner, move_in) and not services.can_deduct(owner, draft_out)
    for report in (move_in, draft_out, foreign):
        with pytest.raises(ValidationError):
            deposits.deduct(owner, lease, amount=100, reason="x", condition_report=report)


# --- Who may --------------------------------------------------------------------------------------


def test_capabilities_and_property_scope(owner, prop):
    lease = make_lease(owner, prop)
    viewer = add_member(owner.organization, "viewer", all_properties=True)
    elsewhere = add_member(owner.organization, "caretaker", properties=[make_property(owner.organization)])
    caretaker = add_member(owner.organization, "caretaker", properties=[prop])
    for actor in (viewer, elsewhere):
        with pytest.raises(PermissionDenied):
            services.start(actor, lease, IN)
    report = services.start(caretaker, lease, IN)
    assert services.visible_reports(viewer).filter(pk=report.pk).exists()
    assert not services.visible_reports(elsewhere).exists()
    with pytest.raises(PermissionDenied):
        services.start(make_org(), lease, OUT)
    with pytest.raises(PermissionDenied):
        services.add_item(viewer, lease.unit, name="Tap")


def test_the_status_enum_matches_what_start_allows():
    assert set(services.START_STATUSES[IN]) == {Lease.Status.ACTIVE}
    assert Lease.Status.RENEWED not in services.START_STATUSES[OUT]
