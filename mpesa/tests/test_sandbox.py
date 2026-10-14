"""The Daraja sandbox check (D-104). Safaricom's callbacks are delivered by hand where the check waits."""

import hashlib

import pytest
from django.core.management import CommandError, call_command

from accounts.tests.factories import make_org, make_property
from billing.tests.test_invoicing import make_lease
from mpesa import c2b, sandbox, stk
from mpesa.daraja import DarajaError, FakeDarajaClient
from mpesa.models import DarajaCredentials, MpesaTransaction, StkRequest

from .test_stk import callback, paybill

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner():
    return make_org(name="Acme")


@pytest.fixture
def lease(owner):
    return make_lease(owner, make_property(owner.organization))


@pytest.fixture
def creds(owner):
    return paybill(owner)


HASHED = hashlib.sha256(sandbox.TEST_MSISDN.encode()).hexdigest()


def confirmation(account, trans_id, bill_ref, msisdn=HASHED, kind="Pay Bill", amount=1):
    return {"TransactionType": kind, "TransID": trans_id, "TransTime": "20261002101500", "TransAmount": f"{amount}.00",
            "BusinessShortCode": account.shortcode, "BillRefNumber": bill_ref, "MSISDN": msisdn, "FirstName": "John"}


def safaricom_confirms(monkeypatch, account, *, msisdn=HASHED, keep_ref=True):
    def simulate(self, **sent):
        kind = "Pay Bill" if sent["command"] == "CustomerPayBillOnline" else "Buy Goods"
        FakeDarajaClient.calls.append((account.shortcode, "simulate", sent))
        c2b.receive(account, confirmation(account, f"SIM{len(FakeDarajaClient.calls):07d}",
                                        sent["bill_ref"] if keep_ref else "", msisdn, kind, sent["amount"]))
        return {"ResponseCode": "0"}
    monkeypatch.setattr(FakeDarajaClient, "simulate_c2b", simulate)


def context(account, **kw):
    return sandbox.Context(creds=account, **{"phone": "+254712345602", "wait": 0, **kw})


def test_only_sandbox_credentials_are_accepted(creds):
    creds.environment = DarajaCredentials.Environment.PRODUCTION
    with pytest.raises(sandbox.SandboxOnly):
        sandbox.run(context(creds), ["token"])
    with pytest.raises(DarajaError, match="only be simulated"):
        FakeDarajaClient(creds).simulate_c2b(command="CustomerPayBillOnline", amount=1, msisdn="1", bill_ref="x")


def test_the_phone_form_is_recognised():
    local = "0" + sandbox.TEST_MSISDN[3:]
    assert sandbox.msisdn_form(sandbox.TEST_MSISDN) == "the number itself"
    assert sandbox.msisdn_form(HASHED) == "SHA-256 of 2547XXXXXXXX"
    assert sandbox.msisdn_form(hashlib.sha256(local.encode()).hexdigest()) == "SHA-256 of 07XXXXXXXX"
    assert sandbox.msisdn_form("a" * 64) == "a 64-character hash of an unknown form"
    assert sandbox.msisdn_form("2547****149").startswith("masked")
    assert sandbox.msisdn_form("") == "empty"


def test_a_paybill_payment_shows_the_phone_form_and_matches_by_reference(monkeypatch, creds, lease):
    safaricom_confirms(monkeypatch, creds)
    finding = sandbox.step_paybill(context(creds))
    assert finding.verdict == "matches"
    assert "SHA-256 of 2547XXXXXXXX" in finding.answer and f"matched to lease {lease.number}" in finding.answer
    tx = MpesaTransaction.objects.get(bill_ref=lease.unit.payment_reference)
    assert tx.c2b_confirmed_at is not None


def test_a_stray_callback_is_not_taken_as_the_answer(monkeypatch, creds, lease):
    def simulate(self, **sent):
        # A late confirmation from an earlier run lands first, with another amount.
        c2b.receive(creds, confirmation(creds, "STRAY00001", "", "c" * 64, "Pay Bill", sent["amount"] + 5))
        c2b.receive(creds, confirmation(creds, "OWN0000001", sent["bill_ref"], HASHED, "Pay Bill", sent["amount"]))
        return {"ResponseCode": "0"}
    monkeypatch.setattr(FakeDarajaClient, "simulate_c2b", simulate)
    ctx = context(creds)
    finding = sandbox.step_paybill(ctx)
    assert "OWN0000001" in finding.answer and finding.verdict == "matches"
    assert ctx.seen == {"OWN0000001"}


def test_a_simulate_error_body_is_a_failure(monkeypatch, creds):
    monkeypatch.setattr(FakeDarajaClient, "_post", lambda self, path, body: {
        "requestId": "1", "errorCode": "400.002.02", "errorMessage": "Invalid ShortCode"})
    with pytest.raises(DarajaError, match="Invalid ShortCode"):
        FakeDarajaClient(creds).simulate_c2b(command="CustomerPayBillOnline", amount=1, msisdn="1", bill_ref="x")
    monkeypatch.setattr(FakeDarajaClient, "_post", lambda self, path, body: {
        "ResponseDescription": "Accept the service request successfully."})
    FakeDarajaClient(creds).simulate_c2b(command="CustomerPayBillOnline", amount=1, msisdn="1", bill_ref="x")


