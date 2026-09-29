"""Hand-typed M-Pesa codes that Safaricom never confirmed (D-066)."""

import datetime

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from audit.models import AuditEvent
from banking.models import StatementImport
from billing.tests.test_invoicing import FEB, bill, make_lease
from mpesa import c2b, codes, jobs, services
from mpesa.models import CodeCheck
from notifications.models import Message
from payments import services as payment_services
from payments.models import Payment, PaymentAccount, PropertyPaymentAccount
from reports import home

pytestmark = pytest.mark.django_db

FEB_3 = datetime.date(2026, 2, 3)
CODE = "QAB12CD301"


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
def creds(owner, org, prop):
    account = services.add_account(owner, type="PAYBILL", number="600123", display_name="Rent Paybill")
    PropertyPaymentAccount.objects.create(organization=org, property=prop, payment_account=account)
    creds = services.save_credentials(owner, account, environment="SANDBOX", consumer_key="k", consumer_secret="s")
    creds.urls_registered_at = timezone.make_aware(datetime.datetime(2026, 1, 1))
    creds.save()
    return creds


def later(hours=25):
    return timezone.now() + datetime.timedelta(hours=hours)


def typed(owner, lease, *, code=CODE, amount="15000", account=None, paid_at=FEB_3):
    return payment_services.record_payment(owner, lease, amount=amount, method=Payment.Method.MPESA,
                                           paid_at=paid_at, reference=code, payment_account=account)


def receive(creds, code=CODE, amount="15000.00"):
    data = {"TransactionType": "Pay Bill", "TransID": code, "TransTime": "20260203101500", "TransAmount": amount,
            "BusinessShortCode": "600123", "BillRefNumber": "ZZ-99", "MSISDN": "254799000111",
            "FirstName": "Jane", "LastName": "Doe"}
    return c2b.receive(creds, data)[0]


def flagged(owner, now=None):
    return codes.review(codes.for_member(owner), now or later()).flagged


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------


def test_code_never_received_is_flagged_after_a_day(owner, creds, lease):
    payment = typed(owner, lease, account=creds.payment_account)
    result = codes.review(codes.for_member(owner), timezone.now())
    assert (result.flagged, result.waiting) == ([], 1)
    [f] = flagged(owner)
    assert (f.payment, f.reason) == (payment, codes.NOT_FOUND)


def test_code_received_with_the_same_amount_is_not_flagged(owner, creds, lease):
    typed(owner, lease, code=CODE.lower())
    receive(creds)  # left in the inbox as already recorded by hand (D-045 item 12)
    result = codes.review(codes.for_member(owner), later())
    assert (result.flagged, result.waiting, result.cannot_check) == ([], 0, 0)


def test_code_received_with_another_amount_is_flagged_at_once(owner, creds, lease):
    payment = typed(owner, lease, amount="15000")
    receive(creds, amount="1500.00")
    [f] = flagged(owner, timezone.now())
    assert (f.payment, f.reason, f.found_amount) == (payment, codes.AMOUNT_DIFFERS, 1500)


def test_payments_made_from_a_transaction_are_never_checked(owner, creds, lease):
    tx = receive(creds)
    tx = c2b.MpesaTransaction.objects.get(pk=tx.pk)
    from mpesa import inbox
    inbox.match(owner, tx, lease)
    assert codes.unproven(Payment.objects.all()).count() == 0


def test_reversed_cash_and_blank_codes_are_not_checked(owner, creds, lease):
    reversed_ = typed(owner, lease, code="QAB12CD302")
    payment_services.reverse_payment(owner, reversed_, reason="Wrong lease")
    payment_services.record_payment(owner, lease, amount="100", method=Payment.Method.CASH, paid_at=FEB_3,
                                    reference="QAB12CD303")
    typed(owner, lease, code="")
    assert codes.unproven(Payment.objects.all()).count() == 0


def test_without_callbacks_or_a_statement_a_code_cannot_be_checked(owner, lease):
    typed(owner, lease)
    result = codes.review(codes.for_member(owner), later())
    assert (result.flagged, result.cannot_check) == ([], 1)


def test_callbacks_registered_after_the_date_paid_do_not_count(owner, creds, lease):
    creds.urls_registered_at = timezone.make_aware(datetime.datetime(2026, 2, 10))
    creds.save()
    typed(owner, lease)
    assert codes.review(codes.for_member(owner), later()).cannot_check == 1


def test_a_statement_covering_the_date_paid_counts(owner, org, lease):
    account = PaymentAccount.objects.create(organization=org, type="TILL", number="555", display_name="Till")
    typed(owner, lease, account=account)
    statement = StatementImport.objects.create(
        organization=org, payment_account=account, kind=StatementImport.Kind.MPESA, created_by=owner.user,
        status=StatementImport.Status.APPLIED, period_from=datetime.date(2026, 2, 5),
        period_to=datetime.date(2026, 2, 28))
    assert codes.review(codes.for_member(owner), later()).cannot_check == 1
    statement.period_from = datetime.date(2026, 2, 1)
    statement.save()
    [f] = flagged(owner)
    assert f.reason == codes.NOT_FOUND


def test_a_payment_on_another_account_is_checked_only_against_that_account(owner, org, creds, lease):
    personal = PaymentAccount.objects.create(organization=org, type="PERSONAL", number="0712", display_name="Phone")
    typed(owner, lease, account=personal)
    assert codes.review(codes.for_member(owner), later()).cannot_check == 1


