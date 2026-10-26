"""One month of rent, a fixed charge and metered water, followed through every screen that adds it up.

Whatever the statement, the invoice, the income pack, the profit and loss, the cash flow and the owner
statement each report has to agree on rent, utilities and other charges, and none of them may lose or double
a shilling (D-178).
"""

import datetime
from decimal import Decimal

import pytest

from billing import invoicing, selectors
from billing.models import ChargeType, Invoice, InvoiceLine, LedgerEntry
from billing.services import ensure_default_charge_types
from billing.tests.test_invoicing import bill, make_lease
from expenses import services as expense_services
from leases import services as lease_services
from meters import services as meter_services
from meters.tests.conftest import make_meter, read
from payments import services as payment_services
from reports import finance, income, statements

pytestmark = pytest.mark.django_db
D = datetime.date
JAN31, FEB = D(2026, 1, 31), D(2026, 2, 1)


def money(value):
    return Decimal(value).quantize(Decimal("0.01"))


@pytest.fixture
def owner():
    from accounts.tests.factories import make_org

    return make_org()


@pytest.fixture
def prop(owner):
    from accounts.tests.factories import make_property

    return make_property(owner.organization, name="Acacia Court")


@pytest.fixture
def february(owner, prop):
    """Rent 20,000, garbage 500 and 10 m³ of water at 100 billed in February; paid in two parts in February."""
    lease = make_lease(owner, prop, rent=20000)
    ensure_default_charge_types(owner.organization)
    garbage = ChargeType.objects.get(organization=owner.organization, category=ChargeType.Category.GARBAGE)
    lease_services.add_charge(owner, lease, charge_type=garbage, amount=500)
    meter = make_meter(owner, prop, [lease.unit], rate="100")
    meter_services.approve(owner, read(owner, meter, D(2025, 12, 31), 100), today=D(2025, 12, 31))
    meter_services.approve(owner, read(owner, meter, JAN31, 110), today=JAN31)
    invoice = bill(lease, FEB)
    payment_services.record_payment(owner, lease, amount=15000, method="CASH", paid_at=D(2026, 2, 3))
    payment_services.record_payment(owner, lease, amount=6500, method="CASH", paid_at=D(2026, 2, 10))
    return lease, invoice


def test_rent_water_and_fixed_charge_land_on_one_invoice_and_are_paid(owner, february):
    lease, invoice = february
    invoice.refresh_from_db()
    by_kind = {ChargeType.bucket(line.charge_type.category): line.amount for line in invoice.lines.all()}
    assert by_kind == {"rent": money(20000), "utilities": money(1000), "other": money(500)}
    assert invoice.total == money(21500) and invoice.status == Invoice.Status.PAID
    assert invoicing.lease_balance(lease) == 0
    # The ledger and the invoice agree, and every payment is fully allocated to it.
    assert sum(e.amount for e in LedgerEntry.objects.filter(invoice=invoice)) == invoice.total
    assert sum(a.amount for p in lease.payments.all() for a in p.allocations.all()) == invoice.amount_paid


def test_the_tenant_statement_shows_each_charge_and_adds_up(owner, february):
    lease, _invoice = february
    st = selectors.statement(lease)
    assert st["charged"] == {"rent": money(20000), "utilities": money(1000), "deposit": 0, "other": money(500)}
    assert (st["credits"], st["closing"], st["other_debits"]) == (money(21500), 0, 0)
    (invoice_row,) = [r for r in st["rows"] if r.entry.kind == LedgerEntry.Kind.INVOICE]
    assert sorted(line.amount for line in invoice_row.lines) == [money(500), money(1000), money(20000)]
    assert st["brought_forward"] + st["debits"] - st["credits"] == st["closing"]


def test_every_report_splits_the_money_the_same_way(owner, prop, february):
    lease, invoice = february
    received = sum(p.amount for p in lease.payments.all())
    assert received == money(21500)

    pack = income.income_pack(owner, 2026, receipts=True)
    total = pack.total
    assert (total.rent_billed, total.utilities_billed, total.other_billed) == (
        money(20000), money(1000), money(500))
    assert total.rent_received + total.utilities_received + total.other_received == received
    assert (total.rent_received, total.utilities_received, total.other_received) == (
        money(20000), money(1000), money(500))
    assert total.unapplied == 0 and total.deposit_received == 0

    report = finance.money_report(owner, FEB, FEB)
    assert (report.total.rent, report.total.utilities, report.total.other) == (
        total.rent_received, total.utilities_received, total.other_received)
    assert report.total.income == received and report.total.money_in == received

    st = statements.statement(owner, statements.NO_OWNER, FEB)
    assert (st.rent, st.utilities, st.other, st.collected) == (money(20000), money(1000), money(500), received)
    assert st.billed == invoice.total and st.balance == 0
    # Every payment's own split adds back to the payment.
    for part in pack.receipts:
        assert part.rent + part.utilities + part.other + part.deposit + part.unapplied == part.amount


def test_expenses_come_off_the_net_but_not_the_income(owner, prop, february):
    category = expense_services.categories(owner.organization).get(name="Repairs and maintenance")
    expense_services.record_expense(owner, prop, category=category, description="Fixed the gate", amount="2500",
                                    paid_on=D(2026, 2, 12), method="BANK")
    report = finance.money_report(owner, FEB, FEB)
    assert (report.total.income, report.total.expenses, report.total.noi) == (
        money(21500), money(2500), money(19000))
    st = statements.statement(owner, statements.NO_OWNER, FEB)
    assert (st.collected, st.expenses, st.whole_due) == (money(21500), money(2500), money(19000))


def test_a_reversed_payment_comes_out_of_every_report_and_the_balance(owner, prop, february):
    lease, invoice = february
    last = lease.payments.order_by("pk").last()
    payment_services.reverse_payment(owner, last, reason="Bounced")
    invoice.refresh_from_db()
    assert (invoice.amount_paid, invoice.status) == (money(15000), Invoice.Status.PARTIALLY_PAID)
    assert invoicing.lease_balance(lease) == money(6500)
    assert selectors.statement(lease)["closing"] == money(6500)
    assert finance.money_report(owner, FEB, FEB).total.income == money(15000)
    assert income.income_pack(owner, 2026).total.received == money(15000)
    st = statements.statement(owner, statements.NO_OWNER, FEB)
    # The month-end balance is as it stood on 28 Feb; the reversal is posted today, so it shows in the account.
    assert st.collected == money(15000)


def test_no_charge_is_billed_twice_when_billing_runs_again(owner, february):
    lease, invoice = february
    assert bill(lease, FEB) is None
    invoicing.bill_meter_charges(owner.organization, today=D(2026, 2, 5))
    assert InvoiceLine.objects.filter(lease=lease, is_void=False).count() == 3
    assert Invoice.objects.filter(lease=lease).count() == 1
