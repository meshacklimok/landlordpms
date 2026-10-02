"""C2B confirmations: storing, duplicates, matching and system confirmation (D-045 step 2)."""

import datetime
import json
from decimal import Decimal

import pytest
from django.urls import reverse

from accounts.tests.factories import make_org, make_property
from audit.models import AuditEvent
from billing.invoicing import lease_balance
from billing.models import Invoice, LedgerEntry
from billing.tests.test_invoicing import FEB, bill, make_lease
from leases.models import LeasePayer
from mpesa import c2b, services
from mpesa.models import MpesaTransaction
from payments.models import Payment, PropertyPaymentAccount

pytestmark = pytest.mark.django_db

Status = MpesaTransaction.Status


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def owner():
    return make_org(name="Acme")


@pytest.fixture
def prop(owner):
    return make_property(owner.organization)


@pytest.fixture
def lease(owner, prop):
    return make_lease(owner, prop)


@pytest.fixture
def creds(owner):
    account = services.add_account(owner, type="PAYBILL", number="600123", display_name="Rent Paybill")
    return services.save_credentials(owner, account, environment="SANDBOX", consumer_key="k", consumer_secret="s")


def payload(**kw):
    data = {"TransactionType": "Pay Bill", "TransID": "QAB12CD34E", "TransTime": "20260203101500",
            "TransAmount": "15000.00", "BusinessShortCode": "600123", "BillRefNumber": "",
            "MSISDN": "254799000111", "FirstName": "Jane", "MiddleName": "", "LastName": "Doe"}
    data.update(kw)
    return data


def post(client, creds, data, kind="confirm", token=None):
    url = reverse("hook_daraja", args=[token or creds.callback_token, kind])
    return client.post(url, data=json.dumps(data), content_type="application/json")


# ---------------------------------------------------------------------------
# Reference: confident, confirmed at once
# ---------------------------------------------------------------------------


def test_reference_match_confirms_allocates_and_receipts_without_a_reviewer(client, creds, lease):
    invoice = bill(lease, FEB)
    response = post(client, creds, payload(BillRefNumber=lease.unit.payment_reference))
    assert response.status_code == 200 and response.json()["ResultCode"] == 0

    tx = MpesaTransaction.objects.get()
    assert (tx.status, tx.matched_by, tx.matched_by_user) == (Status.MATCHED, "REFERENCE", None)
    assert tx.amount == Decimal("15000.00") and tx.payer_name == "Jane Doe"
    assert tx.paid_at == datetime.datetime(2026, 2, 3, 7, 15, tzinfo=datetime.UTC)  # 10:15 in Nairobi
    payment = tx.payment
    assert (payment.status, payment.method, payment.reference) == (Payment.Status.CONFIRMED, "MPESA", "QAB12CD34E")
    assert payment.recorded_by is None and payment.confirmed_by is None
    assert payment.paid_at == datetime.date(2026, 2, 3) and payment.payment_account == creds.payment_account
    assert payment.tenant == lease.primary_tenant and payment.receipt.number.startswith("RCT-")
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.PAID and lease_balance(lease) == 0
    assert LedgerEntry.objects.get(payment=payment).created_by is None
    events = AuditEvent.objects.filter(action__in=["payment.record", "payment.confirm"])
    assert events.count() == 2
    assert all(e.actor is None and e.changes["source"] == [None, "mpesa:QAB12CD34E"] for e in events)


@pytest.mark.parametrize("typed", ["{ref}", "{low}", " {spaced} ", "{nodash}"])
def test_reference_ignores_case_spaces_and_dashes(client, creds, lease, typed):
    ref = lease.unit.payment_reference
    typed = typed.format(ref=ref, low=ref.lower(), spaced=ref.replace("-", " - "), nodash=ref.replace("-", ""))
    post(client, creds, payload(BillRefNumber=typed))
    assert MpesaTransaction.objects.get().status == Status.MATCHED


