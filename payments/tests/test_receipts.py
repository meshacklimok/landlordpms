"""Receipt numbering and PDF (doc 11 §9, D-043)."""

from decimal import Decimal

import pytest

from accounts.tests.factories import make_org, make_property
from billing.tests.test_invoicing import FEB, bill, make_lease
from payments import receipts, services
from payments.models import Payment

from .test_services import pay

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def test_numbers_run_per_organization_and_pdf_is_stored(tmp_path):
    a, b = make_org(), make_org()
    lease_a = make_lease(a, make_property(a.organization))
    lease_b = make_lease(b, make_property(b.organization))
    first, second = pay(a, lease_a, "100"), pay(a, lease_a, "200")
    other = pay(b, lease_b, "100")
    assert [p.receipt.number for p in (first, second, other)] == [
        "RCT-2026-000001", "RCT-2026-000002", "RCT-2026-000001"]
    receipt = first.receipt
    assert receipt.pdf.name.startswith("receipts/2026/") and receipt.pdf.name.endswith(".pdf")
    assert "RCT" not in receipt.pdf.name  # random name: not guessable from the number
    with receipt.pdf.open("rb") as f:
        assert f.read(5) == b"%PDF-"


def test_receipt_data_lists_allocations_credit_and_balance():
    owner = make_org()
    lease = make_lease(owner, make_property(owner.organization))
    bill(lease, FEB)
    payment = pay(owner, lease, "20000", reference="QXY99")
    d = receipts.receipt_data(payment.receipt)
    assert d["number"] == "RCT-2026-000001" and d["payment_reference"] == "QXY99"
    assert d["tenant"] == lease.primary_tenant.name and d["unit"] == lease.unit.code
    assert [n for n, _amount in d["allocations"]] == [lease.invoices.get().number]
    assert d["unallocated"] and d["balance_label"] == "In credit"
    assert receipts.render_pdf(payment.receipt).startswith(b"%PDF-")


def test_pending_and_rejected_payments_get_no_receipt():
    owner = make_org()
    lease = make_lease(owner, make_property(owner.organization))
    from accounts.tests.factories import add_member

    accountant = add_member(owner.organization, "accountant", all_properties=True)
    payment = pay(accountant, lease, "500")
    services.reject_payment(owner, payment, reason="Duplicate")
    assert not Payment.objects.filter(receipt__isnull=False).exists()
    assert payment.amount == Decimal("500.00")
