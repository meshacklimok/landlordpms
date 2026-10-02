"""Maintenance pages (D-068): the list, reporting, the request page, costs, photos and the portal."""

import datetime

import pytest
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from expenses import services as expense_services
from expenses.models import Expense
from maintenance import services
from maintenance.models import MaintenanceRequest
from reports import home
from reports.tests.test_income import make_lease

from .conftest import NOW, jpeg, portal_user, report

pytestmark = pytest.mark.django_db

Status = MaintenanceRequest.Status


def detail(req):
    return reverse("maintenance:detail", args=[req.public_id])


# --- list -------------------------------------------------------------------


def test_the_list_shows_open_requests_and_filters(client, owner, prop, unit):
    leak = report(owner, prop, unit=unit, title="Kitchen sink is leaking")
    gate = report(owner, prop, title="Gate motor broken", priority="EMERGENCY")
    services.act(owner, report(owner, prop, title="Old bulb"), "close", now=NOW)
    login(client, owner)
    page = client.get(reverse("maintenance:list")).content.decode()
    assert leak.number in page and gate.number in page and "Old bulb" not in page
    page = client.get(reverse("maintenance:list"), {"priority": "EMERGENCY"}).content.decode()
    assert gate.number in page and leak.number not in page
    page = client.get(reverse("maintenance:list"), {"unit": str(unit.public_id)}).content.decode()
    assert leak.number in page and gate.number not in page
    assert "Old bulb" in client.get(reverse("maintenance:list"), {"status": "all"}).content.decode()


def test_the_list_csv(client, owner, org, prop):
    req = report(owner, prop)
    expense_services.record_expense(owner, prop, category=expense_services.categories(org).first(),
                                    description="Tap", amount="700", paid_on=datetime.date(2026, 3, 2),
                                    method="CASH", maintenance_request=req)
    login(client, owner)
    response = client.get(reverse("maintenance:list"), {"format": "csv"})
    body = response.content.decode()
    assert response["Content-Type"].startswith("text/csv")
    assert "Approved cost" in body and req.number in body and "700" in body


def test_who_can_open_the_list(client, org, prop):
    login(client, add_member(org, "accountant", all_properties=True))
    assert client.get(reverse("maintenance:list")).status_code == 403
    login(client, add_member(org, "maintenance_staff", properties=[prop]))
    assert client.get(reverse("maintenance:list")).status_code == 200
    assert client.get(reverse("maintenance:list"), {"format": "csv"}).status_code == 403


def test_maintenance_staff_list_only_their_jobs(client, owner, org, prop):
    staff = add_member(org, "maintenance_staff", properties=[prop])
    mine = services.assign(owner, report(owner, prop, title="Mine"), user=staff.user, now=NOW)
    other = report(owner, prop, title="Somebody else's")
    login(client, staff)
    page = client.get(reverse("maintenance:list")).content.decode()
    assert mine.number in page and other.number not in page
    assert client.get(detail(other)).status_code == 404
    assert client.get(detail(mine)).status_code == 200


# --- reporting --------------------------------------------------------------


def test_reporting_through_the_page(client, owner, prop, unit, tenant):
    login(client, owner)
    page = client.get(reverse("maintenance:create"), {"unit": str(unit.public_id)})
    assert page.context["form"].initial["unit"] == str(unit.public_id)
    response = client.post(reverse("maintenance:create"), {
        "property": str(prop.public_id), "unit": str(unit.public_id), "title": "No hot water",
        "description": "Since Monday", "kind": "PLUMBING", "priority": "HIGH", "from_tenant": "on",
        "photos": [jpeg("a.jpg"), jpeg("b.jpg")]})
    req = MaintenanceRequest.objects.get()
    assert response.status_code == 302 and response["Location"] == detail(req)
    assert (req.unit, req.tenant, req.kind, req.priority, req.photos.count()) == (unit, tenant, "PLUMBING", "HIGH",
                                                                                   2)


def test_common_areas_through_the_page(client, owner, prop):
    login(client, owner)
    client.post(reverse("maintenance:create"), {
        "property": str(prop.public_id), "unit": "", "title": "Gate", "kind": "SECURITY", "priority": "NORMAL"})
    assert MaintenanceRequest.objects.get().unit is None