def test_a_hash_of_another_form_differs(monkeypatch, creds, lease):
    safaricom_confirms(monkeypatch, creds, msisdn="b" * 64)
    assert sandbox.step_paybill(context(creds)).verdict == "differs"


def test_no_confirmation_is_a_failure(creds, lease):
    finding = sandbox.step_paybill(context(creds))
    assert finding.verdict == "failed" and "No confirmation arrived" in finding.answer


def test_a_till_confirmation_with_or_without_an_account_number(monkeypatch, creds, lease):
    safaricom_confirms(monkeypatch, creds, keep_ref=False)
    assert sandbox.step_till(context(creds)).verdict == "matches"
    safaricom_confirms(monkeypatch, creds, keep_ref=True)
    finding = sandbox.step_till(context(creds))
    assert finding.verdict == "differs" and "'TILLTEST'" in finding.answer


def test_the_still_processing_answer(monkeypatch, creds, lease):
    def busy(self, checkout):
        raise DarajaError("500.001.1001 The transaction is being processed")
    monkeypatch.setattr(FakeDarajaClient, "stk_query", busy)
    ctx = context(creds)
    assert sandbox.step_stk_query(ctx).verdict == "matches"
    assert ctx.stk_request.amount == 1

    def other(self, checkout):
        raise DarajaError("500.001.1099 Something new")
    monkeypatch.setattr(FakeDarajaClient, "stk_query", other)
    StkRequest.objects.update(status=StkRequest.Status.FAILED)
    assert sandbox.step_stk_query(context(creds)).verdict == "differs"


def test_a_paid_prompt_followed_by_a_c2b_confirmation(creds, lease):
    ctx = context(creds, wait=5)
    delivered = []

    def sleep(_seconds):
        request = ctx.stk_request
        if not delivered:
            stk.receive(creds, callback(request, amount="1", receipt="QSB11AA11A"))
        else:
            c2b.receive(creds, confirmation(creds, "QSB11AA11A", request.account_reference))
        delivered.append(1)
    ctx.sleep = sleep
    finding = sandbox.step_stk_c2b(ctx)
    assert finding.answer.startswith("Yes: the callback for QSB11AA11A came first")
    tx = MpesaTransaction.objects.get(trans_id="QSB11AA11A")
    assert tx.source == MpesaTransaction.Source.STK and tx.c2b_confirmed_at is not None


def test_a_paid_prompt_with_no_c2b_confirmation(owner, creds, lease):
    ctx = context(creds)
    ctx.stk_request = stk.send(lease, phone=ctx.phone, amount=1, payment_account=creds.payment_account,
                                requested_by=owner.user)
    stk.receive(creds, callback(ctx.stk_request, amount="1", receipt="QSB22BB22B"))
    finding = sandbox.step_stk_c2b(ctx)
    assert finding.answer.startswith("No: only the payment request's callback came for QSB22BB22B")
    assert MpesaTransaction.objects.get(trans_id="QSB22BB22B").c2b_confirmed_at is None


def test_the_amount_limit(creds):
    assert sandbox.step_limit(context(creds)).verdict == "differs"
    FakeDarajaClient.fail = "400.002.02 Bad Request - Invalid Amount"
    finding = sandbox.step_limit(context(creds))
    assert finding.verdict == "matches" and "Invalid Amount" in finding.answer
    assert FakeDarajaClient.calls[-1][2]["Amount"] == int(stk.MAX_AMOUNT) + 1


def test_rent_messages_with_the_link_are_measured(settings):
    settings.SMS_PRICE = "0.80"
    finding = sandbox.step_sms(sandbox.Context(creds=None, site_url="https://app.example.co.ke"))
    assert "45-character link" in finding.answer and "rent_due_soon (en)" in finding.answer
    assert "KES 0.80 a part" in finding.assumption


def test_the_network_counts_unicode_texts_in_shorter_parts():
    assert sandbox.network_parts("a" * 160) == 1
    assert sandbox.network_parts("a" * 161) == 2
    assert sandbox.network_parts("[" * 80) == 1 and sandbox.network_parts("[" * 81) == 2
    assert sandbox.network_parts("’" + "a" * 69) == 1 and sandbox.network_parts("’" + "a" * 70) == 2


def test_a_failing_step_does_not_stop_the_others(creds):
    FakeDarajaClient.fail = "boom"
    findings = sandbox.run(context(creds, phone=""), ["urls", "limit", "sms"])
    assert [(f.step, f.verdict) for f in findings] == [("urls", "failed"), ("limit", "skipped"), ("sms", "answered")]


def test_the_command(creds, tmp_path):
    out = tmp_path / "answers.md"
    call_command("daraja_sandbox_check", "--only", "sms", "--out", str(out))
    assert "| sms | answered |" in out.read_text(encoding="utf-8")
    with pytest.raises(CommandError, match="Unknown step"):
        call_command("daraja_sandbox_check", "--only", "nope")
    with pytest.raises(CommandError, match="--account is needed"):
        call_command("daraja_sandbox_check", "--only", "token")
    call_command("daraja_sandbox_check", "--account", str(creds.payment_account.public_id), "--only", "token,urls")
    creds.refresh_from_db()
    assert creds.urls_registered_at is not None
