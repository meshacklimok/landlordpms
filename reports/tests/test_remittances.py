"""Payments to owners and sending the owner statement (D-058)."""

import datetime
from decimal import Decimal

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from audit.models import AuditEvent
from properties import services as property_services
from reports import owners, statements
from reports.models import OwnerRemittance, OwnerStatementSend
from reports.tests.test_income import bill, make_lease, pay

pytestmark = pytest.mark.django_db

D = datetime.date
JAN, FEB = D(2025, 1, 1), D(2025, 2, 1)


def money(value):
    return Decimal(value).quantize(Decimal("0.01"))


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def jane(owner):
    return property_services.create_owner(owner, name="Jane Wanjiru", phone="+254712000111",
                                          email="jane@example.com")


@pytest.fixture
def prop(owner, jane):
    prop = make_property(owner.organization, name="Acacia Court")
    return property_services.update_property(owner, prop, owner=jane, management_fee_percent=Decimal("10"))


@pytest.fixture
def january(owner, prop):
    """Rent of 20,000 collected in January: 18,000 due to Jane after the 10% fee."""
    lease = make_lease(owner, prop, code="A1")
    bill(lease, JAN)
    pay(owner, lease, 20000, D(2025, 1, 10))
    return lease


def remit(actor, jane, amount="10000", month=JAN, **kw):
    fields = {"paid_on": D(2025, 2, 5), "method": OwnerRemittance.Method.MPESA, "reference": "QK12AB"} | kw
    return owners.record_remittance(actor, jane, month=month, amount=Decimal(amount), **fields)


def st(member, jane, month=JAN):
    return statements.statement(member, str(jane.public_id), month)


# ---------------------------------------------------------------------------
# Recording and voiding
# ---------------------------------------------------------------------------


def test_a_payment_to_the_owner_shows_on_the_statement(owner, jane, january):
    r = remit(owner, jane, "10000")
    statement = st(owner, jane)
    assert statement.shows_remittances and statement.remittances == [r]
    assert (statement.due, statement.remitted, statement.remaining) == (money(18000), money(10000), money(8000))
    # Another month's statement does not count it.
    assert st(owner, jane, FEB).remitted == 0
    assert AuditEvent.objects.filter(action="owner_remittance.record", object_id=str(r.public_id)).exists()


@pytest.mark.parametrize("kw, field", [
    ({"amount": "0"}, "amount"),
    ({"paid_on": D(2999, 1, 1)}, "paid_on"),
    ({"month": D(2999, 1, 1)}, "month"),
])
def test_bad_payments_are_refused(owner, jane, january, kw, field):
    with pytest.raises(ValidationError) as exc:
        remit(owner, jane, **kw)
    assert field in exc.value.message_dict


def test_voiding_takes_the_payment_off_and_keeps_it(owner, jane, january):
    r = remit(owner, jane, "10000")
    with pytest.raises(ValidationError):
        owners.void_remittance(owner, r, "  ")
    owners.void_remittance(owner, r, "Typed the wrong amount")
    r.refresh_from_db()
    assert r.is_void and r.void_reason == "Typed the wrong amount"
    assert st(owner, jane).remitted == 0
    assert owners.account(owner, jane, today=FEB).remittances == [r]
    with pytest.raises(ValidationError):
        owners.void_remittance(owner, r, "again")
    assert AuditEvent.objects.filter(action="owner_remittance.void").count() == 1


def test_who_may_record_payments(owner, jane, january):
    org = owner.organization
    remit(add_member(org, "accountant", all_properties=True), jane)
    for role in ("manager", "caretaker", "viewer"):
        with pytest.raises(PermissionDenied):
            remit(add_member(org, role, all_properties=True), jane)


def test_a_member_who_sees_only_some_of_the_owners_properties(owner, jane, prop, january):
    other = make_property(owner.organization, name="Baobab Flats")
    property_services.update_property(owner, other, owner=jane)
    remit(owner, jane)
    accountant = add_member(owner.organization, "accountant", properties=[prop])
    statement = st(accountant, jane)
    # The due figure is partial, so payments against the whole are hidden.
    assert not statement.shows_remittances
    assert any("not shown" in n for n in statement.notes)
    with pytest.raises(PermissionDenied):
        remit(accountant, jane)
    assert owners.visible_owner(accountant, jane.public_id) is None


def test_the_owner_account(owner, jane, january):
    remit(owner, jane, "15000")
    account = owners.account(owner, jane, today=D(2025, 3, 10))
    assert [r.month for r in account.rows[:2]] == [FEB, JAN] and len(account.rows) == 12
    jan = account.rows[1]
    assert (jan.collected, jan.fee, jan.due, jan.remitted, jan.remaining) == (
        money(20000), money(2000), money(18000), money(15000), money(3000))
    assert account.remaining == money(3000)


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def test_sending_emails_the_pdf_and_texts_the_summary(owner, jane, january):
    remit(owner, jane, "18000")
    send = owners.send_statement(owner, st(owner, jane), sms=True)
    assert send.number.startswith("OST-") and send.pdf.read().startswith(b"%PDF")
    assert (send.due, send.remitted, send.remaining) == (money(18000), money(18000), money(0))
    assert send.email_status == OwnerStatementSend.EmailStatus.SENT
    (email,) = mail.outbox
    assert email.to == ["jane@example.com"] and "January 2025" in email.subject
    (name, content, mimetype) = email.attachments[0]
    assert name.endswith("2025-01.pdf") and mimetype == "application/pdf" and content.startswith(b"%PDF")
    assert send.sms.to == "+254712000111" and "due to you KES 18,000.00" in send.sms.body
    assert AuditEvent.objects.filter(action="owner_statement.send", object_id=str(send.public_id)).exists()
    # Sending again after a correction makes a new numbered copy.
    assert owners.send_statement(owner, st(owner, jane)).number != send.number


