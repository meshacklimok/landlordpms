# ruff: noqa: F811
"""The owner desk (D-217): messages, approvals and payouts between an owner and the office."""

from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from audit.models import AuditEvent
from expenses.models import Expense, ExpenseCategory
from maintenance import services as maintenance
from notifications.models import Message
from owner_portal import desk, selectors
from owner_portal.models import OwnerApproval, OwnerPayoutDetail, OwnerPayoutRequest
from owner_portal.tests.test_owner_portal import D, jane, join, media, owner, prop, tom  # noqa: F401 - fixtures

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def run_on_commit(monkeypatch):
    """The tests run inside a transaction, so alerts queued for after commit are sent at once."""
    monkeypatch.setattr("django.db.transaction.on_commit", lambda func, *a, **k: func())


@pytest.fixture
def account(owner, jane, prop):
    return selectors.live_accounts(join(owner, jane)).get()


@pytest.fixture
def expense(owner, prop):
    category = ExpenseCategory.objects.create(organization=owner.organization, name="Repairs")
    return Expense.objects.create(
        organization=owner.organization, number="EXP-1", property=prop, category=category,
        description="New gate", amount=Decimal("12000"), paid_on=D(2025, 2, 1), recorded_by=owner.user)


def test_messages_both_ways_alert_the_other_side(owner, jane, account):
    desk.owner_send(account, "Please fix the gate")
    assert Message.objects.filter(type="owner_message_received", user=owner.user).exists()
    desk.staff_send(owner, jane, "On it")
    assert Message.objects.filter(type="owner_message_reply", to=jane.phone).exists()
    assert [m.side for m in desk.thread(jane)] == ["OWNER", "STAFF"]
    assert desk.mark_read_by_owner(jane) == 1 and desk.mark_read_by_staff(jane) == 1
    with pytest.raises(ValidationError):
        desk.owner_send(account, "   ")


def test_an_expense_approval_is_a_record_and_changes_nothing(owner, jane, account, expense):
    approval = desk.ask_approval(owner, jane, kind="EXPENSE", subject_id=expense.public_id, note="Urgent")
    assert approval.amount == Decimal("12000") and Message.objects.filter(type="owner_approval_asked").exists()
    with pytest.raises(ValidationError):  # one open question per expense
        desk.ask_approval(owner, jane, kind="EXPENSE", subject_id=expense.public_id)
    desk.answer_approval(account, approval, False, "Too dear")
    approval.refresh_from_db()
    expense.refresh_from_db()
    assert approval.status == OwnerApproval.Status.DECLINED and approval.answer_note == "Too dear"
    assert expense.status == Expense.Status.SUBMITTED
    assert Message.objects.filter(type="owner_approval_answered", user=owner.user).exists()
    with pytest.raises(ValidationError):
        desk.answer_approval(account, approval, True)
    assert AuditEvent.objects.filter(action="owner_portal.approval_answer").exists()


def test_a_repair_can_be_asked_and_withdrawn(owner, jane, account, prop):
    repair = maintenance.report(owner, prop, title="Leaking roof")
    approval = desk.ask_approval(owner, jane, kind="REPAIR", subject_id=repair.public_id)
    desk.withdraw_approval(owner, approval)
    approval.refresh_from_db()
    assert approval.status == OwnerApproval.Status.WITHDRAWN
    with pytest.raises(ValidationError):
        desk.answer_approval(account, approval, True)


def test_only_the_owners_own_properties_can_be_asked_about(owner, jane, tom, account, expense):
    from accounts.tests.factories import make_property
    from properties import services as property_services

    other = make_property(owner.organization, name="Cedar")
    property_services.update_property(owner, other, owner=tom)
    foreign = maintenance.report(owner, other, title="Cracked wall")
    with pytest.raises(ValidationError):
        desk.ask_approval(owner, jane, kind="REPAIR", subject_id=foreign.public_id)


def test_another_owner_cannot_answer_my_approval(owner, jane, tom, account, expense, client):
    from django.urls import reverse

    from accounts.tests.factories import make_user

    approval = desk.ask_approval(owner, jane, kind="EXPENSE", subject_id=expense.public_id)
    toms = make_user(phone=tom.phone)
    from owner_portal import services

    _inv, token = services.invite_owner(owner, tom)
    services.accept_invitation(token, toms)
    tom_account = selectors.live_accounts(toms).get()
    client.force_login(toms)
    url = reverse("owner_portal:desk_answer", args=[tom_account.public_id, approval.public_id])
    assert client.post(url, {"decision": "approve"}).status_code == 404
    approval.refresh_from_db()
    assert approval.status == OwnerApproval.Status.ASKED