def test_report_errors_show_on_the_form(client, owner, org, prop):
    other_unit = make_lease(owner, make_property(org), code="Z1").unit
    login(client, owner)
    response = client.post(reverse("maintenance:create"), {
        "property": str(prop.public_id), "unit": str(other_unit.public_id), "title": "Leak", "kind": "OTHER",
        "priority": "NORMAL"})
    assert response.status_code == 200 and response.context["form"].errors["unit"]
    assert not MaintenanceRequest.objects.exists()


def test_reporting_is_refused_without_the_capability(client, org, prop):
    login(client, add_member(org, "accountant", all_properties=True))
    assert client.get(reverse("maintenance:create")).status_code == 403


def test_a_caretaker_cannot_report_on_another_property(client, org, prop):
    login(client, add_member(org, "caretaker", properties=[make_property(org)]))
    response = client.post(reverse("maintenance:create"), {
        "property": str(prop.public_id), "unit": "", "title": "Leak", "kind": "OTHER", "priority": "NORMAL"})
    assert response.status_code == 200 and "property" in response.context["form"].errors
    assert not MaintenanceRequest.objects.exists()


# --- the request page -------------------------------------------------------


def test_the_request_page_moves_the_job_on(client, owner, org, prop, fundi):
    staff = add_member(org, "maintenance_staff", properties=[prop])
    req = report(owner, prop)
    login(client, owner)
    page = client.get(detail(req))
    assert page.status_code == 200 and page.context["can_assign"]
    client.post(detail(req), {"action": "assign", "assigned_to": str(staff.user.public_id),
                              "supplier": str(fundi.public_id), "sms_supplier": "on"})
    req.refresh_from_db()
    assert (req.status, req.assigned_to, req.supplier) == (Status.ASSIGNED, staff.user, fundi)
    client.post(detail(req), {"action": "start"})
    client.post(detail(req), {"action": "note", "text": "Bought a new tap"})
    client.post(detail(req), {"action": "done"})
    client.post(detail(req), {"action": "close"})
    req.refresh_from_db()
    assert req.status == Status.CLOSED
    assert req.updates.filter(kind="NOTE", text="Bought a new tap").exists()
    page = client.get(detail(req)).content.decode()
    assert "Bought a new tap" in page and "Reopen" in page


def test_a_missing_reason_is_shown(client, owner, prop):
    req = services.assign(owner, report(owner, prop), user=owner.user, now=NOW)
    login(client, owner)
    response = client.post(detail(req), {"action": "hold", "note": ""}, follow=True)
    assert "Say what the job is waiting for." in response.content.decode()
    req.refresh_from_db()
    assert req.status == Status.ASSIGNED


def test_priority_through_the_page(client, owner, prop):
    req = report(owner, prop)
    login(client, owner)
    client.post(detail(req), {"action": "priority", "priority": "EMERGENCY", "due_at": ""})
    req.refresh_from_db()
    assert req.priority == "EMERGENCY"


def test_maintenance_staff_see_no_assign_or_close(client, owner, org, prop):
    staff = add_member(org, "maintenance_staff", properties=[prop])
    req = services.assign(owner, report(owner, prop), user=staff.user, now=NOW)
    login(client, staff)
    ctx = client.get(detail(req)).context
    assert not ctx["can_assign"] and "close" not in ctx["actions"] and not ctx["can_see_costs"]
    assert client.post(detail(req), {"action": "close"}).status_code == 404


def test_another_organizations_request_is_not_found(client, owner, prop):
    req = report(owner, prop)
    login(client, make_org())
    assert client.get(detail(req)).status_code == 404


# --- costs and photos -------------------------------------------------------


def test_recording_a_cost(client, owner, org, prop, fundi):
    req = services.assign(owner, report(owner, prop), supplier=fundi, sms_supplier=False, now=NOW)
    login(client, owner)
    form = client.get(reverse("maintenance:cost", args=[req.public_id])).context["form"]
    assert form.initial["supplier"] == str(fundi.public_id) and req.number in form.initial["description"]
    response = client.post(reverse("maintenance:cost", args=[req.public_id]), {
        "category": form.initial["category"], "description": "Tap and labour", "amount": "1,800",
        "paid_on": "2026-03-02", "method": "CASH", "reference": "", "supplier": str(fundi.public_id)})
    assert response.status_code == 302
    e = Expense.objects.get()
    assert (e.maintenance_request, e.amount, e.property) == (req, 1800, prop)
    assert req.number in client.get(reverse("expenses:detail", args=[e.public_id])).content.decode()


def test_the_cost_page_needs_the_capabilities(client, org, prop):
    caretaker = add_member(org, "caretaker", properties=[prop])
    req = report(caretaker, prop)
    login(client, caretaker)
    assert client.get(reverse("maintenance:cost", args=[req.public_id])).status_code == 404


