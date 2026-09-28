"""Payment requests (STK push): sending, the callback, status queries and the pages (D-045 step 4)."""

import json
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from audit.models import AuditEvent
from billing.invoicing import lease_balance
from billing.tests.test_invoicing import FEB, bill, make_lease
from mpesa import c2b, services, stk
from mpesa.daraja import FakeDarajaClient
from mpesa.models import MpesaTransaction, StkRequest
from payments.models import Payment, PropertyPaymentAccount

pytestmark = pytest.mark.django_db

Status = StkRequest.Status
TENANT_PHONE = "+254712345602"  # make_lease's tenant for a two-letter unit code


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def owner():
    return make_org(name="Acme")


@pytest.fixture
def org(owner):
    return owner.organization


@pytest.fixture
def prop(org):
    return make_property(org)


@pytest.fixture
def lease(owner, prop):
    lease = make_lease(owner, prop)
    bill(lease, FEB)
    return lease


def paybill(owner, number="600123", passkey="pk", type="PAYBILL"):
    account = services.add_account(owner, type=type, number=number, display_name=f"Paybill {number}")
    return services.save_credentials(owner, account, environment="SANDBOX", consumer_key="k", consumer_secret="s",
                                     passkey=passkey)


@pytest.fixture
def creds(owner):
    return paybill(owner)


def send(owner, lease, **kw):
    return stk.request_payment(owner, lease, **{"phone": "0712345602", "amount": 15000, **kw})


def callback(req, code=0, receipt="QST12AB34C", amount="15000", phone=254712345602, **kw):
    cb = {"MerchantRequestID": req.merchant_request_id, "CheckoutRequestID": req.checkout_request_id,
          "ResultCode": code, "ResultDesc": kw.pop("desc", "The service request is processed successfully.")}
    if code == 0:
        items = [{"Name": "Amount", "Value": amount}, {"Name": "MpesaReceiptNumber", "Value": receipt},
                 {"Name": "TransactionDate", "Value": 20260203101500}, {"Name": "PhoneNumber", "Value": phone}]
        cb["CallbackMetadata"] = {"Item": [i for i in items if i["Value"] is not None]}
    return {"Body": {"stkCallback": {**cb, **kw}}}


def post_hook(client, creds, data):
    return client.post(reverse("hook_daraja", args=[creds.callback_token, "stk"]), data=json.dumps(data),
                       content_type="application/json")


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def test_request_is_sent_to_safaricom_and_kept_pending(owner, lease, creds):
    req = send(owner, lease)
    assert req.status == Status.PENDING and req.phone == TENANT_PHONE and req.amount == Decimal("15000")
    assert req.checkout_request_id.startswith("ws_CO_fake_") and req.payment_account == creds.payment_account
    assert req.account_reference == lease.unit.payment_reference[:12]
    _, path, body = FakeDarajaClient.calls[-1]
    assert path == "/mpesa/stkpush/v1/processrequest"
    assert body["PhoneNumber"] == "254712345602" and body["Amount"] == 15000
    assert body["CallBackURL"].endswith(f"/{creds.callback_token}/stk/")
    assert AuditEvent.objects.filter(action="mpesa.stk_request").count() == 1


def test_refusal_by_safaricom_is_kept_as_failed(owner, lease, creds):
    FakeDarajaClient.fail = "Invalid Access Token"
    with pytest.raises(ValidationError, match="Invalid Access Token"):
        send(owner, lease)
    req = StkRequest.objects.get()
    assert req.status == Status.FAILED and "Invalid Access Token" in req.result_desc


@pytest.mark.parametrize("amount", ["0", "-5", "100.50", "abc", "250001"])
def test_amount_must_be_whole_shillings_in_range(owner, lease, creds, amount):
    with pytest.raises(ValidationError):
        send(owner, lease, amount=amount)
    assert not StkRequest.objects.exists()


@pytest.mark.parametrize("phone", ["12", "+14155550100"])
def test_phone_must_be_kenyan(owner, lease, creds, phone):
    with pytest.raises(ValidationError):
        send(owner, lease, phone=phone)


def test_needs_a_paybill_with_passkey(owner, lease):
    paybill(owner, passkey="")
    paybill(owner, number="5123456", type="TILL")
    assert stk.stk_accounts(lease) == []
    with pytest.raises(ValidationError, match="passkey"):
        send(owner, lease)