def test_payout_details_need_confirmation_before_a_request(owner, jane, account):
    with pytest.raises(ValidationError):
        desk.request_payout(account, Decimal("5000"))
    detail = desk.submit_detail(account, method="MPESA", account_name="Jane Wanjiru", account_number="0712 000 111")
    assert detail.status == OwnerPayoutDetail.Status.PENDING and detail.masked == "…0111"
    assert Message.objects.filter(type="owner_payout_event", user=owner.user).exists()
    desk.decide_detail(owner, detail, True)
    assert desk.confirmed_detail(jane).pk == detail.pk
    # New details replace the confirmed ones only once confirmed.
    newer = desk.submit_detail(account, method="BANK", account_name="Jane W", account_number="0123456789",
                               bank_name="KCB")
    assert desk.confirmed_detail(jane).pk == detail.pk
    desk.decide_detail(owner, newer, True)
    detail.refresh_from_db()
    assert detail.status == OwnerPayoutDetail.Status.REPLACED and desk.confirmed_detail(jane).pk == newer.pk
    ask = desk.request_payout(account, Decimal("5000"), "School fees")
    with pytest.raises(ValidationError):
        desk.request_payout(account, None)
    desk.handle_request(owner, ask, True, "Sent")
    ask.refresh_from_db()
    assert ask.status == OwnerPayoutRequest.Status.DONE


def test_bad_payout_details_are_refused(account):
    with pytest.raises(ValidationError):
        desk.submit_detail(account, method="MPESA", account_name="J", account_number="abc")
    with pytest.raises(ValidationError):
        desk.submit_detail(account, method="BANK", account_name="J", account_number="12345678")


def test_revoked_owner_cannot_use_the_desk(owner, jane, account):
    from owner_portal import services

    services.revoke_account(owner, account)
    with pytest.raises(PermissionDenied):
        desk.owner_send(account, "hello")


def test_desk_pages_render(client, owner, jane, account, expense):
    from django.urls import reverse

    desk.ask_approval(owner, jane, kind="EXPENSE", subject_id=expense.public_id)
    client.force_login(account.user)
    body = client.get(reverse("owner_portal:desk", args=[account.public_id])).content.decode()
    assert "New gate" in body and "Approve" in body
    client.force_login(owner.user)
    response = client.get(reverse("owner_portal:staff_desk", args=[jane.public_id]))
    assert response.status_code == 200 and "Owner desk" in response.content.decode()


# ---------------------------------------------------------------------------
# People the owner chooses
# ---------------------------------------------------------------------------


def test_a_guest_sees_the_figures_but_cannot_use_the_desk(client, owner, jane, account):
    from django.urls import reverse

    from accounts.tests.factories import make_user
    from owner_portal import services

    invitation, token = services.invite_guest(account, "Peter Kamau", "0722 000 333")
    assert invitation.phone == "+254722000333" and invitation.is_delegate
    guest = make_user(phone="+254722000333")
    services.accept_invitation(token, guest)
    guest_account = selectors.live_accounts(guest).get()
    assert guest_account.is_delegate and services.live_account(jane).pk == account.pk
    client.force_login(guest)
    assert client.get(reverse("owner_portal:account", args=[guest_account.public_id])).status_code == 200
    assert client.get(reverse("owner_portal:desk", args=[guest_account.public_id])).status_code == 404
    assert client.get(reverse("owner_portal:guests", args=[guest_account.public_id])).status_code == 404
    assert client.post(reverse("owner_portal:desk_send", args=[guest_account.public_id]),
                       {"body": "hi"}).status_code == 404
    with pytest.raises(PermissionDenied):
        desk.owner_send(guest_account, "hi")


def test_guest_limits_and_removal(owner, jane, account):
    from owner_portal import services

    for i in range(services.MAX_GUESTS):
        services.invite_guest(account, f"Guest {i}", f"07220000{i:02d}")
    with pytest.raises(ValidationError):
        services.invite_guest(account, "One more", "0722000099")
    with pytest.raises(ValidationError):
        services.invite_guest(account, "Self", jane.phone)
    pending = services.guests(jane)["invitations"][0]
    services.remove_guest(account, invitation_id=pending.public_id)
    assert len(services.guests(jane)["invitations"]) == services.MAX_GUESTS - 1


def test_removing_the_owners_access_removes_the_guests(owner, jane, account):
    from accounts.tests.factories import make_user
    from owner_portal import services

    _inv, token = services.invite_guest(account, "Peter", "0722000333")
    guest = make_user(phone="+254722000333")
    services.accept_invitation(token, guest)
    services.revoke_account(owner, account)
    assert not selectors.live_accounts(guest).exists()
