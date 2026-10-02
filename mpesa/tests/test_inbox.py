"""The M-Pesa inbox: scope, matching by hand, ignoring, reversal, alerts and the pages (D-045 step 3)."""

import datetime

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from audit.models import AuditEvent
from billing.invoicing import lease_balance
from billing.models import Invoice
from billing.tests.test_invoicing import FEB, bill, make_lease
from mpesa import c2b, inbox, services
from mpesa.models import MpesaTransaction
from notifications import services as notification_services
from notifications.models import Message
from payments import services as payment_services
from payments.models import Payment, PropertyPaymentAccount

pytestmark = pytest.mark.django_db

Status = MpesaTransaction.Status
STRANGER = "+254799000111"
FEB_3 = datetime.date(2026, 2, 3)


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
    return make_lease(owner, prop)


@pytest.fixture
def creds(owner):
    account = services.add_account(owner, type="PAYBILL", number="600123", display_name="Rent Paybill")
    return services.save_credentials(owner, account, environment="SANDBOX", consumer_key="k", consumer_secret="s")


def receive(creds, n=1, **kw):
    data = {"TransactionType": "Pay Bill", "TransID": f"QAB12CD3{n:02d}", "TransTime": "20260203101500",
            "TransAmount": "15000.00", "BusinessShortCode": "600123", "BillRefNumber": "ZZ-99",
            "MSISDN": STRANGER.lstrip("+"), "FirstName": "Jane", "LastName": "Doe"}
    data.update(kw)
    return c2b.receive(creds, data)[0]


def serve(org, prop, creds):
    PropertyPaymentAccount.objects.create(organization=org, property=prop, payment_account=creds.payment_account)


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def test_account_serving_no_property_is_seen_only_by_members_with_every_property(org, prop, creds, lease):
    tx = receive(creds)
    everywhere = add_member(org, "accountant", all_properties=True)
    one = add_member(org, "accountant", properties=[prop])
    assert list(inbox.inbox(everywhere)) == [tx]
    assert not inbox.inbox(one).exists()


def test_account_serving_a_property_is_seen_by_its_members(org, prop, creds, lease):
    serve(org, prop, creds)
    other = make_property(org)
    tx = receive(creds)
    assert list(inbox.inbox(add_member(org, "accountant", properties=[prop]))) == [tx]
    assert not inbox.inbox(add_member(org, "accountant", properties=[other])).exists()
    assert not inbox.inbox(add_member(org, "caretaker", all_properties=True)).exists()


def test_matched_transaction_follows_the_lease_property(owner, org, prop, creds, lease):
    other = make_property(org)
    serve(org, prop, creds), serve(org, other, creds)
    tx = receive(creds, BillRefNumber=lease.unit.payment_reference)
    assert tx.status == Status.MATCHED
    assert inbox.visible_transactions(add_member(org, "accountant", properties=[prop])).filter(pk=tx.pk).exists()
    assert not inbox.visible_transactions(add_member(org, "accountant", properties=[other])).exists()


def test_other_organization_sees_nothing(creds, lease):
    receive(creds)
    them = make_org(name="Other")
    assert not inbox.visible_transactions(them).exists()


# ---------------------------------------------------------------------------
# Matching by hand
# ---------------------------------------------------------------------------


def test_accepting_the_suggestion_confirms_as_the_matcher(org, creds, lease):
    invoice = bill(lease, FEB)
    tx = receive(creds, MSISDN=lease.primary_tenant.phone.lstrip("+"))
    assert tx.suggested_lease == lease
    clerk = add_member(org, "accountant", all_properties=True)
    payment = inbox.accept_suggestion(clerk, tx)

    tx.refresh_from_db()
    assert (tx.status, tx.matched_by, tx.matched_by_user, tx.payment) == (
        Status.MATCHED, "MANUAL", clerk.user, payment)
    assert (payment.status, payment.recorded_by, payment.confirmed_by) == (
        Payment.Status.CONFIRMED, clerk.user, clerk.user)
    assert payment.reference == tx.trans_id and payment.tenant == lease.primary_tenant
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.PAID and lease_balance(lease) == 0
    event = AuditEvent.objects.get(action="mpesa.match")
    assert event.actor == clerk.user and event.changes["suggested"] == [None, True]


def test_matching_to_any_lease(owner, org, prop, creds, lease):
    other = make_lease(owner, prop, code="B2")
    tx = receive(creds)
    payment = inbox.match(owner, tx, other)
    assert payment.lease == other and payment.confirmed_by == owner.user


