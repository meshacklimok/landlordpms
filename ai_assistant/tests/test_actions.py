"""Assistant actions (D-092): the model proposes, the person confirms on a card."""

import datetime

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from ai_assistant import actions, services
from ai_assistant.client import FakeClient
from ai_assistant.views import PROPOSALS_KEY
from audit.models import AuditEvent
from expenses.models import Supplier
from maintenance import services as maintenance_services
from notifications.models import Announcement, Message
from payments.models import Payment
from reports.tests.test_income import make_lease

pytestmark = pytest.mark.django_db
URL = reverse("ai_assistant:ask")
CONFIRM = reverse("ai_assistant:confirm")


@pytest.fixture
def owner():
    m = make_org()
    m.organization.assistant_enabled = True
    m.organization.save()
    return m


@pytest.fixture
def prop(owner):
    return make_property(owner.organization, name="Acacia Court")


@pytest.fixture
def lease(owner, prop):
    return make_lease(owner, prop, code="A1")


def ask_with(client, *steps, question="Do it"):
    FakeClient.script = [*steps, FakeClient.text("The card is there to check and confirm.")]
    client.post(URL, {"question": question})
    stored = client.session[PROPOSALS_KEY]
    return list(stored.values())


def pay_args(**kw):
    return {"lease_number_or_unit": "A1", "amount": 7500, "method": "MPESA", "reference": "SJK12AB3CD", **kw}


# ---------------------------------------------------------------------------
# Proposals write nothing; Confirm runs the normal service
# ---------------------------------------------------------------------------


def test_a_payment_is_recorded_only_when_confirmed(client, owner, lease):
    login(client, owner)
    [card] = ask_with(client, FakeClient.tool("propose_payment", pay_args()))
    assert not Payment.objects.exists()
    # The model is told a card is shown and nothing is done yet (item 6).
    told = FakeClient.calls[1]["messages"][-1]["content"][0]["content"]
    assert "nothing is done until they press Confirm" in told and "proposal" not in told
    assert card["kind"] == "payment" and card["state"] == "open"
    assert ["Amount", "KES 7,500.00"] in card["lines"] and ["Tenants", "Tenant A1"] in card["lines"]

    page = client.get(URL).content.decode()
    assert f'id="p-{card["id"]}"' in page and "SJK12AB3CD" in page

    response = client.post(CONFIRM, {"id": card["id"], "action": "confirm"})
    assert response.status_code == 302
    payment = Payment.objects.get()
    assert (payment.lease, payment.amount, payment.method, payment.reference, payment.status) == (
        lease, 7500, "MPESA", "SJK12AB3CD", Payment.Status.CONFIRMED)
    assert payment.recorded_by == owner.user
    assert AuditEvent.objects.filter(action="payment.record").exists()
    assert AuditEvent.objects.get(action="assistant.confirmed").changes == {"action": [None, "payment"]}
    assert client.session[PROPOSALS_KEY][card["id"]]["state"] == "done"
    assert "Payment recorded." in client.get(URL).content.decode()

    # Used once (item 4).
    client.post(CONFIRM, {"id": card["id"], "action": "confirm"})
    assert Payment.objects.count() == 1


def test_a_payment_by_someone_who_cannot_confirm_waits_for_review(client, owner, prop, lease):
    accountant = add_member(owner.organization, "accountant", all_properties=True)
    login(client, accountant)
    [card] = ask_with(client, FakeClient.tool("propose_payment", pay_args(method="cash")))
    assert ["Review", "It will wait for someone who can confirm payments."] in card["lines"]
    client.post(CONFIRM, {"id": card["id"], "action": "confirm"})
    assert Payment.objects.get().status == Payment.Status.PENDING_REVIEW


def test_proposals_check_permissions_and_inputs(owner, prop, lease):
    caretaker = add_member(owner.organization, "caretaker", properties=[prop])
    today = timezone.localdate()
    assert actions.propose_payment(caretaker, pay_args(), today) == {
        "error": "not allowed: this member's role cannot see this. Say so; do not guess."}
    assert actions.propose_message(caretaker, {"lease_number_or_unit": "A1", "text": "Hi"}, today)["error"]
    assert "error" in actions.propose_payment(owner, pay_args(amount=0), today)
    assert "error" in actions.propose_payment(owner, pay_args(method="BITCOIN"), today)
    tomorrow = (today + datetime.timedelta(days=1)).isoformat()
    assert actions.propose_payment(owner, pay_args(date=tomorrow), today) == {
        "error": "the date cannot be in the future"}
    assert "error" in actions.propose_payment(owner, pay_args(lease_number_or_unit="Z9"), today)
    # Another organization's lease is never found.
    other = make_org()
    make_lease(other, make_property(other.organization, name="Elsewhere"), code="B7")
    assert "error" in actions.propose_payment(owner, pay_args(lease_number_or_unit="B7"), today)


def test_confirm_checks_permissions_again(owner, prop, lease):
    proposal = actions.propose_payment(owner, pay_args(), timezone.localdate())["proposal"]
    caretaker = add_member(owner.organization, "caretaker", properties=[prop])
    with pytest.raises(PermissionDenied):
        actions.confirm(caretaker, proposal)
    assert not Payment.objects.exists()


def test_two_quick_confirms_record_one_payment(owner, lease):
    """Both submits read the same open proposal from the session; only the first may run it."""
    proposal = actions.propose_payment(owner, pay_args(), timezone.localdate())["proposal"]
    actions.confirm(owner, dict(proposal))
    with pytest.raises(ValidationError):
        actions.confirm(owner, dict(proposal))
    assert Payment.objects.count() == 1


