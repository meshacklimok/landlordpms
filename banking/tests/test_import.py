"""Statement import, matching, the bank inbox and the pages (D-064)."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from audit.models import AuditEvent
from banking import inbox, matching, services
from banking.models import BankTransaction, StatementImport
from billing.invoicing import lease_balance
from billing.tests.test_invoicing import FEB, bill, make_lease
from leases.models import LeaseTenant
from mpesa import services as mpesa_services
from mpesa.models import MpesaTransaction
from notifications.models import Message
from payments import services as payment_services
from payments.models import Payment

pytestmark = pytest.mark.django_db

Status = BankTransaction.Status
HEADER = "Date,Description,Reference,Debit,Credit,Balance\n"


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
def account(owner, prop):
    return services.add_bank_account(owner, bank="Equity", number="0123456789", properties=[prop])


def csv(*rows: str) -> SimpleUploadedFile:
    return SimpleUploadedFile("statement.csv", (HEADER + "\n".join(rows) + "\n").encode(), "text/csv")


def ref(lease) -> str:
    return lease.unit.payment_reference


def run(owner, account, *rows):
    return services.apply(owner, services.preview(owner, account, csv(*rows)))


# ---------------------------------------------------------------------------
# Bank accounts
# ---------------------------------------------------------------------------


def test_adding_a_bank_account(owner, account, prop):
    assert account.display_name == "Equity 6789"
    assert list(account.properties.values_list("property", flat=True)) == [prop.pk]
    assert AuditEvent.objects.filter(action="payment_account.create").exists()
    with pytest.raises(ValidationError):
        services.add_bank_account(owner, bank="Equity", number="0123 456789")
    with pytest.raises(ValidationError):
        services.add_bank_account(owner, bank="", number="")


def test_only_managers_add_bank_accounts(org):
    with pytest.raises(PermissionDenied):
        services.add_bank_account(add_member(org, "accountant", all_properties=True), bank="KCB", number="1")


# ---------------------------------------------------------------------------
# Preview and import
# ---------------------------------------------------------------------------


def test_preview_then_import_matches_by_unit_reference(owner, account, lease):
    batch = services.preview(owner, account, csv(
        f"03/02/2026,CASH DEP {ref(lease)} JANE,FT001,,15000,15000",
        "04/02/2026,Bank charges,,50,,14950",
        "05/02/2026,RTGS SOMEONE ELSE,FT002,,700,15650"))
    assert (batch.status, batch.new_count, batch.duplicate_count, batch.skipped_count) == ("PREVIEW", 2, 0, 1)
    assert not BankTransaction.objects.exists()

    batch = services.apply(owner, batch)
    assert (batch.status, batch.new_count, batch.matched_count, batch.unmatched_count) == ("APPLIED", 2, 1, 1)
    assert batch.rows == []
    matched = BankTransaction.objects.get(reference="FT001")
    assert (matched.status, matched.matched_by) == (Status.MATCHED, "REFERENCE")
    payment = matched.payment
    assert (payment.status, payment.method, payment.amount, payment.reference, payment.lease) == (
        Payment.Status.CONFIRMED, Payment.Method.BANK, Decimal("15000.00"), "FT001", lease)
    assert payment.paid_at == datetime.date(2026, 2, 3)
    assert lease_balance(lease) == 0
    other = BankTransaction.objects.get(reference="FT002")
    assert other.status == Status.UNMATCHED
    assert "No unit reference" in other.note
    assert AuditEvent.objects.filter(action="statement.import").exists()


def test_the_reference_split_across_two_words_matches(owner, account, lease):
    code = lease.unit.property.code
    run(owner, account, f"03/02/2026,MPESA DEP {code.lower()} a1 from Jane,FT001,,15000,")
    assert BankTransaction.objects.get().status == Status.MATCHED


def test_an_overlapping_statement_imports_only_new_lines(owner, account, lease):
    first = f"03/02/2026,CASH DEP {ref(lease)},FT001,,15000,15000"
    run(owner, account, first, "04/02/2026,Transfer,FT002,,100,15100")
    batch = services.preview(owner, account, csv(first, "04/02/2026,Transfer,FT002,,100,15100",
                                                 "04/02/2026,Transfer,FT002,,100,15100",
                                                 "06/02/2026,Transfer,FT003,,200,15300"))
    # Two identical lines differ by their occurrence; only the second is new.
    assert (batch.new_count, batch.duplicate_count) == (2, 2)
    services.apply(owner, batch)
    assert BankTransaction.objects.count() == 4
    assert Payment.objects.count() == 1


def test_a_file_that_cannot_be_read_is_refused(owner, account):
    with pytest.raises(ValidationError, match="date column"):
        services.preview(owner, account, SimpleUploadedFile("x.csv", b"Name,Phone\nA,1\n"))


def test_a_preview_is_imported_once_and_expires(owner, account, lease):
    batch = services.preview(owner, account, csv("03/02/2026,Deposit,FT001,,100,"))
    services.discard(owner, batch)
    with pytest.raises(ValidationError):
        services.apply(owner, batch)
    batch = services.preview(owner, account, csv("03/02/2026,Deposit,FT001,,100,"))
    StatementImport.objects.filter(pk=batch.pk).update(created_at=timezone.now() - datetime.timedelta(days=2))
    batch.refresh_from_db()
    with pytest.raises(ValidationError, match="expired"):
        services.apply(owner, batch)
    call_command("purge_import_previews")
    batch.refresh_from_db()
    assert batch.status == StatementImport.Status.DISCARDED


# ---------------------------------------------------------------------------
# Matching rules
# ---------------------------------------------------------------------------


def test_a_reference_already_on_a_payment_is_left_for_staff(owner, account, lease):
    payment_services.record_payment(owner, lease, amount=15000, method=Payment.Method.BANK,
                                    paid_at=datetime.date(2026, 2, 3), reference="FT001")
    run(owner, account, f"03/02/2026,CASH DEP {ref(lease)},FT001,,15000,")
    line = BankTransaction.objects.get()
    assert (line.status, line.suggested_lease) == (Status.UNMATCHED, lease)
    assert "already recorded" in line.note
    with pytest.raises(ValidationError):
        inbox.match(owner, line, lease)


def test_a_same_amount_bank_payment_nearby_is_left_for_staff(owner, account, lease):
    payment_services.record_payment(owner, lease, amount=15000, method=Payment.Method.BANK,
                                    paid_at=datetime.date(2026, 2, 1), reference="counter slip")
    run(owner, account, f"03/02/2026,CASH DEP {ref(lease)},FT001,,15000,")
    line = BankTransaction.objects.get()
    assert (line.status, line.suggested_lease) == (Status.UNMATCHED, lease)
    assert "same amount" in line.note


def test_two_unit_references_are_not_guessed(owner, account, prop, lease):
    other = make_lease(owner, prop, code="A2")
    run(owner, account, f"03/02/2026,{ref(lease)} and {ref(other)},FT001,,30000,")
    line = BankTransaction.objects.get()
    assert line.status == Status.UNMATCHED
    assert "more than one" in line.note


def test_a_unit_the_account_does_not_serve_is_not_matched(owner, org, account, lease):
    elsewhere = make_lease(owner, make_property(org))
    run(owner, account, f"03/02/2026,CASH DEP {ref(elsewhere)},FT001,,15000,")
    assert BankTransaction.objects.get().status == Status.UNMATCHED


def test_the_payers_name_suggests_a_lease(owner, account, lease):
    tenant = LeaseTenant.objects.get(lease=lease).tenant
    tenant.name = "Jane Wanjiru Doe"
    tenant.save()
    run(owner, account, "03/02/2026,RTGS FROM JANE DOE,FT001,,15000,")
    line = BankTransaction.objects.get()
    assert (line.status, line.suggested_lease) == (Status.UNMATCHED, lease)
    assert line.payment is None
    inbox.accept_suggestion(owner, line)
    line.refresh_from_db()
    assert (line.status, line.matched_by, line.matched_by_user) == (Status.MATCHED, "MANUAL", owner.user)
    assert AuditEvent.objects.filter(action="bank.match").exists()


def test_tokens_join_neighbouring_words():
    assert {"GVA1", "GV", "A1"} <= matching.tokens("paid gv a1")


# ---------------------------------------------------------------------------
# The inbox
# ---------------------------------------------------------------------------


def test_match_ignore_restore(owner, account, lease):
    run(owner, account, "03/02/2026,Deposit,FT001,,15000,", "04/02/2026,Own transfer,FT002,,500,")
    one, two = BankTransaction.objects.order_by("posted_on")
    assert list(inbox.inbox(owner)) == [one, two]

    inbox.match(owner, one, lease)
    with pytest.raises(ValidationError):
        inbox.match(owner, one, lease)
    with pytest.raises(ValidationError):
        inbox.ignore(owner, two, reason=" ")
    inbox.ignore(owner, two, reason="Our own transfer")
    two.refresh_from_db()
    assert two.status == Status.IGNORED
    assert "Our own transfer" in two.note
    assert list(inbox.inbox(owner)) == []
    inbox.restore(owner, two)
    assert list(inbox.inbox(owner)) == [two]


def test_reversing_the_payment_returns_the_line_to_the_inbox(owner, account, lease):
    run(owner, account, f"03/02/2026,CASH DEP {ref(lease)},FT001,,15000,")
    line = BankTransaction.objects.get()
    payment_services.reverse_payment(owner, line.payment, reason="Wrong unit")
    line.refresh_from_db()
    assert (line.status, line.payment, line.matched_at) == (Status.UNMATCHED, None, None)
    assert "Wrong unit" in line.note
    assert lease_balance(lease) == 15000
    other = make_lease(owner, lease.unit.property, code="A2")
    inbox.match(owner, line, other)
    assert lease_balance(other) == -15000


# ---------------------------------------------------------------------------
# Scope and permissions
# ---------------------------------------------------------------------------


def test_scope_follows_the_properties_the_account_serves(owner, org, prop, account, lease):
    run(owner, account, "03/02/2026,Deposit,FT001,,15000,")
    line = BankTransaction.objects.get()
    here = add_member(org, "accountant", properties=[prop])
    elsewhere = add_member(org, "accountant", properties=[make_property(org)])
    caretaker = add_member(org, "caretaker", properties=[prop])
    assert list(inbox.inbox(here)) == [line]
    assert list(inbox.inbox(elsewhere)) == []
    assert list(services.importable_accounts(elsewhere)) == []
    assert list(services.importable_accounts(caretaker)) == []
    with pytest.raises(PermissionDenied):
        inbox.ignore(elsewhere, line, reason="x")
    with pytest.raises(PermissionDenied):
        services.preview(elsewhere, account, csv("03/02/2026,Deposit,FT009,,1,"))


def test_another_organization_sees_nothing(account, owner, lease):
    run(owner, account, "03/02/2026,Deposit,FT001,,15000,")
    stranger = make_org(name="Other")
    assert list(inbox.visible_lines(stranger)) == []
    with pytest.raises(PermissionDenied):
        inbox.match(stranger, BankTransaction.objects.get(), lease)


# ---------------------------------------------------------------------------
# M-Pesa statement
# ---------------------------------------------------------------------------


MPESA_HEADER = ("Receipt No.,Completion Time,Details,Transaction Status,Paid In,Withdrawn,Other Party Info,"
                "A/C No.\n")


def test_an_mpesa_statement_adds_statement_transactions_without_alerts(owner, lease):
    paybill = mpesa_services.add_account(owner, type="PAYBILL", number="600123", display_name="Rent Paybill")
    rows = (f"QAB12CD301,03-02-2026 10:15:00,Pay Bill,Completed,15000.00,,254712345678 - JANE DOE,{ref(lease)}\n"
            "QAB12CD302,03-02-2026 11:00:00,Pay Bill,Completed,500.00,,2547****5678 - JOHN DOE,ZZ-99\n")
    upload = SimpleUploadedFile("mpesa.csv", (MPESA_HEADER + rows).encode())
    batch = services.apply(owner, services.preview(owner, paybill, upload))
    assert (batch.kind, batch.new_count, batch.matched_count, batch.unmatched_count) == ("MPESA", 2, 1, 1)
    matched = MpesaTransaction.objects.get(trans_id="QAB12CD301")
    assert (matched.source, matched.status) == (MpesaTransaction.Source.STATEMENT, MpesaTransaction.Status.MATCHED)
    assert matched.payer_phone == "+254712345678"
    unmatched = MpesaTransaction.objects.get(trans_id="QAB12CD302")
    assert unmatched.payer_phone == ""
    assert not Message.objects.filter(type__in=["mpesa_unmatched", "payment_unmatched"]).exists()

    again = services.preview(owner, paybill, SimpleUploadedFile("mpesa.csv", (MPESA_HEADER + rows).encode()))
    assert (again.new_count, again.duplicate_count) == (0, 2)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def test_the_pages(client, owner, prop, account, lease):
    login(client, owner)
    assert client.get(reverse("banking:accounts")).status_code == 200
    response = client.post(reverse("banking:accounts"), {"bank": "KCB", "number": "998877", "properties": [prop.pk]})
    assert response.status_code == 302
    assert client.get(reverse("banking:import")).status_code == 200

    response = client.post(reverse("banking:import"), {"account": account.pk,
                                                        "file": csv("03/02/2026,Deposit,FT001,,15000,")})
    assert response.status_code == 302
    batch = StatementImport.objects.get()
    detail = reverse("banking:import_detail", args=[batch.public_id])
    assert response.url == detail
    assert b"Deposit" in client.get(detail).content
    assert client.post(detail, {"action": "apply"}).status_code == 302

    line = BankTransaction.objects.get()
    assert b"Deposit" in client.get(reverse("banking:inbox")).content
    page = client.get(reverse("banking:line", args=[line.public_id]), {"q": lease.unit.code})
    assert lease.number.encode() in page.content
    response = client.post(reverse("banking:line", args=[line.public_id]),
                           {"action": "match", "lease": lease.public_id})
    assert response.status_code == 302
    line.refresh_from_db()
    assert line.status == Status.MATCHED
    assert b"Deposit" in client.get(reverse("banking:lines"), {"status": "MATCHED"}).content
    assert client.get(reverse("banking:line", args=[line.public_id])).status_code == 200


def test_a_member_without_the_capability_is_refused(client, org, prop, account):
    login(client, add_member(org, "caretaker", properties=[prop]))
    assert client.get(reverse("banking:import")).status_code == 403
    assert client.get(reverse("banking:inbox")).status_code == 403