def test_cannot_match_to_a_lease_out_of_reach(org, prop, creds, lease):
    serve(org, prop, creds)
    theirs = make_property(org)
    serve(org, theirs, creds)
    tx = receive(creds)
    clerk = add_member(org, "accountant", properties=[theirs])
    with pytest.raises(PermissionDenied):
        inbox.match(clerk, tx, lease)
    with pytest.raises(PermissionDenied):
        inbox.match(add_member(org, "caretaker", all_properties=True), tx, lease)
    stranger = make_org(name="Other")
    with pytest.raises(PermissionDenied):
        inbox.match(stranger, tx, make_lease(stranger, make_property(stranger.organization)))
    assert not Payment.objects.filter(lease=lease).exists()


def test_matched_or_flagged_cannot_be_matched(owner, creds, lease):
    tx = receive(creds)
    inbox.match(owner, tx, lease)
    with pytest.raises(ValidationError):
        inbox.match(owner, tx, lease)
    flagged = receive(creds, n=2, TransAmount="abc")
    with pytest.raises(ValidationError):
        inbox.match(owner, flagged, lease)
    assert Payment.objects.count() == 1


def test_accept_without_a_suggestion_is_refused(owner, creds, lease):
    with pytest.raises(ValidationError):
        inbox.accept_suggestion(owner, receive(creds))


# ---------------------------------------------------------------------------
# Ignoring and restoring
# ---------------------------------------------------------------------------


def test_ignore_needs_a_reason_and_restore_puts_it_back(owner, creds, lease):
    tx = receive(creds)
    with pytest.raises(ValidationError):
        inbox.ignore(owner, tx, reason="  ")
    inbox.ignore(owner, tx, reason="Refunded to sender")
    tx.refresh_from_db()
    assert tx.status == Status.IGNORED and "Refunded to sender" in tx.note
    assert not inbox.inbox(owner).exists()
    with pytest.raises(ValidationError):
        inbox.ignore(owner, tx, reason="again")
    inbox.restore(owner, tx)
    tx.refresh_from_db()
    assert tx.status == Status.UNMATCHED
    assert {"mpesa.ignore", "mpesa.restore"} <= set(AuditEvent.objects.values_list("action", flat=True))


def test_flagged_is_restored_as_flagged(owner, creds):
    tx = receive(creds, BusinessShortCode="999999")
    inbox.ignore(owner, tx, reason="Wrong shortcode, checked")
    assert inbox.restore(owner, tx).status == Status.FLAGGED


def test_restore_only_from_ignored(owner, creds):
    with pytest.raises(ValidationError):
        inbox.restore(owner, receive(creds))


# ---------------------------------------------------------------------------
# Reversal
# ---------------------------------------------------------------------------


def test_reversed_payment_goes_back_to_the_inbox_and_can_be_matched_again(owner, prop, creds, lease):
    other = make_lease(owner, prop, code="B2")
    tx = receive(creds, BillRefNumber=lease.unit.payment_reference)
    first = tx.payment
    payment_services.reverse_payment(owner, first, reason="Tenant typed the wrong unit")

    tx.refresh_from_db()
    assert (tx.status, tx.payment, tx.matched_by, tx.matched_by_user, tx.suggested_lease) == (
        Status.UNMATCHED, None, "", None, None)
    assert "Payment reversed: Tenant typed the wrong unit" in tx.note
    assert list(inbox.inbox(owner)) == [tx]

    second = inbox.match(owner, tx, other)
    assert second.lease == other and second.pk != first.pk
    first.refresh_from_db()
    assert first.status == Payment.Status.REVERSED


def test_reversing_a_hand_recorded_mpesa_payment_touches_no_transaction(owner, creds, lease):
    tx = receive(creds)
    payment = payment_services.record_system_payment(lease, amount=100, method="MPESA", paid_at=FEB,
                                                     reference="XYZ", source="test", actor=owner)
    payment_services.reverse_payment(owner, payment, reason="Mistake")
    tx.refresh_from_db()
    assert tx.status == Status.UNMATCHED


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