def test_a_failed_confirm_can_be_tried_again(owner, prop, lease):
    proposal = actions.propose_payment(owner, pay_args(), timezone.localdate())["proposal"]
    caretaker = add_member(owner.organization, "caretaker", properties=[prop])
    with pytest.raises(PermissionDenied):
        actions.confirm(caretaker, proposal)
    actions.confirm(owner, proposal)
    assert Payment.objects.count() == 1


def test_a_message_can_be_edited_then_sent_once(client, owner, lease):
    login(client, owner)
    [card] = ask_with(client, FakeClient.tool("propose_message", {
        "lease_number_or_unit": "A1", "text": "Dear {tenant_name}, water is off on Monday."}))
    assert card["kind"] == "message" and card["text"] == "Dear {tenant_name}, water is off on Monday."
    assert ["To", "Tenant A1"] in card["lines"]
    assert not Announcement.objects.exists()

    client.post(CONFIRM, {"id": card["id"], "action": "confirm", "text": "Dear {tenant_name}, no water Tuesday."})
    announcement = Announcement.objects.get()
    assert announcement.text == "Dear {tenant_name}, no water Tuesday." and announcement.recipient_count == 1
    assert str(announcement.public_id) == card["id"]
    assert Message.objects.filter(announcement=announcement, tenant__name="Tenant A1").exists()
    client.post(CONFIRM, {"id": card["id"], "action": "confirm"})
    assert Announcement.objects.count() == 1


def test_a_message_to_one_lease_reaches_only_that_lease(owner, prop, lease):
    make_lease(owner, prop, code="A2")
    proposal = actions.propose_message(owner, {"lease_number_or_unit": "A1", "text": "Hello"},
                                       timezone.localdate())["proposal"]
    actions.confirm(owner, proposal)
    assert list(Message.objects.filter(announcement__isnull=False).values_list("tenant__name", flat=True)) == [
        "Tenant A1"]


def test_an_assignment_keeps_what_was_not_named(client, owner, prop, lease):
    req = maintenance_services.report(owner, prop, title="Leaking tap", unit=lease.unit)
    maintenance_services.assign(owner, req, user=owner.user)
    Supplier.objects.create(organization=owner.organization, name="Juma Plumbing", phone="0722000111")
    login(client, owner)
    [card] = ask_with(client, FakeClient.tool("propose_assignment", {
        "repair_number": req.number, "supplier": "juma"}))
    assert ["Supplier", "Juma Plumbing"] in card["lines"] and ["Staff", str(owner.user)] in card["lines"]
    client.post(CONFIRM, {"id": card["id"], "action": "confirm"})
    req.refresh_from_db()
    assert (req.assigned_to, req.supplier.name) == (owner.user, "Juma Plumbing")
    assert actions.propose_assignment(owner, {"repair_number": req.number, "supplier": "Juma"},
                                      timezone.localdate()) == {"error": "the repair is already assigned that way"}
    assert "error" in actions.propose_assignment(owner, {"repair_number": req.number, "staff": "nobody here"},
                                                 timezone.localdate())


# ---------------------------------------------------------------------------
# The card is held on the server (item 4)
# ---------------------------------------------------------------------------


def test_dismiss_expiry_and_unknown_ids(client, owner, lease):
    login(client, owner)
    first, second = ask_with(client, FakeClient.tool("propose_payment", pay_args(), "c1"),
                             FakeClient.tool("propose_payment", pay_args(amount=100), "c2"))
    client.post(CONFIRM, {"id": first["id"], "action": "dismiss"})
    client.post(CONFIRM, {"id": first["id"], "action": "confirm"})
    assert "Dismissed" in client.get(URL).content.decode()

    session = client.session
    session[PROPOSALS_KEY][second["id"]]["created"] = (timezone.now() - datetime.timedelta(minutes=31)).isoformat()
    session.save()
    client.post(CONFIRM, {"id": second["id"], "action": "confirm"})
    assert "This has expired" in client.get(URL).content.decode()

    response = client.post(CONFIRM, {"id": "00000000-0000-0000-0000-000000000000", "action": "confirm"}, follow=True)
    assert "no longer available" in response.content.decode()
    assert not Payment.objects.exists()

    client.post(URL, {"action": "new"})
    assert PROPOSALS_KEY not in client.session


def test_confirm_is_refused_when_the_assistant_is_off(client, owner, lease):
    login(client, owner)
    [card] = ask_with(client, FakeClient.tool("propose_payment", pay_args()))
    owner.organization.assistant_enabled = False
    owner.organization.save()
    assert client.post(CONFIRM, {"id": card["id"], "action": "confirm"}).status_code == 403
    assert not Payment.objects.exists()


def test_proposals_per_answer_are_limited_and_dropped_on_failure(owner, lease):
    calls = [FakeClient.tool("propose_payment", pay_args(amount=n), f"c{n}") for n in (1, 2, 3, 4)]
    FakeClient.script = [*calls, FakeClient.text("Cards shown.")]
    result = services.ask(owner, "Record four payments")
    assert len(result.proposals) == actions.MAX_PROPOSALS
    assert "at most 3 proposals" in FakeClient.calls[4]["messages"][-1]["content"][0]["content"]

    FakeClient.calls.clear()
    FakeClient.script = [FakeClient.tool("propose_payment", pay_args())]  # then no answer: fails
    result = services.ask(owner, "Record it")
    assert not result.answered and result.proposals == []


def test_confirm_refuses_a_used_or_unknown_proposal(owner, lease):
    proposal = actions.propose_payment(owner, pay_args(), timezone.localdate())["proposal"]
    with pytest.raises(ValidationError):
        actions.confirm(owner, {**proposal, "state": "done"})
    with pytest.raises(ValidationError):
        actions.confirm(owner, {**proposal, "kind": "delete_everything"})