def test_a_failed_email_is_kept_with_its_error(owner, jane, january, monkeypatch):
    def fail(self, *a, **kw):
        raise OSError("SMTP down")

    monkeypatch.setattr("django.core.mail.EmailMessage.send", fail)
    send = owners.send_statement(owner, st(owner, jane))
    assert send.email_status == OwnerStatementSend.EmailStatus.FAILED and "SMTP down" in send.email_error


def test_when_a_statement_cannot_be_sent(owner, jane, prop, january):
    jane.email = ""
    jane.save()
    # No email and no SMS asked for: nothing would go.
    with pytest.raises(ValidationError):
        owners.send_statement(owner, st(owner, jane))
    # A month not over yet.
    assert owners.send_blocker(owner, st(owner, jane), today=D(2025, 1, 31))
    # No contact at all.
    jane.phone = ""
    jane.save()
    assert "email or phone" in owners.send_blocker(owner, st(owner, jane))
    # A caretaker may not send.
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    assert owners.send_blocker(caretaker, st(owner, jane)) == "Not allowed."


def test_statements_to_send_on_the_home_page(owner, jane, january):
    today = D(2025, 2, 10)
    assert owners.to_send(owner, today) == (1, JAN)
    owners.send_statement(owner, st(owner, jane))
    assert owners.to_send(owner, today)[0] == 0
    # An owner with no way to reach them is not counted.
    property_services.create_owner(owner, name="No Contact")
    assert owners.to_send(owner, D(2025, 3, 10))[0] == 1
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    assert owners.to_send(caretaker, today)[0] == 0


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def statement_url(jane, month=JAN):
    return f"{reverse('reports:owner_statement')}?owner={jane.public_id}&month={month:%Y-%m}"


def test_the_statement_page_records_voids_and_sends(client, owner, jane, january):
    login(client, owner)
    page = client.get(statement_url(jane)).content.decode()
    assert "Still to pay" in page and "Send to owner" in page and "Record payment to the owner" in page
    response = client.post(reverse("reports:remittance_create"), {
        "owner": jane.public_id, "month": "2025-01", "amount": "5000", "paid_on": "2025-02-05",
        "method": "BANK", "reference": "FT123", "note": ""})
    assert response.status_code == 302 and response.url == statement_url(jane)
    r = OwnerRemittance.objects.get()
    assert (r.amount, r.month, r.method) == (money(5000), JAN, "BANK")
    client.post(reverse("reports:remittance_void", args=[r.public_id]), {"reason": "Duplicate"})
    r.refresh_from_db()
    assert r.is_void
    client.post(reverse("reports:statement_send"), {"owner": jane.public_id, "month": "2025-01", "sms": "on"})
    send = OwnerStatementSend.objects.get()
    assert send.sms is not None and len(mail.outbox) == 1
    pdf = client.get(reverse("reports:statement_send_pdf", args=[send.public_id]))
    assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf"
    assert send.number in client.get(statement_url(jane)).content.decode()
    account = client.get(reverse("reports:owner_account", args=[jane.public_id])).content.decode()
    assert "The last 12 months" in account and "Duplicate" in account


def test_a_viewer_sees_payments_but_cannot_record_or_send(client, owner, jane, january):
    remit(owner, jane, "5000")
    login(client, add_member(owner.organization, "viewer", all_properties=True))
    page = client.get(statement_url(jane)).content.decode()
    assert "QK12AB" in page and "Record payment to the owner" not in page and "Send to owner" not in page
    assert client.post(reverse("reports:remittance_create"), {
        "owner": jane.public_id, "month": "2025-01", "amount": "1", "paid_on": "2025-02-05",
        "method": "CASH"}).status_code == 403
    assert client.post(reverse("reports:statement_send"), {"owner": jane.public_id,
                                                           "month": "2025-01"}).status_code == 403


def test_another_organization_gets_404(client, owner, jane, january):
    r = remit(owner, jane)
    send = owners.send_statement(owner, st(owner, jane))
    login(client, make_org())
    assert client.get(reverse("reports:owner_account", args=[jane.public_id])).status_code == 404
    assert client.get(reverse("reports:statement_send_pdf", args=[send.public_id])).status_code == 404
    assert client.post(reverse("reports:remittance_void", args=[r.public_id]),
                       {"reason": "x"}).status_code == 404
    assert client.post(reverse("reports:remittance_create"), {
        "owner": jane.public_id, "month": "2025-01", "amount": "1", "paid_on": "2025-02-05",
        "method": "CASH"}).status_code == 404
    r.refresh_from_db()
    assert not r.is_void


def test_the_home_page_lists_statements_to_send(client, owner, jane, prop):
    login(client, owner)
    assert "owner statement to send" in client.get(reverse("accounts:home")).content.decode()