def test_overpayment_stays_as_credit(client, creds, lease):
    bill(lease, FEB)
    post(client, creds, payload(BillRefNumber=lease.unit.payment_reference, TransAmount="20000"))
    payment = MpesaTransaction.objects.get().payment
    assert payment.unallocated == Decimal("5000.00") and lease_balance(lease) == Decimal("-5000.00")


def test_the_tenant_whose_number_paid_is_recorded(client, creds, owner, prop, lease):
    tenant = lease.primary_tenant
    post(client, creds, payload(BillRefNumber=lease.unit.payment_reference, MSISDN=tenant.phone.lstrip("+")))
    assert MpesaTransaction.objects.get().payment.tenant == tenant


# ---------------------------------------------------------------------------
# Duplicates and bad callbacks
# ---------------------------------------------------------------------------


def test_repeated_callback_is_acknowledged_and_changes_nothing(client, creds, lease):
    bill(lease, FEB)
    data = payload(BillRefNumber=lease.unit.payment_reference)
    assert post(client, creds, data).status_code == 200
    assert post(client, creds, {**data, "TransAmount": "1"}).json()["ResultCode"] == 0
    assert MpesaTransaction.objects.count() == 1 and Payment.objects.count() == 1
    assert MpesaTransaction.objects.get().amount == Decimal("15000.00")


def test_unknown_token_is_404_and_missing_code_is_refused(client, creds):
    assert post(client, creds, payload(), token="nope").status_code == 404
    data = payload()
    del data["TransID"]
    assert post(client, creds, data).status_code == 400
    assert not MpesaTransaction.objects.exists()


def test_stk_callback_without_checkout_id_is_refused(client, creds):
    assert post(client, creds, payload(), kind="stk").status_code == 400
    assert post(client, creds, payload(), kind="nope").status_code == 404


def test_wrong_shortcode_is_flagged_and_never_matched(client, creds, lease):
    post(client, creds, payload(BillRefNumber=lease.unit.payment_reference, BusinessShortCode="999999"))
    tx = MpesaTransaction.objects.get()
    assert tx.status == Status.FLAGGED and "999999" in tx.note and tx.payment is None


@pytest.mark.parametrize("amount", ["abc", "0", "-5", ""])
def test_unreadable_amount_is_flagged(client, creds, lease, amount):
    post(client, creds, payload(BillRefNumber=lease.unit.payment_reference, TransAmount=amount))
    tx = MpesaTransaction.objects.get()
    assert tx.status == Status.FLAGGED and tx.amount == 0 and tx.raw_payload["TransAmount"] == amount


def test_unreadable_time_uses_the_time_received(client, creds, lease):
    post(client, creds, payload(BillRefNumber=lease.unit.payment_reference, TransTime="yesterday"))
    tx = MpesaTransaction.objects.get()
    assert tx.status == Status.MATCHED and "Time could not be read" in tx.note


