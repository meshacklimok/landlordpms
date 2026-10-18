"""Landlords paying their subscription online, into our Paybill (Daraja) or our Paystack (D-126)."""

import datetime
import hashlib
import hmac
import json
from decimal import Decimal

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from accounts.tests.factories import add_member, make_org, make_user
from accounts.tests.test_isolation import login
from audit.models import AuditEvent
from mpesa.daraja import FakeDarajaClient
from subscriptions import collect, services
from subscriptions.models import (
    Plan,
    PlatformCheckout,
    PlatformPaySwitch,
    PlatformTransaction,
    Subscription,
    SubscriptionInvoice,
    SubscriptionPayment,
)
from subscriptions.paystack import FakePaystackClient

pytestmark = pytest.mark.django_db

TOKEN = "t" * 40
KEY = "sk_test_platform123"
CStatus = PlatformCheckout.Status
TStatus = PlatformTransaction.Status


@pytest.fixture(autouse=True)
def _platform(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.SITE_URL = "https://app.example.com"
    settings.PLATFORM_DARAJA_ENVIRONMENT = "SANDBOX"
    settings.PLATFORM_DARAJA_SHORTCODE = "888555"
    settings.PLATFORM_DARAJA_CONSUMER_KEY = "ck"
    settings.PLATFORM_DARAJA_CONSUMER_SECRET = "cs"
    settings.PLATFORM_DARAJA_PASSKEY = "pk"
    settings.PLATFORM_CALLBACK_TOKEN = TOKEN
    settings.PLATFORM_PAYSTACK_SECRET_KEY = KEY
    settings.PLATFORM_VAT_REGISTERED = False
    settings.OPS_ALERT_EMAILS = "ops@example.com"
    settings.MPESA_ALLOWED_IPS = settings.PAYSTACK_ALLOWED_IPS = []


@pytest.fixture
def owner():
    return make_org(name="Acme")


@pytest.fixture
def invoice(owner):
    return services.choose_plan(owner, Plan.objects.get(key="starter"), "MONTHLY")  # KES 799


def stk_callback(checkout, code=0, receipt="QSUB12AB34", amount=799):
    cb = {"MerchantRequestID": "m", "CheckoutRequestID": checkout.reference, "ResultCode": code,
          "ResultDesc": "ok" if code == 0 else "Request cancelled by user"}
    if code == 0:
        cb["CallbackMetadata"] = {"Item": [
            {"Name": "Amount", "Value": amount}, {"Name": "MpesaReceiptNumber", "Value": receipt},
            {"Name": "TransactionDate", "Value": 20260310101500}, {"Name": "PhoneNumber", "Value": 254712000111}]}
    return {"Body": {"stkCallback": cb}}


def c2b_payload(code="QSUB12AB34", amount="799", account="", shortcode="888555"):
    return {"TransID": code, "TransAmount": amount, "TransTime": "20260310101500", "BusinessShortCode": shortcode,
            "BillRefNumber": account, "MSISDN": "254712000111", "FirstName": "Jane", "LastName": "Wanjiru"}


def ps_event(checkout, status="success", amount=79900, name="charge.success", **kw):
    return {"event": name, "data": {"id": 5551, "reference": checkout.reference, "status": status, "amount": amount,
                                    "currency": "KES", "paid_at": "2026-03-10T07:15:00.000Z",
                                    "gateway_response": "Approved", "customer": {"email": "a@b.c"}, **kw}}


def hook(client, kind, payload, token=TOKEN, key=KEY):
    body = json.dumps(payload).encode()
    headers = {}
    if kind == "ps":
        headers["HTTP_X_PAYSTACK_SIGNATURE"] = hmac.new(key.encode(), body, hashlib.sha512).hexdigest()
    return client.post(reverse("hook_platform", args=[token, kind]), data=body, content_type="application/json",
                       **headers)


def paystack_checkout(owner, invoice):
    collect.start_paystack(owner, invoice)
    return PlatformCheckout.objects.get(provider=PlatformCheckout.Provider.PAYSTACK)


def assert_paid(invoice, method):
    invoice.refresh_from_db()
    assert invoice.status == SubscriptionInvoice.Status.PAID
    payment = SubscriptionPayment.objects.get(invoice=invoice)
    assert payment.method == method and payment.receipt
    assert Subscription.objects.get(organization=invoice.organization).status == Subscription.Status.ACTIVE
    return payment


# ---------------------------------------------------------------------------
# What is offered
# ---------------------------------------------------------------------------


def test_methods_follow_the_keys_and_the_switch(settings):
    assert collect.methods() == {"mpesa": True, "paystack": True}
    switch = PlatformPaySwitch.get()
    switch.mpesa_on = False
    switch.save()
    assert collect.methods() == {"mpesa": False, "paystack": True}
    settings.PLATFORM_PAYSTACK_SECRET_KEY = ""
    assert collect.methods() == {"mpesa": False, "paystack": False}
    switch.mpesa_on = True
    switch.save()
    settings.PLATFORM_DARAJA_PASSKEY = ""  # Paybill by hand still works; the prompt needs the passkey
    assert collect.methods()["mpesa"] is False and collect.mpesa_configured()


def test_switched_off_refuses_to_start(owner, invoice):
    PlatformPaySwitch.objects.update_or_create(pk=1, defaults={"mpesa_on": False, "paystack_on": False})
    with pytest.raises(ValidationError):
        collect.start_mpesa(owner, invoice, "0712000111")
    with pytest.raises(ValidationError):
        collect.start_paystack(owner, invoice)
    assert not FakeDarajaClient.calls and not FakePaystackClient.calls


def test_account_numbers(invoice):
    assert collect.account_number(invoice) == invoice.number.replace("LPM-INV-", "").replace("-", "")
    assert len(collect.account_number(invoice)) <= 12
    for typed in (collect.account_number(invoice), invoice.number, invoice.number.lower().replace("-", " ")):
        assert collect.invoice_for_account(typed) == invoice
    assert collect.invoice_for_account("") is None and collect.invoice_for_account("999") is None


def test_the_page_offers_both_ways(client, owner, invoice):
    login(client, owner)
    page = client.get(reverse("subscriptions:page")).content.decode()
    assert reverse("subscriptions:pay_mpesa") in page and reverse("subscriptions:pay_paystack") in page
    assert collect.account_number(invoice) in page


# ---------------------------------------------------------------------------
# Our Paybill: the prompt
# ---------------------------------------------------------------------------


def test_prompt_pays_the_invoice_and_starts_the_plan(client, owner, invoice):
    checkout = collect.start_mpesa(owner, invoice, "0712000111")
    shortcode, path, body = FakeDarajaClient.calls[-1]
    assert shortcode == "888555" and path.startswith("/mpesa/stkpush/")
    assert body["Amount"] == 799 and body["AccountReference"] == collect.account_number(invoice)
    assert body["CallBackURL"] == f"https://app.example.com/hooks/plat/{TOKEN}/stk/"
    assert checkout.status == CStatus.PENDING and checkout.phone == "+254712000111"
    assert AuditEvent.objects.filter(action="subscription_checkout.start").exists()

    assert hook(client, "stk", stk_callback(checkout)).json()["ResultCode"] == 0
    checkout.refresh_from_db()
    assert checkout.status == CStatus.PAID
    payment = assert_paid(invoice, SubscriptionPayment.Method.MPESA)
    tx = PlatformTransaction.objects.get()
    assert tx.status == TStatus.APPLIED and tx.payment == payment and payment.reference == "QSUB12AB34"

    # Safaricom's C2B confirmation of the same receipt, and a repeated callback, count once.
    hook(client, "confirm", c2b_payload(account=collect.account_number(invoice)))
    hook(client, "stk", stk_callback(checkout))
    assert SubscriptionPayment.objects.count() == 1 and PlatformTransaction.objects.count() == 1
    assert not mail.outbox


def test_prompt_cancelled(client, owner, invoice):
    checkout = collect.start_mpesa(owner, invoice, "0712000111")
    hook(client, "stk", stk_callback(checkout, code=1032))
    checkout.refresh_from_db()
    invoice.refresh_from_db()
    assert checkout.status == CStatus.FAILED and "cancelled" in checkout.result
    assert invoice.status == SubscriptionInvoice.Status.OPEN


def test_prompt_checks(owner, invoice):
    with pytest.raises(ValidationError):
        collect.start_mpesa(owner, invoice, "not a phone")
    collect.start_mpesa(owner, invoice, "0712000111")
    with pytest.raises(ValidationError, match="two minutes"):
        collect.start_mpesa(owner, invoice, "0712000111")
    with pytest.raises(PermissionDenied):
        collect.start_mpesa(make_org(name="Other"), invoice, "0712000111")
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    with pytest.raises(PermissionDenied):
        collect.start_mpesa(caretaker, invoice, "0712000111")


def test_prompt_refused_by_safaricom(owner, invoice):
    FakeDarajaClient.fail = "Invalid Access Token"
    with pytest.raises(ValidationError, match="Invalid Access Token"):
        collect.start_mpesa(owner, invoice, "0712000111")
    assert not PlatformCheckout.objects.exists()


def test_prompt_rounds_up_to_whole_shillings(owner, invoice):
    admin = make_user(is_staff=True, is_superuser=True)
    services.record_payment(admin, invoice, amount=Decimal("0.50"), method="CASH", reference="C1",
                            paid_on=timezone.localdate())
    checkout = collect.start_mpesa(owner, invoice, "0712000111")
    assert checkout.amount == Decimal("799")  # 798.50 rounded up
    collect.receive_stk(stk_callback(checkout, amount=799))
    invoice.refresh_from_db()
    tx = PlatformTransaction.objects.get()
    assert invoice.status == SubscriptionInvoice.Status.PAID and tx.note == ""  # half a shilling over is fine


# ---------------------------------------------------------------------------
# Our Paybill: paid by hand
# ---------------------------------------------------------------------------


def test_paybill_by_hand_matched_by_account_number(client, invoice):
    hook(client, "confirm", c2b_payload(account=invoice.number))
    tx = PlatformTransaction.objects.get()
    assert tx.status == TStatus.APPLIED and tx.payer_name == "Jane Wanjiru"
    assert_paid(invoice, SubscriptionPayment.Method.MPESA)


def test_unmatched_money_waits_and_alerts_then_is_claimed_by_hand(client, owner, invoice,
                                                                  django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        hook(client, "confirm", c2b_payload(code="QWRONG1234", account="RENT"))
    tx = PlatformTransaction.objects.get()
    assert tx.status == TStatus.UNMATCHED and "No open invoice" in tx.note
    assert len(mail.outbox) == 1 and "QWRONG1234" in mail.outbox[0].subject
    admin = make_user(is_staff=True, is_superuser=True)
    payment = services.record_payment(admin, invoice, amount=Decimal("799"), method="MPESA",
                                      reference="QWRONG1234", paid_on=timezone.localdate())
    tx.refresh_from_db()
    assert tx.status == TStatus.APPLIED and tx.payment == payment and tx.invoice == invoice


def test_a_payment_to_another_shortcode_or_a_paid_invoice_waits(client, invoice):
    hook(client, "confirm", c2b_payload(code="QOTHER1234", account=invoice.number, shortcode="111111"))
    assert PlatformTransaction.objects.get(code="QOTHER1234").status == TStatus.UNMATCHED
    hook(client, "confirm", c2b_payload(code="QFIRST1234", account=invoice.number))
    hook(client, "confirm", c2b_payload(code="QAGAIN1234", account=invoice.number))
    # The invoice was paid by the first, so the second has no open invoice to go on.
    assert PlatformTransaction.objects.get(code="QAGAIN1234").status == TStatus.UNMATCHED
    assert SubscriptionPayment.objects.count() == 1


def test_overpayment_is_applied_and_flagged(client, invoice, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        hook(client, "confirm", c2b_payload(amount="1000", account=invoice.number))
    tx = PlatformTransaction.objects.get()
    assert tx.status == TStatus.APPLIED and "201.00 more" in tx.note and len(mail.outbox) == 1


def test_register_urls(settings):
    collect.register_urls()
    shortcode, path, body = FakeDarajaClient.calls[-1]
    assert path.endswith("registerurl") and body["ConfirmationURL"].endswith(f"/hooks/plat/{TOKEN}/confirm/")
    settings.PLATFORM_DARAJA_ENVIRONMENT = "PRODUCTION"
    settings.SITE_URL = "http://app.example.com"
    with pytest.raises(ValidationError, match="https"):
        collect.register_urls()


# ---------------------------------------------------------------------------
# Our Paystack
# ---------------------------------------------------------------------------


def test_paystack_checkout_webhook_pays(client, owner, invoice):
    login(client, owner)
    response = client.post(reverse("subscriptions:pay_paystack"), {"invoice": invoice.public_id})
    checkout = PlatformCheckout.objects.get()
    assert response.status_code == 302 and response["Location"] == f"https://checkout.paystack.com/fake-{checkout.reference}"
    _pk, _method, path, body = FakePaystackClient.calls[-1]
    assert path == "/transaction/initialize" and body["amount"] == 79900 and body["currency"] == "KES"
    assert body["callback_url"] == "https://app.example.com" + reverse("subscriptions:paystack_return")

    client.logout()
    assert hook(client, "ps", ps_event(checkout)).status_code == 200
    checkout.refresh_from_db()
    assert checkout.status == CStatus.PAID
    payment = assert_paid(invoice, SubscriptionPayment.Method.PAYSTACK)
    assert payment.reference == "PS5551"
    hook(client, "ps", ps_event(checkout))  # Paystack retries
    assert SubscriptionPayment.objects.count() == 1


def test_paystack_mpesa_prompt_when_daraja_is_down(client, owner, invoice):
    login(client, owner)
    FakeDarajaClient.fail = "Daraja is down"
    client.post(reverse("subscriptions:pay_mpesa"), {"invoice": invoice.public_id, "phone": "0712345678"})
    assert not PlatformCheckout.objects.exists()
    FakeDarajaClient.fail = ""
    client.post(reverse("subscriptions:pay_paystack_mpesa"), {"invoice": invoice.public_id, "phone": "0712345678"})
    checkout = PlatformCheckout.objects.get()
    assert checkout.provider == PlatformCheckout.Provider.PAYSTACK and checkout.phone == "+254712345678"
    _pk, _method, path, body = FakePaystackClient.calls[-1]
    assert path == "/charge" and body["amount"] == 79900 and body["mobile_money"] == {
        "phone": "+254712345678", "provider": "mpesa"}
    client.post(reverse("subscriptions:pay_paystack_mpesa"), {"invoice": invoice.public_id, "phone": "0712345678"})
    assert PlatformCheckout.objects.count() == 1  # a second prompt within two minutes is refused

    client.logout()
    assert hook(client, "ps", ps_event(checkout)).status_code == 200
    assert_paid(invoice, SubscriptionPayment.Method.PAYSTACK)


def test_paystack_mpesa_prompt_refused_keeps_nothing(client, owner, invoice):
    login(client, owner)
    FakePaystackClient.fail = "Mobile money is not enabled"
    client.post(reverse("subscriptions:pay_paystack_mpesa"), {"invoice": invoice.public_id, "phone": "0712345678"})
    assert not PlatformCheckout.objects.exists()


def test_paystack_webhook_needs_our_signature(client, owner, invoice):
    checkout = paystack_checkout(owner, invoice)
    assert hook(client, "ps", ps_event(checkout), key="sk_test_someone_else").status_code == 401
    assert not PlatformTransaction.objects.exists()


def test_paystack_return_verifies(client, owner, invoice):
    checkout = paystack_checkout(owner, invoice)
    FakePaystackClient.replies = {"/transaction/verify": ps_event(checkout)["data"]}
    login(client, owner)
    response = client.get(reverse("subscriptions:paystack_return") + f"?reference={checkout.reference}", follow=True)
    assert "payment received" in response.content.decode()
    assert_paid(invoice, SubscriptionPayment.Method.PAYSTACK)


def test_pay_status_reports_the_checkout_and_pays_it(client, owner, invoice):
    checkout = paystack_checkout(owner, invoice)
    login(client, owner)
    url = reverse("subscriptions:pay_status", args=[checkout.public_id])
    assert client.get(url).json()["status"] == "PENDING"
    FakePaystackClient.replies = {"/transaction/verify": ps_event(checkout)["data"]}
    assert client.get(url).json()["status"] == "PAID"  # asking Paystack settles it, no webhook needed
    assert_paid(invoice, SubscriptionPayment.Method.PAYSTACK)


def test_pay_status_is_only_for_our_own_checkout(client, owner, invoice):
    checkout = paystack_checkout(owner, invoice)
    client.logout()
    assert client.get(reverse("subscriptions:pay_status", args=[checkout.public_id])).status_code in (302, 403, 404)


def test_paystack_failure_and_wrong_currency(client, owner, invoice):
    checkout = paystack_checkout(owner, invoice)
    hook(client, "ps", ps_event(checkout, status="failed", name="charge.failed", gateway_response="Declined"))
    checkout.refresh_from_db()
    assert checkout.status == CStatus.FAILED and checkout.result == "Declined"
    # A late success still counts, but money in another currency waits for a person.
    hook(client, "ps", ps_event(checkout, currency="USD"))
    checkout.refresh_from_db()
    tx = PlatformTransaction.objects.get()
    assert checkout.status == CStatus.PAID and tx.status == TStatus.UNMATCHED and "USD" in tx.note


def test_paystack_events_for_other_references_are_ignored(client, invoice):
    hook(client, "ps", {"event": "charge.success", "data": {"id": 1, "reference": "rent-123", "status": "success",
                                                             "amount": 100, "currency": "KES"}})
    assert not PlatformTransaction.objects.exists()


# ---------------------------------------------------------------------------
# Callbacks and the daily job
# ---------------------------------------------------------------------------


def test_wrong_token_or_kind_is_404(client, settings):
    settings.PLATFORM_CALLBACK_TOKEN = ""
    assert hook(client, "confirm", c2b_payload(), token="x" * 40).status_code == 404
    settings.PLATFORM_CALLBACK_TOKEN = TOKEN
    assert hook(client, "confirm", c2b_payload(), token="x" * 40).status_code == 404
    assert client.post(reverse("hook_platform", args=[TOKEN, "other"]), data="{}",
                       content_type="application/json").status_code == 404
    assert client.get(reverse("hook_platform", args=[TOKEN, "confirm"])).status_code in (404, 405)


def test_unreadable_callbacks(client):
    response = client.post(reverse("hook_platform", args=[TOKEN, "confirm"]), data="nonsense",
                           content_type="application/json")
    assert response.status_code == 400
    assert hook(client, "confirm", {"TransAmount": "1"}).status_code == 400


def test_give_up_waiting(owner, invoice):
    checkout = collect.start_mpesa(owner, invoice, "0712000111")
    assert collect.give_up_waiting() == 0
    assert collect.give_up_waiting(timezone.now() + datetime.timedelta(hours=25)) == 1
    checkout.refresh_from_db()
    assert checkout.status == CStatus.FAILED and checkout.result == "No answer."


# ---------------------------------------------------------------------------
# Topping up the SMS wallet online (D-142)
# ---------------------------------------------------------------------------


def test_sms_top_up_by_prompt_credits_the_wallet(client, owner):
    checkout = collect.start_sms_mpesa(owner, "500", "0712000111")
    _shortcode, _path, body = FakeDarajaClient.calls[-1]
    assert body["Amount"] == 500 and body["AccountReference"] == "SMS"
    assert checkout.invoice is None and checkout.organization == owner.organization
    with pytest.raises(ValidationError, match="two minutes"):
        collect.start_sms_mpesa(owner, "500", "0712000111")
    hook(client, "stk", stk_callback(checkout, receipt="QSMS12AB34", amount=500))
    checkout.refresh_from_db()
    assert checkout.status == CStatus.PAID
    assert services.sms_balance(owner.organization) == Decimal("500.00")
    payment = SubscriptionPayment.objects.get(reference="QSMS12AB34")
    assert payment.invoice is None and payment.receipt and payment.sms_top_up.amount == Decimal("500.00")
    assert PlatformTransaction.objects.get().status == TStatus.APPLIED
    hook(client, "stk", stk_callback(checkout, receipt="QSMS12AB34", amount=500))
    assert services.sms_balance(owner.organization) == Decimal("500.00")


def test_sms_top_up_by_paystack_credits_the_wallet(client, owner):
    page = collect.start_sms_paystack(owner, "1000")
    assert page.startswith("https://")
    checkout = PlatformCheckout.objects.get(provider=PlatformCheckout.Provider.PAYSTACK)
    assert checkout.invoice is None and checkout.amount == 1000
    hook(client, "ps", ps_event(checkout, amount=100000))
    assert services.sms_balance(owner.organization) == Decimal("1000.00")


def test_sms_top_up_checks(owner):
    for amount in ("50", "60000", "250.50", "abc"):
        with pytest.raises(ValidationError, match="whole shillings"):
            collect.start_sms_mpesa(owner, amount, "0712000111")
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    with pytest.raises(PermissionDenied):
        collect.start_sms_mpesa(caretaker, "500", "0712000111")


def test_sms_top_up_page(client, owner):
    from subscriptions.models import SmsWallet

    SmsWallet.objects.update_or_create(organization=owner.organization, defaults={"mode": SmsWallet.Mode.PREPAID})
    login(client, owner)
    page = client.get(reverse("subscriptions:page")).content.decode()
    assert "Top up with M-Pesa" in page
    client.post(reverse("subscriptions:sms_top_up"), {"amount": "300", "phone": "0712000111", "method": "mpesa"})
    assert PlatformCheckout.objects.get().amount == 300
    response = client.post(reverse("subscriptions:sms_top_up"), {"amount": "300", "method": "paystack"})
    assert response.status_code == 302 and response.url.startswith("https://")


# ---------------------------------------------------------------------------
# Renewing by a saved card
# ---------------------------------------------------------------------------

CARD = {"authorization_code": "AUTH_secret1", "reusable": True, "channel": "card", "last4": "4081",
        "brand": "visa", "exp_month": "12", "exp_year": "2030"}


def saved_card_sub(owner, invoice):
    """Pays the first invoice by card with 'renew automatically' ticked, so a card is saved."""
    owner.organization.billing_email = "billing@acme.example"
    owner.organization.save()
    collect.start_paystack(owner, invoice, save_card=True)
    checkout = PlatformCheckout.objects.get(provider=PlatformCheckout.Provider.PAYSTACK)
    collect.receive_paystack(ps_event(checkout, authorization=CARD)["data"], ps_event(checkout, authorization=CARD))
    return Subscription.objects.get(organization=invoice.organization)


def renewal_due(sub, today):
    """Moves the paid period so the renewal invoice is issued by the daily job on `today`."""
    sub.period_end = today + datetime.timedelta(days=1)
    sub.save()
    services.daily(today)
    return services.open_invoice(sub)


def test_paying_by_card_with_the_box_ticked_saves_the_card(owner, invoice):
    sub = saved_card_sub(owner, invoice)
    assert sub.has_card and sub.auto_renew and (sub.card_last4, sub.card_brand) == ("4081", "visa")
    assert "AUTH_secret1" not in sub.card_code_encrypted
    assert "AUTH_secret1" not in json.dumps(PlatformTransaction.objects.get().raw_payload)


def test_a_card_is_not_saved_unless_asked_or_when_not_reusable(owner, invoice):
    checkout = paystack_checkout(owner, invoice)  # box not ticked
    collect.receive_paystack(ps_event(checkout, authorization=CARD)["data"], {})
    assert not Subscription.objects.get(organization=owner.organization).has_card
    collect.start_paystack(owner, services.choose_plan(owner, Plan.objects.get(key="business"), "MONTHLY"),
                           save_card=True)
    ticked = PlatformCheckout.objects.filter(save_card=True).get()
    collect.receive_paystack(ps_event(ticked, authorization={**CARD, "reusable": False})["data"], {})
    assert not Subscription.objects.get(organization=owner.organization).has_card


def test_the_daily_job_charges_the_saved_card_and_renews(owner, invoice, django_capture_on_commit_callbacks):
    sub = saved_card_sub(owner, invoice)
    today = timezone.localdate()
    renewal = renewal_due(sub, today)
    assert renewal.reason == SubscriptionInvoice.Reason.RENEWAL
    FakePaystackClient.calls.clear()
    with django_capture_on_commit_callbacks(execute=True):
        assert collect.charge_due_cards(today) == {"card_charged": 1, "card_failed": 0}
    _pk, _method, path, body = FakePaystackClient.calls[-1]
    assert path == "/transaction/charge_authorization" and body["authorization_code"] == "AUTH_secret1"
    assert body["amount"] == int(renewal.total * 100)
    renewal.refresh_from_db()
    assert renewal.status == SubscriptionInvoice.Status.PAID
    assert SubscriptionPayment.objects.filter(invoice=renewal, method=SubscriptionPayment.Method.PAYSTACK).exists()
    assert "renewed" in mail.outbox[-1].subject
    assert collect.charge_due_cards(today) == {"card_charged": 0, "card_failed": 0}  # nothing left to charge


def test_a_refused_card_emails_the_owner_and_retries_once_a_day(owner, invoice, django_capture_on_commit_callbacks):
    sub = saved_card_sub(owner, invoice)
    today = timezone.localdate()
    renewal = renewal_due(sub, today)
    FakePaystackClient.replies = {"/transaction/charge_authorization": {
        "id": 7002, "reference": "x", "status": "failed", "gateway_response": "Insufficient funds"}}
    # The fake echoes whatever reply it holds; make it carry the reference of the checkout being charged.
    original = FakePaystackClient._request

    def reply(self, method, path, body=None):
        if path == "/transaction/charge_authorization":
            FakePaystackClient.replies[path]["reference"] = body["reference"]
        return original(self, method, path, body)

    FakePaystackClient._request = reply
    try:
        with django_capture_on_commit_callbacks(execute=True):
            assert collect.charge_due_cards(today) == {"card_charged": 0, "card_failed": 1}
        assert collect.charge_due_cards(today) == {"card_charged": 0, "card_failed": 0}  # one try a day
        with django_capture_on_commit_callbacks(execute=True):
            assert collect.charge_due_cards(today + datetime.timedelta(days=1))["card_failed"] == 1
    finally:
        FakePaystackClient._request = original
    renewal.refresh_from_db()
    assert renewal.status == SubscriptionInvoice.Status.OPEN
    assert "could not charge" in mail.outbox[-1].subject and "Insufficient funds" in mail.outbox[-1].body


def test_paystack_down_counts_as_a_failed_charge(owner, invoice):
    sub = saved_card_sub(owner, invoice)
    today = timezone.localdate()
    renewal_due(sub, today)
    FakePaystackClient.fail = "Could not reach Paystack"
    assert collect.charge_due_cards(today)["card_failed"] == 1
    assert PlatformCheckout.objects.filter(automatic=True, status=CStatus.FAILED).count() == 1


def test_no_charge_when_off_early_or_for_a_chosen_plan(owner, invoice):
    sub = saved_card_sub(owner, invoice)
    today = timezone.localdate()
    sub.period_end = today + datetime.timedelta(days=6)
    sub.save()
    services.daily(today)
    FakePaystackClient.calls.clear()
    assert collect.charge_due_cards(today) == {"card_charged": 0, "card_failed": 0}  # not yet near the end
    sub.period_end = today + datetime.timedelta(days=1)
    sub.auto_renew = False
    sub.save()
    assert collect.charge_due_cards(today) == {"card_charged": 0, "card_failed": 0}  # switched off
    assert not [c for c in FakePaystackClient.calls if c[2] == "/transaction/charge_authorization"]


def test_turning_it_off_and_removing_the_card(client, owner, invoice):
    sub = saved_card_sub(owner, invoice)
    login(client, owner)
    client.post(reverse("subscriptions:page"), {"action": "auto_renew_off"})
    sub.refresh_from_db()
    assert sub.has_card and not sub.auto_renew
    client.post(reverse("subscriptions:page"), {"action": "auto_renew_on"})
    sub.refresh_from_db()
    assert sub.auto_renew
    assert "Renews automatically" in client.get(reverse("subscriptions:page")).content.decode()
    client.post(reverse("subscriptions:page"), {"action": "remove_card"})
    sub.refresh_from_db()
    assert not sub.has_card and not sub.auto_renew and sub.card_last4 == ""
    client.post(reverse("subscriptions:page"), {"action": "auto_renew_on"})  # nothing to charge
    sub.refresh_from_db()
    assert not sub.auto_renew


def test_the_renewal_reminder_says_the_card_will_be_charged(owner, invoice):
    sub = saved_card_sub(owner, invoice)
    today = timezone.localdate()
    sub.period_end = today + datetime.timedelta(days=7)
    sub.save()
    mail.outbox.clear()
    assert services.send_reminders(today) == 1
    assert "4081" in mail.outbox[0].body and "charged automatically" in mail.outbox[0].body
    sub.auto_renew = False
    sub.save()
    mail.outbox.clear()
    SubscriptionReminderModel = type(sub.reminders.first())
    SubscriptionReminderModel.objects.all().delete()
    services.send_reminders(today)
    assert "renewal invoice is on the Subscription page" in mail.outbox[0].body
