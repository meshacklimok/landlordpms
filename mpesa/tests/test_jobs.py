"""The daily M-Pesa job: retries, waiting payment requests and the reconciliation summary (D-045 step 5)."""

import datetime
from io import StringIO

import pytest
from django.core.management import call_command

from accounts.tests.factories import add_member, make_org, make_property
from billing.tests.test_invoicing import FEB, bill, make_lease
from mpesa import c2b, inbox, jobs, services, stk
from mpesa.daraja import FakeDarajaClient
from mpesa.models import MpesaTransaction, StkRequest
from notifications.models import Message

pytestmark = pytest.mark.django_db

Status = MpesaTransaction.Status
QUERY = "/mpesa/stkpushquery/v1/query"
NOW = datetime.datetime(2026, 2, 4, 3, 0, tzinfo=c2b.NAIROBI)
FEB3 = datetime.date(2026, 2, 3)


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


@pytest.fixture
def creds(owner):
    account = services.add_account(owner, type="PAYBILL", number="600123", display_name="Rent Paybill")
    return services.save_credentials(owner, account, environment="SANDBOX", consumer_key="k", consumer_secret="s",
                                     passkey="pk")


def receive(creds, n, ref="", amount="15000", when="20260203101500"):
    tx, _ = c2b.receive(creds, {"TransID": f"QAB12CD3{n:02d}", "TransAmount": amount, "TransTime": when,
                                "BusinessShortCode": "600123", "BillRefNumber": ref, "MSISDN": "254799000111"})
    return tx


def age(obj, delta):
    type(obj).objects.filter(pk=obj.pk).update(created_at=NOW - delta)


# ---------------------------------------------------------------------------
# Retries
# ---------------------------------------------------------------------------


def test_recent_and_exhausted_transactions_are_left(creds):
    fresh, tired = receive(creds, 1), receive(creds, 2)
    MpesaTransaction.objects.update(status=Status.RECEIVED)
    age(fresh, datetime.timedelta(minutes=1))
    age(tired, datetime.timedelta(hours=1))
    MpesaTransaction.objects.filter(pk=tired.pk).update(attempts=jobs.MAX_ATTEMPTS)
    assert jobs.retry_received(NOW)["retried"] == 0


def test_retry_places_a_transaction_that_failed_to_process(creds, lease, monkeypatch):
    def boom(tx):
        raise RuntimeError("database hiccup")

    monkeypatch.setattr(c2b, "process", boom)
    tx = receive(creds, 1, ref=lease.unit.payment_reference)
    assert tx.status == Status.RECEIVED and c2b.RETRY_NOTE in tx.note
    monkeypatch.undo()
    age(tx, datetime.timedelta(hours=1))
    assert jobs.retry_received(NOW) == {"retried": 1, "retry_matched": 1}
    tx.refresh_from_db()
    assert tx.status == Status.MATCHED and c2b.RETRY_NOTE not in tx.note


# ---------------------------------------------------------------------------
# Waiting payment requests
# ---------------------------------------------------------------------------


def test_waiting_request_is_queried_and_closed_when_not_paid(owner, lease, creds):
    req = stk.request_payment(owner, lease, phone="0712345602", amount=100)
    age(req, datetime.timedelta(minutes=15))
    FakeDarajaClient.replies = {QUERY: {"ResultCode": "1037", "ResultDesc": "DS timeout user cannot be reached"}}
    assert jobs.check_pending_requests(NOW) == {"stk_queried": 1, "stk_failed": 1}
    req.refresh_from_db()
    assert req.status == StkRequest.Status.FAILED and req.result_code == "1037"


def test_young_request_is_not_queried(owner, lease, creds):
    req = stk.request_payment(owner, lease, phone="0712345602", amount=100)
    age(req, datetime.timedelta(minutes=2))
    assert jobs.check_pending_requests(NOW)["stk_queried"] == 0


def test_request_still_processing_waits_then_gives_up_after_a_day(owner, lease, creds):
    req = stk.request_payment(owner, lease, phone="0712345602", amount=100)
    age(req, datetime.timedelta(minutes=30))
    FakeDarajaClient.fail = "500.001.1001: The transaction is being processed"
    assert jobs.check_pending_requests(NOW)["stk_failed"] == 0
    assert StkRequest.objects.get().status == StkRequest.Status.PENDING
    age(req, datetime.timedelta(days=2))
    assert jobs.check_pending_requests(NOW)["stk_failed"] == 1
    req.refresh_from_db()
    assert req.status == StkRequest.Status.FAILED and req.result_desc == "No answer from Safaricom."


def test_request_safaricom_says_was_paid_is_never_given_up(owner, lease, creds):
    req = stk.request_payment(owner, lease, phone="0712345602", amount=100)
    StkRequest.objects.filter(pk=req.pk).update(result_code="0")
    age(req, datetime.timedelta(days=3))
    assert jobs.check_pending_requests(NOW) == {"stk_queried": 0, "stk_failed": 0}
    assert StkRequest.objects.get().status == StkRequest.Status.PENDING


# ---------------------------------------------------------------------------
# Reconciliation summary
# ---------------------------------------------------------------------------


def test_summary_counts_yesterdays_payments(owner, lease, creds):
    receive(creds, 1, ref=lease.unit.payment_reference)                       # auto
    manual = receive(creds, 2, ref="nope", amount="500")                      # by hand
    inbox.match(owner, manual, lease)
    ignored = receive(creds, 3, amount="200")
    inbox.ignore(owner, ignored, reason="refunded")
    receive(creds, 4, amount="300")                                           # waiting
    receive(creds, 5, when="20260204080000")                                  # another day
    rows = jobs.summarize(owner.organization, FEB3)
    assert rows == [{"account": "Rent Paybill", "count": 4, "total": 16000, "auto": 1, "manual": 1,
                     "ignored": 1, "waiting": 1}]


def test_summary_goes_in_app_to_org_wide_viewers_once(owner, org, prop, creds):
    receive(creds, 1)
    accountant = add_member(org, "accountant", all_properties=True)
    scoped = add_member(org, "accountant", properties=[prop])
    caretaker = add_member(org, "caretaker", all_properties=True)
    assert jobs.send_summaries(FEB3) == 2
    assert jobs.send_summaries(FEB3) == 0
    to = set(Message.objects.filter(type="mpesa_daily_summary").values_list("user_id", flat=True))
    assert to == {owner.user_id, accountant.user_id}
    assert scoped.user_id not in to and caretaker.user_id not in to
    body = Message.objects.filter(type="mpesa_daily_summary").first().body
    assert "3 Feb 2026" in body and "Rent Paybill: 1 received" in body and "1 still waiting" in body


def test_no_summary_without_payments(owner, creds):
    assert jobs.send_summaries(FEB3) == 0


def test_command_runs_the_whole_job(owner, creds):
    out = StringIO()
    call_command("mpesa_daily", stdout=out)
    assert "retried: 0" in out.getvalue() and "summaries sent: 0" in out.getvalue()