def test_unmatched_alerts_whoever_can_match_it_and_texts_the_payer(owner, org, prop, creds, lease):
    clerk = add_member(org, "accountant", all_properties=True)
    add_member(org, "accountant", properties=[prop])  # the account serves no property: not theirs
    add_member(org, "caretaker", all_properties=True)
    tx = receive(creds)

    staff = Message.objects.filter(type="mpesa_unmatched")
    assert {m.user for m in staff} == {owner.user, clerk.user}
    assert all(m.channel == "IN_APP" and "Jane Doe" in m.body and "ZZ-99" in m.body for m in staff)
    payer = Message.objects.get(type="payment_unmatched")
    assert (payer.tenant, payer.user, payer.to, payer.channel) == (None, None, STRANGER, "SMS")
    assert tx.trans_id in payer.body and "03/02/2026" in payer.body

    c2b.process(tx)  # nothing twice
    assert Message.objects.count() == 3


def test_payer_who_is_a_tenant_is_texted_as_the_tenant(creds, lease):
    tenant = lease.primary_tenant
    receive(creds, MSISDN=tenant.phone.lstrip("+"))
    assert Message.objects.get(type="payment_unmatched").tenant == tenant


def test_hidden_number_gets_no_text(creds, lease):
    receive(creds, MSISDN=c2b.phone_hash(STRANGER))
    assert not Message.objects.filter(type="payment_unmatched").exists()
    assert Message.objects.filter(type="mpesa_unmatched").exists()


def test_payer_who_stopped_sms_is_skipped(owner, creds, lease):
    tenant = lease.primary_tenant
    notification_services.set_tenant_channel(owner, tenant, "SMS", allowed=False)
    receive(creds, MSISDN=tenant.phone.lstrip("+"))
    assert Message.objects.get(type="payment_unmatched").status == Message.Status.SKIPPED


def test_shared_number_goes_to_the_bare_number_and_respects_a_stop(owner, prop, creds, lease):
    other = make_lease(owner, prop, code="B2")  # same phone as `lease`
    notification_services.set_tenant_channel(owner, other.primary_tenant, "SMS", allowed=False)
    receive(creds, MSISDN=lease.primary_tenant.phone.lstrip("+"), TransAmount="1")
    message = Message.objects.get(type="payment_unmatched")
    assert message.tenant is None and message.to == lease.primary_tenant.phone
    assert message.status == Message.Status.SKIPPED


def test_confident_match_alerts_nobody(creds, lease):
    receive(creds, BillRefNumber=lease.unit.payment_reference)
    assert not Message.objects.filter(type__in=["mpesa_unmatched", "payment_unmatched"]).exists()


def test_alert_failure_does_not_undo_the_inbox(creds, lease, monkeypatch):
    from notifications import triggers

    def boom(tx):
        raise RuntimeError("provider down")

    monkeypatch.setattr(triggers, "mpesa_unmatched", boom)
    tx = receive(creds)
    assert tx.status == Status.UNMATCHED and tx.attempts == 1


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def test_inbox_page_and_accepting_from_it(client, org, creds, lease):
    clerk = add_member(org, "accountant", all_properties=True)
    tx = receive(creds, MSISDN=lease.primary_tenant.phone.lstrip("+"))
    login(client, clerk)
    page = client.get(reverse("mpesa:inbox"))
    assert page.status_code == 200 and tx.trans_id in page.content.decode()
    response = client.post(reverse("mpesa:inbox"), {"trans_id": tx.trans_id, "action": "accept"})
    assert response.status_code == 302
    tx.refresh_from_db()
    assert tx.status == Status.MATCHED and tx.matched_by_user == clerk.user


def test_inbox_needs_the_capability(client, org, creds):
    login(client, add_member(org, "caretaker", all_properties=True))
    assert client.get(reverse("mpesa:inbox")).status_code in (403, 404)
    assert client.get(reverse("mpesa:transactions")).status_code in (403, 404)


def test_detail_page_lists_leases_and_matches_by_hand(client, owner, prop, creds, lease):
    other = make_lease(owner, prop, code="B2")
    tx = receive(creds, BillRefNumber="B2")
    login(client, owner)
    url = reverse("mpesa:transaction", args=[tx.trans_id])
    page = client.get(url)
    body = page.content.decode()
    assert page.status_code == 200 and str(other.public_id) in body and str(lease.public_id) not in body
    client.post(url, {"action": "match", "lease": str(other.public_id)})
    tx.refresh_from_db()
    assert tx.status == Status.MATCHED and tx.payment.lease == other
    assert client.get(url).status_code == 200


