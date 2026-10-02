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


def test_generate_and_void_through_the_pages(client, owner, lease):
    r = client.post(reverse("billing:generate"), {"month": "2026-02"})
    [invoice] = r.context["result"].invoices
    assert invoice.lines.get().billing_month == FEB
    r = client.post(reverse("billing:generate"), {"month": "2099-01"})
    assert r.context["form"].errors["month"]
    url = reverse("billing:invoice", args=[invoice.public_id])
    r = client.post(url, {"reason": ""})
    assert r.status_code == 200 and r.context["void_form"].errors
    r = client.post(url, {"reason": "Billed in error"})
    assert r.status_code == 302
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.VOID


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