def test_only_accounts_serving_the_property_default_first(owner, org, prop, lease):
    general = paybill(owner, "600111")
    other_prop = make_property(org, name="Elsewhere")
    elsewhere = paybill(owner, "600222")
    PropertyPaymentAccount.objects.create(organization=org, property=other_prop,
                                          payment_account=elsewhere.payment_account)
    mine = paybill(owner, "600333")
    PropertyPaymentAccount.objects.create(organization=org, property=prop, payment_account=mine.payment_account,
                                          is_default=True)
    assert [c.pk for c in stk.stk_accounts(lease)] == [mine.pk, general.pk]
    assert send(owner, lease).payment_account == mine.payment_account
    assert send(owner, lease, phone="0711000222",
                payment_account=general.payment_account).payment_account == general.payment_account
    with pytest.raises(ValidationError):
        send(owner, lease, phone="0711000333", payment_account=elsewhere.payment_account)


def test_second_request_to_the_same_phone_waits(owner, lease, creds):
    send(owner, lease)
    with pytest.raises(ValidationError, match="two minutes"):
        send(owner, lease)
    send(owner, lease, phone="0711000222")


def test_caretaker_and_other_orgs_cannot_request(owner, org, lease, creds):
    caretaker = add_member(org, "caretaker", all_properties=True)
    with pytest.raises(PermissionDenied):
        send(caretaker, lease)
    other = make_org(name="Other")
    with pytest.raises(PermissionDenied):
        send(other, lease)


def test_accountant_limited_to_other_property_cannot_request(org, lease, creds):
    accountant = add_member(org, "accountant", properties=[make_property(org, name="Elsewhere")])
    with pytest.raises(PermissionDenied):
        send(accountant, lease)


# ---------------------------------------------------------------------------
# The callback
# ---------------------------------------------------------------------------


def test_paid_callback_confirms_on_the_lease(client, owner, lease, creds):
    req = send(owner, lease)
    assert post_hook(client, creds, callback(req)).json()["ResultCode"] == 0
    req.refresh_from_db()
    tx = req.transaction
    assert req.status == Status.PAID and tx.trans_id == "QST12AB34C"
    assert tx.source == MpesaTransaction.Source.STK and tx.payer_phone == TENANT_PHONE
    assert tx.status == MpesaTransaction.Status.MATCHED and tx.matched_by == MpesaTransaction.MatchedBy.STK
    assert tx.payment.lease == lease and tx.payment.status == Payment.Status.CONFIRMED
    assert lease_balance(lease) == 0


def test_paid_callback_confirms_even_when_the_reference_is_wrong(client, owner, lease, creds):
    lease.unit.payment_reference = "NOT-UNIQUE"
    lease.unit.save()
    req = send(owner, lease)
    post_hook(client, creds, callback(req, amount="5000"))
    assert Payment.objects.get().amount == Decimal("5000.00")
    assert lease_balance(lease) == Decimal("10000.00")


@pytest.mark.parametrize("code", [1032, 1037, 1, 2001])
def test_failed_callback_records_nothing(client, owner, lease, creds, code):
    req = send(owner, lease)
    post_hook(client, creds, callback(req, code=code, desc="Request cancelled by user"))
    req.refresh_from_db()
    assert req.status == Status.FAILED and req.result_code == str(code)
    assert req.result_desc == "Request cancelled by user"
    assert not MpesaTransaction.objects.exists() and not Payment.objects.exists()


def test_repeated_callback_changes_nothing(client, owner, lease, creds):
    req = send(owner, lease)
    post_hook(client, creds, callback(req))
    post_hook(client, creds, callback(req))
    post_hook(client, creds, callback(req, code=1032))
    assert Payment.objects.count() == 1 and StkRequest.objects.get().status == Status.PAID


def test_c2b_confirmation_for_the_same_receipt_is_a_duplicate(client, owner, lease, creds):
    req = send(owner, lease)
    post_hook(client, creds, callback(req))
    tx, created = c2b.receive(creds, {"TransID": "QST12AB34C", "TransAmount": "15000", "TransTime": "20260203101500",
                                      "BusinessShortCode": "600123", "BillRefNumber": "", "MSISDN": "254712345602"})
    assert not created and Payment.objects.count() == 1


def test_c2b_confirmation_first_is_then_placed_by_the_callback(client, owner, lease, creds):
    lease.unit.payment_reference = "XX"
    lease.unit.save()
    req = send(owner, lease)
    tx, _ = c2b.receive(creds, {"TransID": "QST12AB34C", "TransAmount": "15000", "TransTime": "20260203101500",
                                "BusinessShortCode": "600123", "BillRefNumber": "XX1", "MSISDN": "254799000111"})
    assert tx.status == MpesaTransaction.Status.UNMATCHED
    post_hook(client, creds, callback(req))
    tx.refresh_from_db()
    req.refresh_from_db()
    assert req.status == Status.PAID and req.transaction == tx
    assert tx.status == MpesaTransaction.Status.MATCHED and tx.payment.lease == lease
    assert tx.matched_by == MpesaTransaction.MatchedBy.STK


