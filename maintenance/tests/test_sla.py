"""Maintenance targets and supplier performance (D-074)."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.urls import reverse

from accounts.tests.factories import add_member, make_property
from accounts.tests.test_isolation import login
from audit.models import AuditEvent
from expenses import services as expense_services
from expenses.models import Supplier
from maintenance import services
from maintenance.models import MaintenanceRequest, MaintenanceTarget
from notifications.models import Message

from .conftest import NOW, report

pytestmark = pytest.mark.django_db

Status = MaintenanceRequest.Status
H = datetime.timedelta(hours=1)
DAY = datetime.timedelta(days=1)


# --- targets -------------------------------------------------------------------


def test_default_targets(owner, prop):
    req = report(owner, prop)
    assert (req.respond_by, req.due_at) == (NOW + 2 * DAY, NOW + 7 * DAY)
    req = report(owner, prop, priority="EMERGENCY")
    assert (req.respond_by, req.due_at) == (NOW + 4 * H, NOW + 24 * H)


def test_the_organization_sets_its_own_targets(owner, org, prop):
    services.save_targets(owner, {"EMERGENCY": (2, 12), "LOW": (168, 720)})
    t = MaintenanceTarget.objects.get(organization=org)  # LOW is the default, so no row
    assert (t.priority, t.respond_hours, t.fix_hours) == ("EMERGENCY", 2, 12)
    assert services.targets(org)["EMERGENCY"].custom and not services.targets(org)["LOW"].custom
    req = report(owner, prop, priority="EMERGENCY")
    assert (req.respond_by, req.due_at) == (NOW + 2 * H, NOW + 12 * H)
    assert AuditEvent.objects.filter(action="maintenance.targets").count() == 1
    # Back to the default removes the organization's row.
    services.save_targets(owner, {"EMERGENCY": (4, 24)})
    assert not MaintenanceTarget.objects.exists()


@pytest.mark.parametrize("respond, fix", [(0, 24), (48, 24), (1, 366 * 24)])
def test_targets_are_checked(owner, respond, fix):
    with pytest.raises(ValidationError) as exc:
        services.save_targets(owner, {"HIGH": (respond, fix)})
    assert "HIGH" in exc.value.message_dict


def test_only_organization_managers_set_targets(org, prop):
    with pytest.raises(PermissionDenied):
        services.save_targets(add_member(org, "manager", properties=[prop]), {"HIGH": (2, 24)})


def test_existing_times_stay_when_targets_change(owner, prop):
    req = report(owner, prop)
    services.save_targets(owner, {"NORMAL": (1, 24)})
    req.refresh_from_db()
    assert req.respond_by == NOW + 2 * DAY


# --- setting the times --------------------------------------------------------------


def test_a_new_priority_resets_both_times(owner, prop):
    req = services.set_priority(owner, report(owner, prop), priority="HIGH", now=NOW + H)
    assert (req.respond_by, req.due_at, req.due_moved) == (NOW + H + DAY, NOW + H + 3 * DAY, 0)


def test_respond_by_stays_once_started(owner, prop):
    req = services.act(owner, report(owner, prop), "start", now=NOW + H)
    req = services.set_priority(owner, req, priority="EMERGENCY", now=NOW + 2 * H)
    assert req.respond_by == NOW + 2 * DAY and req.due_at == NOW + 26 * H


def test_moving_the_due_time_by_hand_is_counted(owner, prop):
    req = report(owner, prop)
    req = services.set_priority(owner, req, priority="NORMAL", due_at=NOW + 10 * DAY, now=NOW)
    assert req.due_moved == 1 and req.respond_by == NOW + 2 * DAY
    req = services.set_priority(owner, req, priority="NORMAL", due_at=NOW + 10 * DAY, now=NOW)
    assert req.due_moved == 1  # nothing changed


# --- met or missed ------------------------------------------------------------------


def test_states(owner, prop):
    req = report(owner, prop)
    assert (req.response_state(NOW + H), req.fix_state(NOW + H)) == ("", "")
    assert (req.response_state(NOW + 3 * DAY), req.fix_state(NOW + 8 * DAY)) == ("late", "late")
    req = services.act(owner, req, "start", now=NOW + 3 * DAY)
    req = services.act(owner, req, "done", now=NOW + 5 * DAY)
    assert (req.response_state(), req.fix_state()) == ("missed", "met")


def test_done_without_starting_counts_as_responded(owner, prop):
    req = services.act(owner, report(owner, prop), "done", now=NOW + H)
    assert req.responded_at == NOW + H and req.response_state() == "met"


def test_cancelled_is_not_measured(owner, prop):
    req = services.act(owner, report(owner, prop), "cancel", note="Duplicate", now=NOW + H)
    assert (req.response_state(NOW + 30 * DAY), req.fix_state(NOW + 30 * DAY)) == ("", "")


# --- performance --------------------------------------------------------------------------


@pytest.fixture
def jobs(owner, org, prop, fundi):
    """Juma: one on time, one late and reopened, one cancelled. Wanjiku: one still open and late."""
    other = Supplier.objects.create(organization=org, name="Wanjiku Electric", created_by=owner.user)
    staff = add_member(org, "maintenance_staff", properties=[prop])
    on_time = services.assign(owner, report(owner, prop), user=staff.user, supplier=fundi, now=NOW)
    services.act(owner, on_time, "start", now=NOW + H)
    services.act(owner, on_time, "done", now=NOW + DAY)
    late = services.assign(owner, report(owner, prop), supplier=fundi, now=NOW)
    services.act(owner, late, "done", now=NOW + DAY)
    services.act(owner, late, "reopen", note="Still leaking", now=NOW + 2 * DAY)
    services.act(owner, late, "start", now=NOW + 3 * DAY)
    services.act(owner, late, "done", now=NOW + 9 * DAY)
    cancelled = services.assign(owner, report(owner, prop), supplier=fundi, now=NOW)
    services.act(owner, cancelled, "cancel", note="Duplicate", now=NOW + H)
    services.assign(owner, report(owner, prop), supplier=other, now=NOW)
    expense_services.record_expense(owner, prop, category=expense_services.categories(org).first(),
                                    description="Tap", amount="700", paid_on=datetime.date(2026, 3, 2),
                                    method="CASH", maintenance_request=on_time, supplier=fundi)
    return {"staff": staff, "fundi": fundi, "other": other}


def test_performance_by_supplier(owner, jobs):
    rows = services.performance(services.visible_requests(owner), "supplier", now=NOW + 10 * DAY, with_costs=True)
    juma, wanjiku = rows
    assert juma.name == "Juma Plumbing" and wanjiku.name == "Wanjiku Electric"
    assert (juma.jobs, juma.finished, juma.open, juma.reopened) == (2, 2, 0, 1)
    # The second was started only after it was reopened, three days in.
    assert (juma.respond_met, juma.respond_missed) == (1, 1)
    assert (juma.fix_met, juma.fix_missed, juma.fix_on_time) == (1, 1, 50)
    assert juma.cost == 700 and juma.avg_days_to_fix == 5.0
    assert (wanjiku.jobs, wanjiku.open, wanjiku.overdue, wanjiku.fix_missed, wanjiku.respond_on_time) == (
        1, 1, 1, 1, 0)


def test_performance_by_staff(owner, jobs):
    [row] = services.performance(services.visible_requests(owner), "assigned_to", now=NOW + 10 * DAY)
    assert row.name == str(jobs["staff"].user) and row.fix_on_time == 100 and row.cost is None


def test_summary_share_fixed_on_time(owner, jobs):
    assert services.summary(services.visible_requests(owner), NOW + 10 * DAY).fixed_on_time == 50


# --- alerts ------------------------------------------------------------------------------------


def test_late_alerts_go_once_to_the_assignee_and_assigners(owner, org, prop):
    staff = add_member(org, "maintenance_staff", properties=[prop])
    req = services.assign(owner, report(owner, prop, priority="EMERGENCY"), user=staff.user, now=NOW)
    assert services.send_late_alerts(NOW + H)["late_alerts_sent"] == 0
    counts = services.send_late_alerts(NOW + 5 * H)
    assert counts["late_to_start"] == 1 and counts["late_alerts_sent"] == 2
    told = set(Message.objects.filter(type="maintenance_late").values_list("user_id", flat=True))
    assert told == {owner.user.pk, staff.user.pk}
    assert "not started" in Message.objects.filter(type="maintenance_late").first().body
    assert services.send_late_alerts(NOW + 6 * H)["late_alerts_sent"] == 0
    # Past the fix-by time too: one more each.
    assert services.send_late_alerts(NOW + 25 * H)["late_alerts_sent"] == 2  # "not done"; "not started" was sent
    # Moving the due time can alert again once the new time passes.
    services.set_priority(owner, req, priority="EMERGENCY", due_at=NOW + 30 * H, now=NOW + 26 * H)
    assert services.send_late_alerts(NOW + 27 * H)["late_alerts_sent"] == 0
    assert services.send_late_alerts(NOW + 31 * H)["late_alerts_sent"] == 2


def test_started_or_done_jobs_are_not_late(owner, prop):
    req = report(owner, prop, priority="EMERGENCY")
    services.act(owner, req, "start", now=NOW + H)
    assert services.send_late_alerts(NOW + 5 * H)["late_to_start"] == 0
    services.act(owner, req, "done", now=NOW + 2 * H)
    assert services.send_late_alerts(NOW + 30 * H)["late_to_fix"] == 0


def test_the_alerts_command_is_a_job(owner, prop):
    from io import StringIO

    from core.models import JobRun
    call_command("maintenance_alerts", stdout=StringIO())
    assert JobRun.objects.filter(name="maintenance_alerts", ok=True).exists()


def test_the_supplier_sms_has_the_times(owner, prop, fundi):
    services.assign(owner, report(owner, prop), supplier=fundi, now=NOW)
    body = Message.objects.get(type="maintenance_supplier_job").body
    assert "start by 04/03/2026 09:00" in body and "finish by 09/03/2026 09:00" in body


# --- pages ---------------------------------------------------------------------------------------


def test_targets_page(client, owner, org):
    login(client, owner)
    url = reverse("maintenance:targets")
    assert client.get(url).status_code == 200
    data = {}
    for p in MaintenanceRequest.Priority.values:
        data.update({f"{p}_respond": 1, f"{p}_respond_unit": "days", f"{p}_fix": 7, f"{p}_fix_unit": "days"})
    data.update({"EMERGENCY_respond": 2, "EMERGENCY_respond_unit": "hours", "EMERGENCY_fix": 1})
    assert client.post(url, data).status_code == 302
    assert services.targets(org)["EMERGENCY"].respond == 2 * H
    assert services.targets(org)["EMERGENCY"].fix == DAY and services.targets(org)["LOW"].fix == 7 * DAY
    data.update({"HIGH_respond": 10, "HIGH_fix": 2})
    r = client.post(url, data)
    assert r.status_code == 200 and "cannot be due after the fix" in r.text


def test_targets_page_needs_organization_manage(client, org, prop):
    login(client, add_member(org, "maintenance_manager", properties=[prop]))
    assert client.get(reverse("maintenance:targets")).status_code == 403


def test_performance_page(client, owner, org, prop, jobs):
    login(client, owner)
    r = client.get(reverse("maintenance:performance"), {"days": "365"})
    assert r.status_code == 200 and "Juma Plumbing" in r.text and "Change targets" in r.text
    assert f"supplier={jobs['fundi'].public_id}" in r.text
    assert 'data-chart="chart-kinds"' in r.text and r.context["specs"]["kinds"]["series"][0]["data"]
    csv = client.get(reverse("maintenance:performance"), {"format": "csv", "days": "365"}).content.decode()
    assert "Fixed on time %" in csv and "Juma Plumbing" in csv


def test_performance_scope(client, owner, org, prop, jobs):
    login(client, add_member(org, "maintenance_staff", properties=[prop]))
    assert client.get(reverse("maintenance:performance")).status_code == 403
    login(client, add_member(org, "maintenance_manager", properties=[make_property(org)]))
    r = client.get(reverse("maintenance:performance"))
    assert r.status_code == 200 and "Juma Plumbing" not in r.text


def test_list_filters_and_csv(client, owner, org, prop, jobs):
    login(client, owner)
    url = reverse("maintenance:list")
    r = client.get(url, {"status": "all", "supplier": str(jobs["other"].public_id)})
    assert r.context["page"].paginator.count == 1 and "Jobs of Wanjiku Electric only" in r.text
    assert client.get(url, {"status": "all", "staff": str(jobs["staff"].user.public_id)}
                      ).context["page"].paginator.count == 1
    assert client.get(url, {"late": "1"}).context["page"].paginator.count == 1  # Wanjiku's, not started
    body = client.get(url, {"status": "all", "format": "csv"}).content.decode()
    assert "Respond by" in body and "Fixed on time" in body


def test_detail_shows_both_times(client, owner, prop):
    req = report(owner, prop)
    login(client, owner)
    r = client.get(reverse("maintenance:detail", args=[req.public_id]))
    assert "Start by" in r.text and "Fix by" in r.text and "not started, overdue" in r.text


def test_report_form_shows_the_targets(client, owner, prop):
    services.save_targets(owner, {"EMERGENCY": (2, 12)})
    login(client, owner)
    r = client.get(reverse("maintenance:create"))
    assert "Emergency: start within 2 hours, fix within 12 hours." in r.text


def test_performance_by_kind_priority_and_property(owner, prop, jobs):
    """D-110: the same measures for each kind of job, priority and property; cancelled jobs left out."""
    report(owner, prop, kind="PLUMBING", priority="HIGH")
    qs, now = services.visible_requests(owner), NOW + 10 * DAY
    kinds = services.performance(qs, "kind", now=now, with_costs=True)
    assert [(k.name, k.jobs, k.public_id) for k in kinds] == [("Other", 3, "OTHER"), ("Plumbing", 1, "PLUMBING")]
    assert kinds[0].cost == 700 and kinds[0].cost_per_job == Decimal("233.33") and kinds[1].cost == 0
    assert [p.key for p in services.performance(qs, "priority", now=now)] == ["HIGH", "NORMAL"]
    [place] = services.performance(qs, "property", now=now)
    assert (place.name, place.jobs, place.public_id) == (prop.name, 4, prop.public_id)
    with pytest.raises(ValueError):
        services.performance(qs, "status")


def test_overall_performance(owner, jobs):
    total = services.overall(services.visible_requests(owner), now=NOW + 10 * DAY, with_costs=True)
    assert (total.jobs, total.finished, total.open, total.reopened) == (3, 2, 1, 1)
    assert (total.fix_met, total.fix_missed, total.fix_on_time) == (1, 2, 33)
    assert total.cost == 700 and total.cost_per_job == Decimal("233.33")
    assert services.overall(services.visible_requests(owner), now=NOW).cost is None


def test_the_performance_page_breaks_jobs_down(client, owner, prop, jobs):
    login(client, owner)
    r = client.get(reverse("maintenance:performance"), {"days": "365"})
    assert "By kind of job" in r.text and "By priority" in r.text and "kind=OTHER" in r.text
    assert "Approved cost" in r.text and "a job" in r.text
    csv = client.get(reverse("maintenance:performance"), {"format": "csv", "days": "365"}).content.decode()
    assert "All,All jobs,3," in csv and "Kind,Other,3," in csv and "Cost per job" in csv


def test_performance_page_draws_no_charts_without_jobs(client, owner):
    login(client, owner)
    r = client.get(reverse("maintenance:performance"))
    assert r.status_code == 200 and "data-chart" not in r.text
