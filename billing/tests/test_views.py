"""Billing pages: render, act through services, and stay inside the member's scope."""

from decimal import Decimal

import pytest
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from billing import deposits, invoicing
from billing.models import DepositEntry, Invoice

from .test_invoicing import FEB, JAN, bill, make_lease

pytestmark = pytest.mark.django_db



@pytest.fixture
def owner(client):
    m = make_org()
    login(client, m)
    return m


@pytest.fixture
def lease(owner):
    return make_lease(owner, make_property(owner.organization), due_day=5)


def account(lease):
    return reverse("billing:lease_account", args=[lease.public_id])


def test_pages_render(client, owner, lease):
    invoice = bill(lease, JAN)
    deposits.record_received(owner, lease, amount=15000, entry_date=JAN)
    for url in (reverse("billing:invoices"), reverse("billing:invoices") + "?status=open&overdue=1&q=Tenant",
                reverse("billing:invoice", args=[invoice.public_id]), reverse("billing:generate"),
                reverse("billing:arrears"), reverse("billing:arrears") + "?all=1", account(lease),
                account(lease) + "?start=2026-01-01&end=2026-03-31",
                reverse("leases:detail", args=[lease.public_id])):
        r = client.get(url)
        assert r.status_code == 200, url
    r = client.get(reverse("leases:detail", args=[lease.public_id]))
    assert r.context["account"] == {"balance": Decimal("15000.00"), "deposit_held": Decimal("15000.00")}
    assert invoice in client.get(reverse("billing:invoices")).context["page"]


def test_generate_can_pick_leases_and_needs_a_scope(client, owner, lease):
    base = {"month": "2026-02", "include": "all", "action": "issue"}
    r = client.post(reverse("billing:generate_review"), {**base, "scope": "leases"})
    assert r.context["form"].errors["leases"] and Invoice.objects.count() == 0
    r = client.post(reverse("billing:generate_review"), {**base, "scope": "property"})
    assert r.context["form"].errors["property"]
    r = client.post(reverse("billing:generate_review"), {**base, "scope": "leases", "leases": [str(lease.public_id)],
                                                  "due_date": "2000-01-01"})
    assert r.context["form"].errors["due_date"] and Invoice.objects.count() == 0
    r = client.post(reverse("billing:generate_review"), {**base, "scope": "leases", "leases": [str(lease.public_id)],
                                                  "include": "meters"})
    assert r.context["result"].invoices == []  # no meter charges, and rent was not asked for
    r = client.post(reverse("billing:generate_review"), {**base, "scope": "leases", "leases": [str(lease.public_id)]})
    assert len(r.context["result"].invoices) == 1


def test_review_can_be_limited_to_one_property(client, owner, lease):
    prop = lease.unit.property
    q = {"month": "2026-02", "include": "all", "scope": "property", "property": str(prop.public_id)}
    [preview] = client.get(reverse("billing:generate_review"), q).context["previews"]
    assert preview.lease == lease
    r = client.get(reverse("billing:generate_review"), {**q, "property": ""})
    assert r.context["form"].errors["property"]


def test_an_old_generate_form_goes_to_the_review_and_issues_nothing(client, owner, lease):
    r = client.post(reverse("billing:generate"), {"month": "2026-02", "include": "all", "scope": "all",
                                                  "action": "issue"})
    assert r.status_code == 302 and r.url.startswith(reverse("billing:generate_review") + "?month=2026-02")
    assert Invoice.objects.count() == 0


def test_generate_shows_the_message_and_why_nothing_is_billed(client, owner, lease):
    r = client.get(reverse("billing:generate"))
    assert r.status_code == 200
    base = {"month": "2026-02", "include": "all", "scope": "all", "notify": "on", "action": "issue"}
    client.post(reverse("billing:generate_review"), base)
    r = client.get(reverse("billing:generate_review"), {k: v for k, v in base.items() if k != "action"})
    assert r.context["previews"] == []
    [(billed, why)] = r.context["nothing"]
    assert billed == lease and "INV-" in why
    assert r.context["example"]["row"]["text"] and "Dear" in r.context["example"]["row"]["text"]
    assert b"The message tenants get with a new invoice" in r.content


def test_generate_and_void_through_the_pages(client, owner, lease):
    base = {"month": "2026-02", "include": "all", "scope": "all", "notify": "on"}
    assert client.get(reverse("billing:generate_review")).status_code == 302  # nothing chosen yet
    r = client.get(reverse("billing:generate_review"), base)  # a review issues nothing
    [preview] = r.context["previews"]
    assert preview.lease == lease and Invoice.objects.count() == 0
    assert preview.messages and all(m["text"] for m in preview.messages)
    assert r.context["scope_label"] and r.context["by_property"] and r.context["change_url"].startswith(
        reverse("billing:generate") + "?month=2026-02")
    r2 = client.get(r.context["change_url"])  # "Change my choices" comes back filled in
    assert r2.context["form"].initial["month"] == "2026-02" and r2.context["form"].initial["notify"] is True
    assert b"INV-" in r.content or not preview.messages[0]["text"]
    r = client.post(reverse("billing:generate_review"), {**base, "action": "issue"})
    [invoice] = r.context["result"].invoices
    assert invoice.lines.get().billing_month == FEB
    r = client.get(reverse("billing:generate_review"), {**base, "month": "2099-01"})
    assert r.context["form"].errors["month"]
    url = reverse("billing:invoice", args=[invoice.public_id])
    r = client.post(url, {"reason": ""})
    assert r.status_code == 200 and r.context["void_form"].errors
    r = client.post(url, {"reason": "Billed in error"})
    assert r.status_code == 302
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.VOID