def test_a_code_in_another_organization_does_not_prove_it(owner, creds, lease):
    other = make_org(name="Other")
    other_account = services.add_account(other, type="PAYBILL", number="700999", display_name="Theirs")
    other_creds = services.save_credentials(other, other_account, environment="SANDBOX", consumer_key="k",
                                            consumer_secret="s")
    receive(other_creds)
    typed(owner, lease, account=creds.payment_account)
    [f] = flagged(owner)
    assert f.reason == codes.NOT_FOUND


# ---------------------------------------------------------------------------
# Checking by hand
# ---------------------------------------------------------------------------


def test_marking_checked_takes_it_off_the_list(owner, creds, lease):
    payment = typed(owner, lease)
    codes.mark_checked(owner, payment, note="  Seen on the   Safaricom portal ")
    assert flagged(owner) == []
    check = CodeCheck.objects.get()
    assert (check.payment, check.note, check.checked_by) == (payment, "Seen on the Safaricom portal", owner.user)
    assert AuditEvent.objects.filter(action="mpesa.code_checked").exists()
    with pytest.raises(ValidationError, match="nothing to check"):
        codes.mark_checked(owner, payment, note="again")


def test_marking_checked_needs_a_note_and_the_capability(owner, org, prop, creds, lease):
    payment = typed(owner, lease)
    with pytest.raises(ValidationError, match="how the code was checked"):
        codes.mark_checked(owner, payment, note="  ")
    viewer = add_member(org, "viewer", all_properties=True)
    with pytest.raises(PermissionDenied):
        codes.mark_checked(viewer, payment, note="ok")
    assert codes.for_member(viewer).count() == 0
    stranger = make_org(name="Other")
    with pytest.raises(PermissionDenied):
        codes.mark_checked(stranger, payment, note="ok")


def test_scope_follows_the_lease_property(owner, org, prop, creds, lease):
    typed(owner, lease)
    elsewhere = add_member(org, "accountant", properties=[make_property(org, name="Other Court")])
    here = add_member(org, "accountant", properties=[prop])
    assert flagged(elsewhere) == [] and len(flagged(here)) == 1


# ---------------------------------------------------------------------------
# Alerts, home and pages
# ---------------------------------------------------------------------------


def test_daily_alert_once_a_day_to_those_who_have_codes(owner, org, prop, creds, lease):
    typed(owner, lease)
    elsewhere = add_member(org, "accountant", properties=[make_property(org, name="Other Court")])
    assert codes.send_alerts(later()) == 1
    assert codes.send_alerts(later(26)) == 0
    msg = Message.objects.get(type="mpesa_unverified_codes")
    assert msg.user == owner.user and "1 M-Pesa payment" in msg.body
    assert not Message.objects.filter(type="mpesa_unverified_codes", user=elsewhere.user).exists()


def test_mpesa_daily_sends_the_code_alerts(owner, creds, lease):
    typed(owner, lease)
    assert jobs.run_daily(later())["code_alerts_sent"] == 1


def test_home_task(owner, creds, lease, monkeypatch):
    typed(owner, lease)
    monkeypatch.setattr(codes, "WAIT", datetime.timedelta(0))
    [task] = [t for t in home.home(owner).tasks if t.key == "codes"]
    assert task.count == 1 and task.url == reverse("mpesa:codes")


def test_codes_page_lists_and_marks_checked(client, owner, creds, lease, monkeypatch):
    payment = typed(owner, lease)
    monkeypatch.setattr(codes, "WAIT", datetime.timedelta(0))
    login(client, owner)
    r = client.get(reverse("mpesa:codes"))
    assert r.status_code == 200 and CODE in r.content.decode() and "Not found" in r.content.decode()
    r = client.post(reverse("mpesa:codes"), {"payment": str(payment.public_id), "note": ""}, follow=True)
    assert not CodeCheck.objects.exists() and "how the code was checked" in r.content.decode()
    r = client.post(reverse("mpesa:codes"), {"payment": str(payment.public_id), "note": "Portal"})
    assert r.status_code == 302 and CodeCheck.objects.filter(payment=payment).exists()
    assert "Nothing to check" in client.get(reverse("mpesa:codes")).content.decode()


def test_codes_page_needs_mpesa_match(client, org, owner, creds, lease):
    login(client, add_member(org, "viewer", all_properties=True))
    assert client.get(reverse("mpesa:codes")).status_code == 403


def test_payment_page_warns_then_shows_the_check(client, owner, creds, lease, monkeypatch):
    payment = typed(owner, lease)
    monkeypatch.setattr(codes, "WAIT", datetime.timedelta(0))
    login(client, owner)
    page = client.get(reverse("payments:detail", args=[payment.public_id])).content.decode()
    assert "M-Pesa code not found" in page
    codes.mark_checked(owner, payment, note="Seen on the portal")
    page = client.get(reverse("payments:detail", args=[payment.public_id])).content.decode()
    assert "M-Pesa code not found" not in page and "Seen on the portal" in page


# ---------------------------------------------------------------------------
# Statement period
# ---------------------------------------------------------------------------


def test_statement_preview_keeps_the_dates_it_covers(owner, org):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from banking import services as banking_services

    account = PaymentAccount.objects.create(organization=org, type="PAYBILL", number="600555", display_name="PB")
    csv = ("Receipt No.,Completion Time,Details,Transaction Status,Paid In,Withdrawn,Other Party Info,A/C No.\n"
           "QAB12CD311,03-02-2026 10:15:00,Pay Bill,Completed,15000.00,,254799000111 - JANE DOE,A1\n"
           "QAB12CD312,20-02-2026 09:00:00,Pay Bill,Completed,500.00,,254799000111 - JANE DOE,A1\n")
    batch = banking_services.preview(owner, account, SimpleUploadedFile("s.csv", csv.encode()))
    assert (batch.period_from, batch.period_to) == (datetime.date(2026, 2, 3), datetime.date(2026, 2, 20))