def test_photos_are_private(client, owner, org, prop):
    req = report(owner, prop, photos=[jpeg()])
    photo = req.photos.get()
    url = reverse("maintenance:photo", args=[photo.public_id])
    login(client, owner)
    response = client.get(url)
    assert response.status_code == 200 and response["Content-Type"] == "image/jpeg"
    assert "private" in response["Cache-Control"]
    login(client, add_member(org, "maintenance_staff", properties=[prop]))
    assert client.get(url).status_code == 404
    client.logout()
    assert client.get(url).status_code == 302


# --- elsewhere --------------------------------------------------------------


def test_home_tasks(owner, org, prop):
    staff = add_member(org, "maintenance_staff", properties=[prop])
    report(owner, prop)
    services.assign(owner, report(owner, prop), user=staff.user, now=NOW)
    keys = {t.key: t.count for t in home.home(owner).tasks}
    assert keys["repairs_new"] == 1 and keys["repairs_late"] == 2
    staff_keys = {t.key: t.count for t in home.home(staff).tasks}
    assert staff_keys.get("repairs_mine") == 1
    assert "repairs_new" not in staff_keys and "repairs_late" not in staff_keys


def test_the_unit_page_lists_open_repairs(client, owner, prop, unit):
    req = report(owner, prop, unit=unit)
    login(client, owner)
    page = client.get(reverse("properties:unit", args=[unit.public_id])).content.decode()
    assert req.title in page and f"?unit={unit.public_id}" in page


def test_the_nav_link(client, owner, org, prop):
    login(client, owner)
    assert reverse("maintenance:list") in client.get(reverse("accounts:home")).content.decode()
    login(client, add_member(org, "accountant", all_properties=True))
    assert reverse("maintenance:list") not in client.get(reverse("accounts:home")).content.decode()


# --- portal -----------------------------------------------------------------


def test_a_tenant_reports_and_follows_a_repair(client, owner, prop, lease):
    user = portal_user(owner, lease)
    client.force_login(user)
    assert reverse("portal:repair_create", args=[lease.public_id]) in client.get(
        reverse("portal:lease", args=[lease.public_id])).content.decode()
    response = client.post(reverse("portal:repair_create", args=[lease.public_id]), {
        "title": "No water in the bathroom", "description": "", "emergency": "on", "photos": [jpeg()]})
    req = MaintenanceRequest.objects.get()
    assert response.status_code == 302 and response["Location"] == reverse("portal:repair", args=[req.public_id])
    assert (req.priority, req.source) == ("EMERGENCY", "PORTAL")
    services.add_note(owner, req, text="Internal: fundi is slow", now=NOW)
    services.add_note(owner, req, text="The plumber comes at 10", share=True, now=NOW)
    page = client.get(reverse("portal:repair", args=[req.public_id])).content.decode()
    assert "The plumber comes at 10" in page and "Internal" not in page
    client.post(reverse("portal:repair", args=[req.public_id]), {"text": "I will be home"})
    assert req.updates.filter(kind="COMMENT", text="I will be home").exists()
    photo = req.photos.get()
    assert client.get(reverse("portal:repair_photo", args=[photo.public_id])).status_code == 200


def test_a_tenant_sees_only_their_own_repairs(client, owner, prop, lease):
    user = portal_user(owner, lease)
    neighbour = make_lease(owner, prop, code="B7")
    theirs = report(owner, prop, unit=neighbour.unit, from_tenant=True, photos=[jpeg()])
    staff_photo = report(owner, prop, unit=lease.unit, from_tenant=True, photos=[jpeg()])
    client.force_login(user)
    assert client.get(reverse("portal:repair", args=[theirs.public_id])).status_code == 404
    assert client.get(reverse("portal:repair_create", args=[neighbour.public_id])).status_code == 404
    assert client.get(reverse("portal:repair_photo", args=[theirs.photos.get().public_id])).status_code == 404
    # Staff photos stay with staff unless shared.
    assert client.get(reverse("portal:repair", args=[staff_photo.public_id])).status_code == 200
    assert client.get(reverse("portal:repair_photo",
                              args=[staff_photo.photos.get().public_id])).status_code == 404


def test_staff_without_a_portal_account_get_404(client, owner, lease):
    client.force_login(owner.user)
    assert client.get(reverse("portal:repair_create", args=[lease.public_id])).status_code == 404