def test_callback_for_an_ended_lease_goes_to_the_inbox(client, owner, lease, creds):
    req = send(owner, lease)
    lease.archived_at = lease.created_at
    lease.save()
    post_hook(client, creds, callback(req))
    tx = MpesaTransaction.objects.get()
    assert tx.status == MpesaTransaction.Status.UNMATCHED and lease.number in tx.note
    assert StkRequest.objects.get().status == Status.PAID and not Payment.objects.exists()


def test_unknown_or_other_accounts_request_is_ignored(client, owner, lease, creds):
    req = send(owner, lease)
    other = paybill(owner, "600999")
    assert post_hook(client, other, callback(req)).status_code == 200
    assert StkRequest.objects.get().status == Status.PENDING and not MpesaTransaction.objects.exists()
    data = callback(req)
    data["Body"]["stkCallback"]["CheckoutRequestID"] = "ws_CO_unknown"
    assert post_hook(client, creds, data).status_code == 200


def test_paid_without_receipt_stays_pending(client, owner, lease, creds):
    req = send(owner, lease)
    post_hook(client, creds, callback(req, receipt=None))
    req.refresh_from_db()
    assert req.status == Status.PENDING and "no receipt" in req.result_desc
    assert not MpesaTransaction.objects.exists()


def test_unreadable_amount_is_flagged(client, owner, lease, creds):
    req = send(owner, lease)
    post_hook(client, creds, callback(req, amount="x"))
    assert MpesaTransaction.objects.get().status == MpesaTransaction.Status.FLAGGED
    assert not Payment.objects.exists()


# ---------------------------------------------------------------------------
# Asking Safaricom
# ---------------------------------------------------------------------------


QUERY = "/mpesa/stkpushquery/v1/query"


def test_check_status_records_a_failure(owner, lease, creds):
    req = send(owner, lease)
    FakeDarajaClient.replies = {QUERY: {"ResponseCode": "0", "ResultCode": "1032",
                                        "ResultDesc": "Request cancelled by user"}}
    req = stk.check_status(req, actor=owner)
    assert req.status == Status.FAILED and req.result_code == "1032"


def test_check_status_paid_waits_for_the_callback(owner, lease, creds):
    req = send(owner, lease)
    FakeDarajaClient.replies = {QUERY: {"ResponseCode": "0", "ResultCode": "0", "ResultDesc": "ok"}}
    req = stk.check_status(req)
    assert req.status == Status.PENDING and req.result_code == "0" and not Payment.objects.exists()


def test_check_status_still_processing(owner, lease, creds):
    req = send(owner, lease)
    FakeDarajaClient.fail = "500.001.1001: The transaction is being processed"
    with pytest.raises(ValidationError, match="not answered"):
        stk.check_status(req, actor=owner)
    assert StkRequest.objects.get().status == Status.PENDING


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def test_lease_page_offers_the_request_only_with_a_passkey(client, owner, lease):
    login(client, owner)
    url = reverse("leases:detail", args=[lease.public_id])
    assert "Request M-Pesa payment" not in client.get(url).content.decode()
    paybill(owner)
    assert "Request M-Pesa payment" in client.get(url).content.decode()


def test_request_page_sends_and_lists(client, owner, lease, creds):
    login(client, owner)
    url = reverse("mpesa:request", args=[lease.public_id])
    page = client.get(url).content.decode()
    assert TENANT_PHONE in page and 'value="15000"' in page
    response = client.post(url, {"action": "send", "phone": TENANT_PHONE, "amount": "15000"})
    assert response.status_code == 302
    page = client.get(url).content.decode()
    assert "Waiting for the tenant" in page and 'http-equiv="refresh"' in page
    assert client.post(url, {"action": "send", "phone": TENANT_PHONE, "amount": "15000"}).status_code == 400


def test_request_page_check_action(client, owner, lease, creds):
    login(client, owner)
    req = send(owner, lease)
    FakeDarajaClient.replies = {QUERY: {"ResultCode": "1037", "ResultDesc": "Timeout"}}
    url = reverse("mpesa:request", args=[lease.public_id])
    client.post(url, {"action": "check", "request": str(req.public_id)})
    assert StkRequest.objects.get().status == Status.FAILED


def test_request_page_hidden_from_caretaker_and_other_orgs(client, org, lease, creds):
    url = reverse("mpesa:request", args=[lease.public_id])
    login(client, add_member(org, "caretaker", all_properties=True))
    assert client.get(url).status_code in (403, 404)
    login(client, make_org(name="Other"))
    assert client.get(url).status_code == 404