def test_the_invoice_pdf_opens(client, owner, lease):
    invoice = bill(lease, FEB)
    r = client.get(reverse("billing:invoice_pdf", args=[invoice.public_id]))
    assert r.status_code == 200 and r["Content-Type"] == "application/pdf" and r.content.startswith(b"%PDF")


def test_the_move_out_statement_and_pdf_open_after_the_lease_ends(client, owner, lease):
    from leases import services as lease_services

    bill(lease, FEB)
    lease_services.end_lease(owner, lease, ended_on=FEB.replace(day=20))
    page = client.get(reverse("leases:move_out", args=[lease.public_id]))
    assert page.status_code == 200
    r = client.get(reverse("leases:move_out_pdf", args=[lease.public_id]))
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


def test_opening_balance_and_deposits_through_the_account_page(client, owner, lease):
    url = account(lease)
    client.post(url, {"action": "opening_balance", "amount": "KES 7,500", "as_of": "2025-12-31"})
    assert invoicing.lease_balance(lease) == Decimal("7500.00")
    client.post(url, {"action": "deposit_received", "received-amount": "30000", "received-deposit_type": "RENT",
                      "received-entry_date": "2026-01-01", "received-reference": "QK1"})
    assert deposits.held(lease) == Decimal("30000.00")
    r = client.post(url, {"action": "deposit_deduct", "deduct-amount": "7500", "deduct-deposit_type": "RENT",
                          "deduct-entry_date": "2026-01-02", "deduct-reason": ""})
    assert r.status_code == 200 and r.context["deduct_form"].errors  # a reason is required
    client.post(url, {"action": "deposit_deduct", "deduct-amount": "7500", "deduct-deposit_type": "RENT",
                      "deduct-entry_date": "2026-01-02", "deduct-reason": "Arrears",
                      "deduct-apply_to_balance": "on"})
    assert invoicing.lease_balance(lease) == 0 and deposits.held(lease) == Decimal("22500.00")
    deduction = DepositEntry.objects.get(lease=lease, kind=DepositEntry.Kind.DEDUCTION)
    client.post(url, {"action": "deposit_reverse", "entry": deduction.pk, "reason": "Paid in cash"})
    assert invoicing.lease_balance(lease) == Decimal("7500.00") and deposits.held(lease) == Decimal("30000.00")
    r = client.get(url)
    assert deduction.pk in r.context["reversed_ids"]
    assert client.post(url, {"action": "deposit_reverse", "entry": "x", "reason": "?"}).status_code == 404


def test_viewer_sees_but_cannot_change_money(client, owner, lease):
    invoice = bill(lease, JAN)
    login(client, add_member(owner.organization, "viewer", all_properties=True))
    r = client.get(account(lease))
    assert r.status_code == 200 and not any(r.context[k] for k in ("can_opening", "can_record", "can_deduct"))
    assert client.post(account(lease), {"action": "opening_balance", "amount": "1", "as_of": "2026-01-01"}
                       ).status_code == 403
    assert client.get(reverse("billing:generate")).status_code == 403
    r = client.get(reverse("billing:invoice", args=[invoice.public_id]))
    assert r.status_code == 200 and not r.context["can_void"]
    assert client.post(reverse("billing:invoice", args=[invoice.public_id]), {"reason": "x"}).status_code == 403


def test_out_of_scope_and_other_org_are_404(client, owner, lease):
    invoice = bill(lease, JAN)
    login(client, add_member(owner.organization, "manager", properties=[make_property(owner.organization)]))
    assert client.get(account(lease)).status_code == 404
    assert client.get(reverse("billing:invoice", args=[invoice.public_id])).status_code == 404
    assert not client.get(reverse("billing:arrears") + "?all=1").context["rows"]
    login(client, make_org())
    assert client.get(account(lease)).status_code == 404
    assert client.get(reverse("billing:invoice", args=[invoice.public_id])).status_code == 404


def test_the_invoice_list_shows_totals(client, owner, lease):
    bill(lease, JAN)
    page = client.get(reverse("billing:invoices")).content.decode()
    assert "Billed" in page and "Totals for all invoices" in page
    page = client.get(reverse("billing:invoices"), {"q": "Tenant"}).content.decode()
    assert "matching your search" in page


def test_the_arrears_page_sums_up(client, owner, lease):
    bill(lease, JAN)
    other = make_lease(owner, make_property(owner.organization, name="Baobab"), code="B2", due_day=5)
    bill(other, JAN)
    page = client.get(reverse("billing:arrears")).content.decode()
    assert "Owed in total" in page and "How late the money is" in page and "By property" in page
    assert "Share of total" in page and "50%" in page


def test_the_account_shows_the_payment_record(client, owner, lease):
    from payments import services as payment_services
    assert "Payment record" not in client.get(account(lease)).content.decode()
    bill(lease, JAN)
    payment_services.record_payment(owner, lease, amount=15000, method="CASH", paid_at=JAN)
    page = client.get(account(lease)).content.decode()
    assert "Payment record" in page and "Billed to date" in page and "100% of it paid" in page
    assert "Payments received" in page and "1 payment, " in page


def test_invoice_check_page_shows_only_face_facts(client, owner, lease):
    from billing.pdf import check_url, render_invoice
    invoice = bill(lease, FEB)
    url = reverse("invoice_check", args=[invoice.public_id])
    assert check_url(invoice).endswith(url)
    body = client.get(url).content.decode()
    assert invoice.number in body and lease.unit.code not in body and "Paid so far" not in body
    assert render_invoice(invoice).startswith(b"%PDF")
    assert client.get(reverse("invoice_check", args=["00000000-0000-4000-8000-000000000000"])).status_code == 404
    from billing import invoicing
    invoicing.void_invoice(owner, invoice, reason="Billed in error")
    assert "cancelled" in client.get(url).content.decode()
