"""Payment pages: render, act through services, and stay inside the member's scope."""

from decimal import Decimal

import pytest
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from billing.invoicing import lease_balance
from billing.models import Invoice
from billing.tests.test_invoicing import FEB, JAN, bill, make_lease
from payments.models import Payment, PaymentAccount, PropertyPaymentAccount

from .test_services import pay

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def prop(owner):
    return make_property(owner.organization)


@pytest.fixture
def lease(owner, prop):
    return make_lease(owner, prop)


def detail(payment):
    return reverse("payments:detail", args=[payment.public_id])


def record(lease):
    return reverse("payments:record", args=[lease.public_id])


def test_pages_render(client, owner, lease):
    login(client, owner)
    bill(lease, FEB)
    payment = pay(owner, lease, "5000", reference="QAB1")
    for url in (reverse("payments:list"), reverse("payments:list") + "?status=confirmed&q=QAB1",
                reverse("payments:review"), record(lease), detail(payment),
                reverse("billing:lease_account", args=[lease.public_id])):
        r = client.get(url)
        assert r.status_code == 200, url
    assert payment in client.get(reverse("payments:list")).context["page"]
    r = client.get(reverse("payments:receipt", args=[payment.public_id]))
    assert r["Content-Type"] == "application/pdf" and b"".join(r.streaming_content).startswith(b"%PDF-")
    r = client.get(reverse("payments:receipt", args=[payment.public_id]) + "?download=1")
    assert "attachment" in r["Content-Disposition"] and payment.receipt.number in r["Content-Disposition"]


def test_record_confirms_for_owner(client, owner, prop, lease):
    login(client, owner)
    invoice = bill(lease, FEB)
    account = PaymentAccount.objects.create(organization=owner.organization, type=PaymentAccount.Type.PAYBILL,
                                            number="400200", display_name="Rent paybill")
    PropertyPaymentAccount.objects.create(organization=owner.organization, property=prop, payment_account=account,
                                          is_default=True)
    r = client.post(record(lease), {"amount": "15000", "method": "MPESA", "paid_at": "2026-02-02",
                                    "reference": "QZZ1", "payment_account": account.pk})
    payment = Payment.objects.get()
    assert r.status_code == 302 and r["Location"] == detail(payment)
    assert payment.status == Payment.Status.CONFIRMED and payment.payment_account == account
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.PAID


def test_record_errors_are_shown(client, owner, lease):
    login(client, owner)
    r = client.post(record(lease), {"amount": "-3", "method": "CASH", "paid_at": "2026-02-02"})
    assert r.status_code == 200 and r.context["form"].errors["amount"]
    assert not Payment.objects.exists()


def test_record_with_chosen_split(client, owner, lease):
    login(client, owner)
    jan, feb = bill(lease, JAN), bill(lease, FEB)
    r = client.post(record(lease), {"amount": "5000", "method": "CASH", "paid_at": "2026-02-02",
                                    f"alloc_{feb.public_id}": "5000", f"alloc_{jan.public_id}": ""})
    assert r.status_code == 302
    feb.refresh_from_db()
    jan.refresh_from_db()
    assert (jan.amount_paid, feb.amount_paid) == (0, Decimal("5000.00"))
    r = client.post(record(lease), {"amount": "5000", "method": "CASH", "paid_at": "2026-02-02",
                                    f"alloc_{jan.public_id}": "99999"})
    assert r.status_code == 200 and r.context["form"].non_field_errors()


def test_maker_checker_flow_through_the_pages(client, owner, lease):
    bill(lease, FEB)
    accountant = add_member(owner.organization, "accountant", all_properties=True)
    login(client, accountant)
    client.post(record(lease), {"amount": "6000", "method": "CASH", "paid_at": "2026-02-02"})
    payment = Payment.objects.get()
    assert payment.status == Payment.Status.PENDING_REVIEW
    assert client.get(reverse("payments:review")).status_code == 403
    assert client.post(detail(payment), {"action": "confirm"}).status_code == 403
    payment.refresh_from_db()
    assert payment.status == Payment.Status.PENDING_REVIEW
    assert client.get(reverse("payments:receipt", args=[payment.public_id])).status_code == 404

    client.logout()
    login(client, owner)
    assert payment in client.get(reverse("payments:review")).context["payments"]
    r = client.post(detail(payment), {"action": "confirm"})
    assert r.status_code == 302
    payment.refresh_from_db()
    assert payment.status == Payment.Status.CONFIRMED and lease_balance(lease) == Decimal("9000.00")

    r = client.post(detail(payment), {"action": "reverse", "reverse-reason": ""})
    assert r.status_code == 200 and r.context["reverse_form"].errors
    r = client.post(detail(payment), {"action": "reverse", "reverse-reason": "Cash was counterfeit"})
    assert r.status_code == 302
    payment.refresh_from_db()
    assert payment.status == Payment.Status.REVERSED and lease_balance(lease) == Decimal("15000.00")
    assert client.get(detail(payment)).status_code == 200


def test_reject_through_the_page(client, owner, lease):
    accountant = add_member(owner.organization, "accountant", all_properties=True)
    payment = pay(accountant, lease, "100")
    login(client, owner)
    r = client.post(detail(payment), {"action": "reject", "reject-reason": "Never arrived"})
    assert r.status_code == 302
    payment.refresh_from_db()
    assert payment.status == Payment.Status.REVERSED


def test_apply_credit_from_the_lease_account(client, owner, lease):
    login(client, owner)
    pay(owner, lease, "15000", paid_at=JAN)
    invoice = bill(lease, FEB)
    account_url = reverse("billing:lease_account", args=[lease.public_id])
    assert client.get(account_url).context["credit"] == Decimal("15000.00")
    r = client.post(reverse("payments:apply_credit", args=[lease.public_id]))
    assert r.status_code == 302 and r["Location"] == account_url
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.PAID


def test_out_of_scope_is_404_and_no_capability_is_403(client, owner, prop, lease):
    payment = pay(owner, lease, "100")
    other = make_property(owner.organization)
    scoped = add_member(owner.organization, "accountant", properties=[other])
    login(client, scoped)
    for url in (detail(payment), reverse("payments:receipt", args=[payment.public_id]), record(lease)):
        assert client.get(url).status_code == 404, url
    assert client.post(reverse("payments:apply_credit", args=[lease.public_id])).status_code == 404
    client.logout()
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    login(client, caretaker)
    assert client.get(reverse("payments:list")).status_code == 403
    assert client.get(detail(payment)).status_code == 403
    client.logout()
    stranger = make_org()
    login(client, stranger)
    assert client.get(detail(payment)).status_code == 404
