"""Condition report pages (D-047): the flow through HTTP, private photos and scope."""

import importlib

import pytest
from django.apps import apps
from django.urls import reverse

from accounts.models import Capability, RoleCapability
from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from billing import deposits
from billing.tests.test_invoicing import JAN, make_lease
from inspections import services
from inspections.models import ConditionReport

from .conftest import jpeg

pytestmark = pytest.mark.django_db

IN = ConditionReport.Kind.MOVE_IN


def test_lease_page_starts_a_report_and_the_draft_is_filled_in(client, owner, prop):
    lease = make_lease(owner, prop)
    login(client, owner)
    page = client.get(reverse("leases:detail", args=[lease.public_id]))
    assert b"Condition reports" in page.content
    response = client.post(reverse("inspections:start", args=[lease.public_id, "move-in"]))
    report = ConditionReport.objects.get(lease=lease, kind=IN)
    url = reverse("inspections:report", args=[report.public_id])
    assert response.status_code == 302 and response.url == url
    assert b'form="details"' in client.get(url).content

    data = {"action": "complete", "inspected_on": report.inspected_on.isoformat(), "tenant_present": "on",
            "keys_handed": "2"}
    data.update({f"condition-{ln.pk}": "GOOD" for ln in report.lines.all()})
    data.update({f"note-{ln.pk}": "" for ln in report.lines.all()})
    assert client.post(url, data).status_code == 302
    report.refresh_from_db()
    assert report.status == ConditionReport.Status.COMPLETED and report.tenant_present and report.keys_handed == 2
    assert b'form="details"' not in client.get(url).content


def test_an_unknown_kind_is_404(client, owner, prop):
    lease = make_lease(owner, prop)
    login(client, owner)
    assert client.post(reverse("inspections:start", args=[lease.public_id, "sideways"])).status_code == 404


def test_photos_are_served_only_through_the_checked_view(client, owner, prop):
    lease = make_lease(owner, prop)
    report = services.start(owner, lease, IN)
    login(client, owner)
    url = reverse("inspections:report", args=[report.public_id])
    data = {"action": "add_photo", "image": jpeg(), "line": report.lines.first().pk}
    assert client.post(url, data).status_code == 302
    photo = report.photos.get()
    response = client.get(reverse("inspections:photo", args=[photo.public_id]))
    assert response.status_code == 200 and response["Content-Type"] == "image/jpeg"
    assert "private" in response["Cache-Control"] and "noindex" in response["X-Robots-Tag"]

    stranger = make_org()
    client.logout()
    login(client, stranger)
    assert client.get(reverse("inspections:photo", args=[photo.public_id])).status_code == 404
    assert client.get(url).status_code == 404


def test_scoped_staff_see_only_their_properties(client, owner, prop):
    lease = make_lease(owner, prop)
    report = services.start(owner, lease, IN)
    elsewhere = add_member(owner.organization, "caretaker", properties=[make_property(owner.organization)])
    login(client, elsewhere)
    assert client.get(reverse("inspections:report", args=[report.public_id])).status_code == 404
    assert client.get(reverse("inspections:register", args=[lease.unit.public_id])).status_code == 404


def test_a_viewer_reads_but_cannot_edit(client, owner, prop):
    lease = make_lease(owner, prop)
    report = services.start(owner, lease, IN)
    viewer = add_member(owner.organization, "viewer", all_properties=True)
    login(client, viewer)
    url = reverse("inspections:report", args=[report.public_id])
    page = client.get(url)
    assert page.status_code == 200 and b'form="details"' not in page.content
    client.post(url, {"action": "cancel", "reason": "no"})
    report.refresh_from_db()
    assert report.status == ConditionReport.Status.DRAFT
    assert client.post(reverse("inspections:start", args=[lease.public_id, "move-out"])).status_code in (403, 404)


def test_register_page_adds_edits_and_archives(client, owner, prop):
    lease = make_lease(owner, prop)
    login(client, owner)
    url = reverse("inspections:register", args=[lease.unit.public_id])
    assert client.get(url).status_code == 200
    client.post(url, {"action": "add", "name": "Gas cooker", "area": "Kitchen", "quantity": "1"})
    item = lease.unit.items.get()
    client.post(url, {"action": "edit", "item": item.pk, "name": "Cooker", "area": "Kitchen", "quantity": "2"})
    item.refresh_from_db()
    assert (item.name, item.quantity) == ("Cooker", 2)
    client.post(url, {"action": "archive", "item": item.pk})
    assert not lease.unit.items.exists()
    assert b"Cooker" in client.get(url).content  # listed under archived


def test_move_out_page_offers_a_deduction_that_cites_it(client, owner, prop):
    lease = make_lease(owner, prop)
    deposits.record_received(owner, lease, amount=30000, entry_date=JAN)
    out = services.start(owner, lease, ConditionReport.Kind.MOVE_OUT)
    services.save_details(owner, out, inspected_on=out.inspected_on,
                          lines={ln.pk: ("DAMAGED", "") for ln in out.lines.all()})
    services.complete(owner, out)
    login(client, owner)
    url = reverse("inspections:report", args=[out.public_id])
    assert b"Deduct from the deposit" in client.get(url).content
    response = client.post(url, {"action": "deduct", "deduct-amount": "4000", "deduct-deposit_type": "RENT",
                                 "deduct-reason": "Damage", "deduct-entry_date": out.inspected_on.isoformat()})
    assert response.status_code == 302
    assert out.deposit_entries.get().amount == -4000
    assert b"inspections/" in client.get(reverse("billing:lease_account", args=[lease.public_id])).content


def test_the_grant_migration_gives_existing_roles_the_capabilities(owner):
    RoleCapability.objects.filter(capability__module="inspections").delete()
    Capability.objects.filter(module="inspections").delete()
    manager = add_member(owner.organization, "manager").role
    viewer = add_member(owner.organization, "viewer").role
    accountant = add_member(owner.organization, "accountant").role
    module = importlib.import_module("accounts.migrations.0007_grant_inspection_capabilities")
    module.grant(apps, None)
    codes = lambda role: set(role.capabilities.filter(module="inspections").values_list("codename", flat=True))  # noqa: E731
    assert codes(owner.role) == codes(manager) == {"inspections.view", "inspections.record"}
    assert codes(viewer) == {"inspections.view"}
    assert codes(accountant) == set()
