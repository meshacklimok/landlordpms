"""Expense pages (D-067): scope, permissions, receipts and the CSV."""

import pytest
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from expenses import services
from expenses.models import Expense
from reports import home

from .conftest import PAID, jpeg, pdf, spend

pytestmark = pytest.mark.django_db

Status = Expense.Status


def test_recording_through_the_page(client, owner, prop, repairs):
    login(client, owner)
    assert client.get(reverse("expenses:create")).status_code == 200
    response = client.post(reverse("expenses:create"), {
        "property": str(prop.public_id), "category": str(repairs.public_id), "description": "New gate lock",
        "amount": "3,200", "paid_on": PAID.isoformat(), "method": "CASH", "reference": "", "supplier": "",
        "receipt": pdf()})
    e = Expense.objects.get()
    assert response.status_code == 302 and response["Location"] == reverse("expenses:detail", args=[e.public_id])
    assert (e.amount, e.status, e.receipt_is_pdf) == (3200, Status.APPROVED, True)
    page = client.get(reverse("expenses:list")).content.decode()
    assert "New gate lock" in page and e.number in page


def test_errors_show_on_the_form(client, owner, prop, repairs):
    login(client, owner)
    response = client.post(reverse("expenses:create"), {
        "property": str(prop.public_id), "category": str(repairs.public_id), "description": "Lock",
        "amount": "0", "paid_on": PAID.isoformat(), "method": "CASH"})
    assert response.status_code == 200 and not Expense.objects.exists()
    assert response.context["form"].errors["amount"]


def test_a_caretaker_cannot_record_or_see_expenses(client, org, prop, repairs):
    login(client, add_member(org, "caretaker", properties=[prop]))
    assert client.get(reverse("expenses:list")).status_code == 403
    assert client.get(reverse("expenses:create")).status_code == 403


def test_a_manager_cannot_post_to_a_property_outside_their_scope(client, org, prop, repairs):
    manager = add_member(org, "manager", properties=[make_property(org)])
    login(client, manager)
    response = client.post(reverse("expenses:create"), {
        "property": str(prop.public_id), "category": str(repairs.public_id), "description": "Lock",
        "amount": "100", "paid_on": PAID.isoformat(), "method": "CASH"})
    assert response.status_code == 200 and "property" in response.context["form"].errors
    assert not Expense.objects.exists()


def test_another_organizations_expense_is_not_found(client, owner, prop, repairs):
    e = spend(owner, prop, repairs, receipt=jpeg())
    stranger = make_org()
    login(client, stranger)
    for name in ("detail", "receipt"):
        assert client.get(reverse(f"expenses:{name}", args=[e.public_id])).status_code == 404
    assert client.post(reverse("expenses:detail", args=[e.public_id]),
                       {"action": "void", "reason": "x"}).status_code == 404
    e.refresh_from_db()
    assert e.status == Status.APPROVED
    assert e.number not in client.get(reverse("expenses:list")).content.decode()


def test_a_scoped_member_sees_only_their_properties(client, owner, org, prop, repairs):
    other = make_property(org, name="Baobab Flats")
    mine, theirs = spend(owner, prop, repairs, description="Mine"), spend(owner, other, repairs, description="Theirs")
    login(client, add_member(org, "accountant", properties=[prop]))
    page = client.get(reverse("expenses:list")).content.decode()
    assert "Mine" in page and "Theirs" not in page
    assert client.get(reverse("expenses:detail", args=[theirs.public_id])).status_code == 404
    assert client.get(reverse("expenses:detail", args=[mine.public_id])).status_code == 200


def test_approve_reject_and_void_from_the_page(client, owner, org, prop, repairs):
    manager = add_member(org, "manager", all_properties=True)
    a, b = spend(manager, prop, repairs), spend(manager, prop, repairs)
    login(client, manager)
    client.post(reverse("expenses:detail", args=[a.public_id]), {"action": "approve"})
    a.refresh_from_db()
    assert a.status == Status.SUBMITTED  # a manager cannot approve

    login(client, owner)
    url = reverse("expenses:detail", args=[a.public_id])
    assert "Approve" in client.get(url).content.decode()
    client.post(url, {"action": "approve"})
    client.post(reverse("expenses:detail", args=[b.public_id]), {"action": "reject", "reason": ""})
    b.refresh_from_db()
    assert b.status == Status.SUBMITTED  # no reason, no rejection
    client.post(reverse("expenses:detail", args=[b.public_id]), {"action": "reject", "reason": "Wrong unit"})
    client.post(url, {"action": "void", "reason": "Recorded twice"})
    a.refresh_from_db()
    b.refresh_from_db()
    assert (a.status, b.status) == (Status.VOIDED, Status.REJECTED)


