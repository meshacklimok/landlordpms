"""The accounting export: journal, accounts and registers (D-075)."""

import csv
import datetime
import io
import zipfile
from decimal import Decimal

import pytest
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from audit.models import AuditEvent
from billing import deposits, invoicing
from billing.models import DepositEntry, LedgerEntry
from billing.tests.test_invoicing import FEB, JAN, bill, make_lease
from expenses import services as expense_services
from payments import services as payment_services
from payments.models import Payment
from properties import services as property_services
from reports import accounting
from reports.accounting import DEPOSITS_HELD, OWNERS, RECEIVABLE
from reports.models import OwnerRemittance

pytestmark = pytest.mark.django_db

D = datetime.date
DEC = D(2025, 12, 1)


def money(value) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"))


def pay(owner, lease, amount, day, method=Payment.Method.CASH):
    return payment_services.record_payment(owner, lease, amount=amount, method=method, paid_at=day)


def net(export, account) -> Decimal:
    return next((a.net for a in export.accounts() if a.name == account), Decimal(0))


def journal(export, number):
    return next(j for j in export.journals if j.number == number)


@pytest.fixture
def owner():
    return make_org(name="Acme")


@pytest.fixture
def org(owner):
    return owner.organization


@pytest.fixture
def acacia(org):
    return make_property(org, name="Acacia")


@pytest.fixture
def baobab(org):
    return make_property(org, name="Baobab")


@pytest.fixture
def books(owner, org, acacia, baobab):
    """Acacia A1: 15,000 a month for Jan and Feb, 35,000 paid, a 15,000 deposit with 3,000 refunded,
    2,000 kept for damage and 1,000 put towards the balance, a balance brought forward of 4,000.
    Baobab B1: 10,000 a month, nothing paid. An 8,000 payment to an owner and an expense."""
    a1 = make_lease(owner, acacia, code="A1")
    bill(a1, JAN)
    bill(a1, FEB)
    pay(owner, a1, 15000, D(2026, 1, 10))
    pay(owner, a1, 20000, D(2026, 2, 8), method=Payment.Method.MPESA)
    deposits.record_received(owner, a1, amount=15000, entry_date=JAN)
    deposits.refund(owner, a1, amount=3000, entry_date=D(2026, 2, 20))
    deposits.deduct(owner, a1, amount=2000, reason="Broken window", entry_date=D(2026, 2, 21))
    deposits.deduct(owner, a1, amount=1000, reason="Towards rent", entry_date=D(2026, 2, 22), apply_to_balance=True)
    invoicing.set_opening_balance(owner, a1, amount=4000, as_of=D(2026, 1, 1))
    b1 = make_lease(owner, baobab, code="B1", rent=10000)
    bill(b1, JAN)
    bill(b1, FEB)
    jane = property_services.create_owner(owner, name="Jane Wanjiru", phone="+254712000111")
    OwnerRemittance.objects.create(organization=org, owner=jane, month=JAN, amount=8000, paid_on=D(2026, 2, 25))
    repairs = expense_services.categories(org).get(name="Repairs and maintenance")
    expense_services.record_expense(owner, acacia, category=repairs, description="Fixed the gate", amount="2500",
                                    paid_on=D(2026, 2, 3), method="BANK")
    return a1, b1


# ---------------------------------------------------------------------------
# The journal
# ---------------------------------------------------------------------------


def test_every_journal_balances_and_receivables_follow_the_ledger(owner, org, books):
    export = accounting.build(owner, DEC, FEB)
    assert export.journals
    assert all(j.balanced for j in export.journals)
    assert export.total_debit == export.total_credit
    ledger = LedgerEntry.objects.filter(organization=org).aggregate(s=Sum("amount"))["s"]
    assert net(export, RECEIVABLE) == ledger == money(50000 + 4000 - 35000 - 1000)
    held = DepositEntry.objects.filter(organization=org).aggregate(s=Sum("amount"))["s"]
    assert net(export, DEPOSITS_HELD) == -held == money(-9000)


def test_income_by_charge_type_and_money_by_account(owner, books):
    a1, _b1 = books
    export = accounting.build(owner, DEC, FEB)
    assert net(export, "Income - Rent") == money(-50000)
    assert net(export, "Cash") == money(15000)
    assert net(export, "M-Pesa") == money(20000 - 8000)  # the owner was paid from M-Pesa too
    invoice = a1.invoices.order_by("pk").first()
    lines = journal(export, invoice.number).lines
    assert [(line.account, line.amount) for line in lines] == [(RECEIVABLE, money(15000)),
                                                                ("Income - Rent", money(-15000))]
    assert lines[0].name == "Tenant A1" and lines[0].place == "Acacia"


def test_deposits_are_held_not_income(owner, books):
    export = accounting.build(owner, DEC, FEB)
    assert net(export, "Deposit clearing") == money(15000)
    assert net(export, "Money account not recorded") == money(-3000)
    assert net(export, "Income - Deposit deductions") == money(-2000)
    # The 1,000 put towards the balance is one journal: deposits held against receivables.
    applied = [j for j in export.journals if any(line.account == DEPOSITS_HELD for line in j.lines)
               and any(line.account == RECEIVABLE for line in j.lines)]
    assert len(applied) == 1
    assert {line.account: line.amount for line in applied[0].lines} == {RECEIVABLE: money(-1000),
                                                                        DEPOSITS_HELD: money(1000)}