def test_detail_page_ignore_without_reason_shows_the_error(client, owner, creds):
    tx = receive(creds)
    login(client, owner)
    url = reverse("mpesa:transaction", args=[tx.trans_id])
    response = client.post(url, {"action": "ignore", "reason": ""}, follow=True)
    assert "Say why" in response.content.decode()
    tx.refresh_from_db()
    assert tx.status == Status.UNMATCHED
    client.post(url, {"action": "ignore", "reason": "Not rent"})
    tx.refresh_from_db()
    assert tx.status == Status.IGNORED
    client.post(url, {"action": "restore"})
    tx.refresh_from_db()
    assert tx.status == Status.UNMATCHED


def test_bad_lease_id_is_404(client, owner, creds):
    tx = receive(creds)
    login(client, owner)
    url = reverse("mpesa:transaction", args=[tx.trans_id])
    assert client.post(url, {"action": "match", "lease": "nope"}).status_code == 404
    assert client.post(url, {"action": "dance"}).status_code == 404


def test_another_organizations_transaction_is_404(client, creds):
    tx = receive(creds)
    login(client, make_org(name="Other"))
    assert client.get(reverse("mpesa:transaction", args=[tx.trans_id])).status_code == 404
    assert client.post(reverse("mpesa:inbox"), {"trans_id": tx.trans_id, "action": "ignore",
                                                "reason": "x"}).status_code == 404


def test_transaction_list_filters(client, owner, creds, lease):
    matched = receive(creds, BillRefNumber=lease.unit.payment_reference)
    waiting = receive(creds, n=2)
    login(client, owner)
    url = reverse("mpesa:transactions")
    body = client.get(url).content.decode()
    assert matched.trans_id in body and waiting.trans_id in body
    body = client.get(url, {"status": "UNMATCHED"}).content.decode()
    assert matched.trans_id not in body and waiting.trans_id in body
    body = client.get(url, {"q": matched.trans_id[-4:]}).content.decode()
    assert matched.trans_id in body and waiting.trans_id not in body
    assert client.get(url, {"account": "junk", "status": "junk"}).status_code == 200


# ---------------------------------------------------------------------------
# The same M-Pesa code typed in by hand and sent by Safaricom (counted once)
# ---------------------------------------------------------------------------


def test_code_already_recorded_by_hand_is_not_paid_twice(owner, org, creds, lease):
    bill(lease, FEB)
    typed = payment_services.record_payment(owner, lease, amount="15000", method=Payment.Method.MPESA,
                                            paid_at=FEB_3, reference="qab12cd301")
    tx = receive(creds, BillRefNumber=lease.unit.payment_reference)

    assert (tx.status, tx.payment, tx.suggested_lease) == (Status.UNMATCHED, None, lease)
    assert "Already recorded by hand on" in tx.note and lease.number in tx.note
    assert Payment.objects.get() == typed and lease_balance(lease) == 0
    # Staff are told; the payer already has a receipt, so no "we could not match it" SMS.
    assert Message.objects.filter(type="mpesa_unmatched").exists()
    assert not Message.objects.filter(type="payment_unmatched").exists()
    with pytest.raises(ValidationError, match="already recorded by hand"):
        inbox.accept_suggestion(owner, tx)
    assert Payment.objects.count() == 1


def test_code_recorded_by_hand_then_reversed_matches_normally(owner, creds, lease):
    typed = payment_services.record_payment(owner, lease, amount="15000", method=Payment.Method.MPESA,
                                            paid_at=FEB_3, reference="QAB12CD301")
    payment_services.reverse_payment(owner, typed, reason="Typed on the wrong lease")
    tx = receive(creds, BillRefNumber=lease.unit.payment_reference)
    assert tx.status == Status.MATCHED and tx.payment.reference == tx.trans_id


def test_recording_by_hand_a_code_waiting_in_the_inbox_is_refused(client, owner, creds, lease):
    tx = receive(creds)
    login(client, owner)
    data = {"amount": "15000", "method": "MPESA", "paid_at": "2026-02-03", "reference": tx.trans_id.lower(),
            "allow_duplicate": "on"}
    r = client.post(reverse("payments:record", args=[lease.public_id]), data)
    assert r.status_code == 200 and "M-Pesa inbox" in r.context["form"].errors["reference"][0]
    assert not Payment.objects.exists()
    # A cheque that happens to carry the same characters is not an M-Pesa code.
    r = client.post(reverse("payments:record", args=[lease.public_id]), {**data, "method": "CHEQUE"})
    assert r.status_code == 302 and Payment.objects.count() == 1