def test_the_receipt_is_served_privately(client, owner, prop, repairs):
    photo, scan = spend(owner, prop, repairs, receipt=jpeg()), spend(owner, prop, repairs, receipt=pdf())
    login(client, owner)
    r = client.get(reverse("expenses:receipt", args=[photo.public_id]))
    assert r.status_code == 200 and r["Content-Type"] == "image/jpeg" and "private" in r["Cache-Control"]
    r = client.get(reverse("expenses:receipt", args=[scan.public_id]))
    assert r["Content-Type"] == "application/pdf" and "attachment" in r["Content-Disposition"]
    none = spend(owner, prop, repairs)
    assert client.get(reverse("expenses:receipt", args=[none.public_id])).status_code == 404


def test_adding_a_receipt_later(client, owner, prop, repairs):
    e = spend(owner, prop, repairs)
    login(client, owner)
    client.post(reverse("expenses:detail", args=[e.public_id]), {"action": "receipt", "receipt": jpeg()})
    e.refresh_from_db()
    assert e.receipt


def test_filters_and_csv(client, owner, org, prop, repairs):
    security = services.categories(org).get(name="Security")
    spend(owner, prop, repairs, "1000", description="Gate")
    spend(owner, prop, security, "3000", description="Guards")
    login(client, owner)
    page = client.get(reverse("expenses:list"), {"category": str(security.public_id)}).content.decode()
    assert "Guards" in page and "Gate" not in page
    page = client.get(reverse("expenses:list"), {"from": "2025-02"}).content.decode()
    assert "Guards" not in page and "No expenses match" in page
    r = client.get(reverse("expenses:list"), {"format": "csv"})
    assert r["Content-Type"].startswith("text/csv")
    body = r.content.decode()
    assert "Guards" in body and "3000.00" in body


def test_the_csv_needs_export(client, org, prop, repairs):
    login(client, add_member(org, "maintenance_manager", all_properties=True))
    assert client.get(reverse("expenses:list")).status_code == 200
    assert client.get(reverse("expenses:list"), {"format": "csv"}).status_code == 403


def test_suppliers_pages(client, owner, org, prop, repairs):
    login(client, owner)
    r = client.post(reverse("expenses:supplier_create"), {"name": "Otieno Plumbing", "kra_pin": "bad"})
    assert r.status_code == 200 and r.context["form"].errors["kra_pin"]
    client.post(reverse("expenses:supplier_create"), {"name": "Otieno Plumbing", "phone": "0712000000"})
    s = services.Supplier.objects.get()
    spend(owner, prop, repairs, "4500", supplier=s)
    page = client.get(reverse("expenses:suppliers")).content.decode()
    assert "Otieno Plumbing" in page and "4,500.00" in page
    client.post(reverse("expenses:supplier_edit", args=[s.public_id]), {"action": "archive"})
    s.refresh_from_db()
    assert s.is_archived

    login(client, add_member(org, "accountant", all_properties=True))
    assert client.get(reverse("expenses:suppliers")).status_code == 200
    assert client.get(reverse("expenses:supplier_create")).status_code == 403


def test_categories_page(client, owner, org):
    login(client, owner)
    client.post(reverse("expenses:categories"), {"action": "create", "name": "Pest control"})
    assert services.categories(org).filter(name="Pest control").exists()
    login(client, add_member(org, "manager", all_properties=True))
    assert client.get(reverse("expenses:categories")).status_code == 200
    assert client.post(reverse("expenses:categories"), {"action": "create", "name": "X"}).status_code == 403


def test_the_home_task(owner, org, prop, repairs):
    manager = add_member(org, "manager", all_properties=True)
    spend(manager, prop, repairs)
    (task,) = [t for t in home.home(owner).tasks if t.key == "expenses"]
    assert task.count == 1 and "status=SUBMITTED" in task.url
    assert not [t for t in home.home(manager).tasks if t.key == "expenses"]
