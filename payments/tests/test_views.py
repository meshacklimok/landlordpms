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


def test_list_filters_tabs_and_totals(client, owner, prop, lease):
    login(client, owner)
    pay(owner, lease, "5000", method="MPESA", reference="QAB1")
    pay(owner, lease, "700", method="CASH")
    url = reverse("payments:list")
    r = client.get(url, {"method": "MPESA", "status": "confirmed", "property": prop.public_id,
                         "date_from": "2026-02-01", "date_to": "2026-02-28"})
    assert r.status_code == 200 and [p.reference for p in r.context["page"]] == ["QAB1"]
    assert r.context["totals"]["confirmed"].amounts == {"KES": Decimal("5000.00")}
    # Bad values are dropped, not errors.
    r = client.get(url, {"method": "BITCOIN", "date_from": "yesterday", "property": "nope"})
    assert r.status_code == 200 and len(r.context["page"]) == 2
    r = client.get(url, {"status": "rejected"})
    assert r.status_code == 200 and not r.context["page"].object_list
    assert r.context["totals"]["all"].count == 2  # tab counts ignore the status filter


def test_export_csv_follows_filters_scope_and_quotes_formulas(client, owner, lease):
    pay(owner, lease, "5000", method="MPESA", reference="=HYPERLINK(1)")
    pay(owner, lease, "700", method="CASH")
    stranger = make_org()
    pay(stranger, make_lease(stranger, make_property(stranger.organization)), "999", reference="THEIRS")
    login(client, owner)
    r = client.get(reverse("payments:export"), {"method": "CASH"})
    assert r.status_code == 200 and r["Content-Type"].startswith("text/csv")
    assert "attachment" in r["Content-Disposition"]
    body = b"".join(r.streaming_content).decode("utf-8")
    rows = body.lstrip("\ufeff").splitlines()
    assert rows[0].startswith("Date paid,Status,Amount") and len(rows) == 2 and "700.00" in rows[1]
    body = b"".join(client.get(reverse("payments:export")).streaming_content).decode("utf-8")
    assert "'=HYPERLINK(1)" in body and "THEIRS" not in body
    client.logout()
    login(client, add_member(owner.organization, "maintenance_manager", all_properties=True))
    assert client.get(reverse("payments:export")).status_code == 403


def test_lease_picker_lists_and_searches(client, owner, prop, lease):
    other = make_lease(owner, prop, code="B7")
    bill(lease, FEB)
    login(client, owner)
    r = client.get(reverse("payments:pick_lease"))
    assert r.status_code == 200 and r.context["leases"] == [lease, other]
    r = client.get(reverse("payments:pick_lease"), {"q": "B7"})
    assert r.context["leases"] == [other]
    client.logout()
    login(client, add_member(owner.organization, "viewer", all_properties=True))
    assert client.get(reverse("payments:pick_lease")).status_code == 403


def test_record_warns_on_a_reused_reference(client, owner, lease):
    login(client, owner)
    first = pay(owner, lease, "5000", reference="QAB1")
    data = {"amount": "5000", "method": "MPESA", "paid_at": "2026-02-02", "reference": "qab1"}
    r = client.post(record(lease), data)
    assert r.status_code == 200 and r.context["form"].visible_duplicate == first
    assert Payment.objects.count() == 1
    r = client.post(record(lease), {**data, "allow_duplicate": "on"})
    assert r.status_code == 302 and Payment.objects.count() == 2
    assert first.reference.encode() in client.get(detail(first)).content  # the detail flags its twin


def test_bulk_confirm_from_the_review_queue(client, owner, lease):
    bill(lease, FEB)
    accountant = add_member(owner.organization, "accountant", all_properties=True)
    a, b, c = (pay(accountant, lease, amount) for amount in ("1000", "2000", "3000"))
    login(client, accountant)
    assert client.post(reverse("payments:review"), {"payment": [a.public_id]}).status_code == 403
    client.logout()
    login(client, owner)
    r = client.post(reverse("payments:review"), {"payment": [a.public_id, b.public_id]})
    assert r.status_code == 302 and r["Location"] == reverse("payments:review")
    for p in (a, b, c):
        p.refresh_from_db()
    assert (a.status, b.status, c.status) == ("CONFIRMED", "CONFIRMED", "PENDING_REVIEW")
    assert a.receipt.number and lease_balance(lease) == Decimal("12000.00")
    # Already-confirmed or unknown ids are ignored, not errors.
    client.post(reverse("payments:review"), {"payment": [a.public_id, "not-a-uuid"]})
    assert list(client.get(reverse("payments:review")).context["payments"]) == [c]


def test_detail_shows_rejection(client, owner, lease):
    accountant = add_member(owner.organization, "accountant", all_properties=True)
    payment = pay(accountant, lease, "900")
    login(client, owner)
    r = client.post(detail(payment), {"action": "reject", "reject-reason": "Never arrived"})
    assert r.status_code == 302
    r = client.get(detail(payment))
    assert r.status_code == 200 and b"Never arrived" in r.content and b"Rejected" in r.content