def test_a_processing_failure_leaves_it_received_for_a_retry(client, creds, lease, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("database hiccup")

    monkeypatch.setattr(c2b.payment_services, "record_system_payment", boom)
    assert post(client, creds, payload(BillRefNumber=lease.unit.payment_reference)).status_code == 200
    tx = MpesaTransaction.objects.get()
    assert tx.status == Status.RECEIVED and tx.attempts == 1 and not Payment.objects.exists()
    monkeypatch.undo()
    c2b.process(tx)
    tx.refresh_from_db()
    assert tx.status == Status.MATCHED and tx.attempts == 2 and c2b.RETRY_NOTE not in tx.note


# ---------------------------------------------------------------------------
# Reference that cannot be trusted
# ---------------------------------------------------------------------------


def test_unknown_reference_waits_in_the_inbox(client, creds, lease):
    post(client, creds, payload(BillRefNumber="ZZ-99"))
    tx = MpesaTransaction.objects.get()
    assert tx.status == Status.UNMATCHED and tx.payment is None and tx.suggested_lease is None
    assert tx.note == "No unit has this account reference."


def test_reference_on_a_property_the_account_does_not_serve(client, creds, owner, lease):
    other = make_property(owner.organization)
    PropertyPaymentAccount.objects.create(organization=owner.organization, property=other,
                                          payment_account=creds.payment_account)
    post(client, creds, payload(BillRefNumber=lease.unit.payment_reference))
    tx = MpesaTransaction.objects.get()
    assert tx.status == Status.UNMATCHED and "does not collect for" in tx.note


def test_account_serving_the_property_matches(client, creds, owner, lease):
    PropertyPaymentAccount.objects.create(organization=owner.organization, property=lease.unit.property,
                                          payment_account=creds.payment_account)
    post(client, creds, payload(BillRefNumber=lease.unit.payment_reference))
    assert MpesaTransaction.objects.get().status == Status.MATCHED


def test_unit_without_an_active_lease(client, creds, owner, prop):
    from properties import services as property_services

    unit = property_services.create_unit(owner, prop, code="B7")
    post(client, creds, payload(BillRefNumber=unit.payment_reference))
    tx = MpesaTransaction.objects.get()
    assert tx.status == Status.UNMATCHED and "no active lease" in tx.note


def test_reference_from_another_organization_does_not_match(client, creds):
    other = make_org(name="Other")
    theirs = make_lease(other, make_property(other.organization))
    post(client, creds, payload(BillRefNumber=theirs.unit.payment_reference, MSISDN=theirs.primary_tenant.phone))
    tx = MpesaTransaction.objects.get()
    assert tx.status == Status.UNMATCHED and tx.suggested_lease is None


# ---------------------------------------------------------------------------
# Phone: a suggestion only
# ---------------------------------------------------------------------------


def test_payer_number_suggests_the_lease_but_confirms_nothing(client, creds, lease):
    post(client, creds, payload(MSISDN=lease.primary_tenant.phone.lstrip("+")))
    tx = MpesaTransaction.objects.get()
    assert tx.status == Status.UNMATCHED and tx.suggested_lease == lease and tx.payment is None
    assert tx.payer_phone == lease.primary_tenant.phone
    assert not Payment.objects.exists()


def test_hashed_number_suggests_the_lease_and_is_not_textable(client, creds, lease):
    hashed = c2b.phone_hash(lease.primary_tenant.phone)
    post(client, creds, payload(MSISDN=hashed.upper()))
    tx = MpesaTransaction.objects.get()
    assert tx.suggested_lease == lease and tx.payer_phone == "" and tx.msisdn_hash == hashed


def test_extra_payer_number_suggests_the_lease(client, creds, owner, lease):
    LeasePayer.objects.create(organization=owner.organization, lease=lease, phone="+254733000999")
    post(client, creds, payload(MSISDN="0733000999"))
    assert MpesaTransaction.objects.get().suggested_lease == lease


def test_shared_number_is_decided_by_the_balance(client, creds, owner, prop):
    # make_lease gives both tenants the same phone.
    a, b = make_lease(owner, prop, code="A1"), make_lease(owner, prop, code="B1", rent=12000)
    assert a.primary_tenant.phone == b.primary_tenant.phone
    bill(a, FEB), bill(b, FEB)
    post(client, creds, payload(MSISDN=a.primary_tenant.phone, TransAmount="12000"))
    assert MpesaTransaction.objects.get().suggested_lease == b


def test_shared_number_without_a_clear_winner_suggests_nothing(client, creds, owner, prop):
    a = make_lease(owner, prop, code="A1")
    make_lease(owner, prop, code="B1")
    post(client, creds, payload(MSISDN=a.primary_tenant.phone, TransAmount="100"))
    assert MpesaTransaction.objects.get().suggested_lease is None


def test_msisdn_parsing():
    assert c2b._msisdn("254712345678") == ("+254712345678", c2b.phone_hash("+254712345678"))
    assert c2b._msisdn("rubbish") == ("", "")
    assert c2b.phone_hash("+254712345678") == c2b.phone_hash("254712345678")