def test_expenses_and_payments_to_owners(owner, books):
    export = accounting.build(owner, DEC, FEB)
    assert net(export, "Expense - Repairs and maintenance") == money(2500)
    assert net(export, "Bank") == money(-2500)
    assert net(export, OWNERS) == money(8000)
    assert net(export, "M-Pesa") == money(20000 - 8000)
    assert net(export, "Opening balance equity") == money(-4000)


def test_one_property_or_a_scoped_member_leaves_out_owner_payments(owner, org, books, acacia, baobab):
    export = accounting.build(owner, DEC, FEB, baobab)
    assert not export.whole_organization
    assert net(export, OWNERS) == 0
    assert net(export, RECEIVABLE) == money(20000)
    assert {j.lines[0].place for j in export.journals} == {"Baobab"}
    member = add_member(org, "accountant", properties=[baobab])
    scoped = accounting.build(member, DEC, FEB)
    assert net(scoped, RECEIVABLE) == money(20000) and net(scoped, OWNERS) == 0
    assert [i.lease.unit.code for i in scoped.invoices] == ["B1", "B1"]


def test_voids_and_reversals_are_entries_on_their_own_day(owner, org, acacia):
    today = timezone.localdate()
    this_month = today.replace(day=1)
    lease = make_lease(owner, acacia, code="A1", start=this_month)
    invoice = bill(lease, this_month, today=today)
    payment = pay(owner, lease, 5000, today)
    payment_services.reverse_payment(owner, payment, reason="Bounced")
    invoicing.void_invoice(owner, invoice, reason="Wrong rent")
    export = accounting.build(owner, this_month, this_month)
    assert all(j.balanced for j in export.journals)
    numbers = {j.number for j in export.journals}
    receipt = payment.receipt.number
    assert {invoice.number, f"{invoice.number}-V", receipt, f"{receipt}-R"} <= numbers
    assert net(export, RECEIVABLE) == 0
    assert net(export, "Cash") == 0
    rows = list(accounting._payment_rows(export))
    assert rows[1][10] == "Reversed" and rows[1][11] == today.isoformat()


def test_a_voided_expense_is_reversed_when_voided(owner, org, acacia):
    today = timezone.localdate()
    repairs = expense_services.categories(org).get(name="Repairs and maintenance")
    early = D(today.year, today.month, 1) - datetime.timedelta(days=40)
    expense = expense_services.record_expense(owner, acacia, category=repairs, description="Paint", amount="900",
                                              paid_on=early, method="CASH")
    expense_services.void(owner, expense, reason="Entered twice")
    then = accounting.build(owner, early.replace(day=1), early.replace(day=1))
    assert net(then, "Expense - Repairs and maintenance") == money(900)
    now = accounting.build(owner, today.replace(day=1), today.replace(day=1))
    assert [j.number for j in now.journals] == [f"{expense.number}-V"]
    assert net(now, "Expense - Repairs and maintenance") == money(-900)


# ---------------------------------------------------------------------------
# The ZIP and the page
# ---------------------------------------------------------------------------


def read_zip(content: bytes) -> dict[str, list[list[str]]]:
    out = {}
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for name in archive.namelist():
            text = archive.read(name).decode("utf-8")
            out[name] = list(csv.reader(io.StringIO(text.lstrip("﻿")))) if name.endswith(".csv") else text
    return out


def test_the_zip_holds_the_journal_accounts_and_registers(owner, books):
    files = read_zip(accounting.to_zip(accounting.build(owner, DEC, FEB)))
    assert set(files) == {"README.txt", *accounting.FILES}
    journal_rows = files["journal.csv"]
    assert journal_rows[0] == ["Journal no", "Date", "Account", "Debit", "Credit", "Description", "Name", "Property"]
    debits = sum(Decimal(r[3] or 0) for r in journal_rows[1:])
    credits = sum(Decimal(r[4] or 0) for r in journal_rows[1:])
    assert debits == credits > 0
    total = files["accounts.csv"][-1]
    assert total[0] == "Total" and total[2] == total[3] and total[4] == "0.00"
    assert ["Accounts receivable", "Accounts receivable"] == files["accounts.csv"][1][:2]
    assert len(files["invoices.csv"]) == 5 and len(files["payments.csv"]) == 3
    assert files["expenses.csv"][1][4] == "Repairs and maintenance"
    assert "Payments to owners are included." in files["README.txt"]


def test_page_and_download(client, owner, org, books):
    login(client, owner)
    url = reverse("reports:accounting_export")
    page = client.get(url, {"from": "2025-12", "to": "2026-02"})
    assert page.status_code == 200
    assert b"Download ZIP" in page.content and b"Accounts receivable" in page.content
    response = client.get(url, {"from": "2025-12", "to": "2026-02", "download": "1"})
    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"
    assert "accounting-2025-12-to-2026-02.zip" in response["Content-Disposition"]
    assert "journal.csv" in read_zip(response.content)
    event = AuditEvent.objects.get(action="reports.accounting_export")
    assert event.changes["months"] == [None, "Dec 2025 – Feb 2026"]


def test_page_needs_export_and_hides_other_properties(client, org, books, acacia, baobab):
    viewer = add_member(org, "caretaker", properties=[acacia])
    login(client, viewer)
    assert client.get(reverse("reports:accounting_export")).status_code == 403
    member = add_member(org, "accountant", properties=[baobab])
    client.logout()
    login(client, member)
    url = reverse("reports:accounting_export")
    assert client.get(url, {"property": str(acacia.public_id)}).status_code == 404
    assert client.get(url, {"property": str(baobab.public_id)}).status_code == 200
