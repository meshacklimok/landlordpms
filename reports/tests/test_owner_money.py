"""What the landlord gets: fee on rent only, deposits held not paid out, expenses off, co-owners add back up (D-183)."""

import datetime
from decimal import Decimal

import pytest

from accounts.tests.factories import make_org, make_property
from billing import deposits
from expenses import services as expense_services
from payments import services as payment_services
from properties import services as property_services
from reports import statements
from reports.tests.test_income import bill, make_lease, pay

pytestmark = pytest.mark.django_db
D = datetime.date
JAN = D(2026, 10, 1)  # a month the property was already managed, so the fixed fee applies


def money(v):
    return Decimal(v).quantize(Decimal("0.01"))


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def jane(owner):
    return property_services.create_owner(owner, name="Jane Wanjiru", phone="+254712000111")


@pytest.fixture
def prop(owner, jane):
    prop = make_property(owner.organization, name="Acacia Court")
    return property_services.update_property(owner, prop, owner=jane, management_fee_percent=Decimal("10"),
                                             management_fee_fixed=Decimal("1500"))


def test_deposit_is_held_not_paid_out_and_utilities_carry_no_fee(owner, jane, prop):
    lease = make_lease(owner, prop, water=1000, deposit_amount=20000)
    deposits.record_received(owner, lease, amount=20000, entry_date=D(2026, 10, 1))
    bill(lease, JAN)
    total_billed = sum(i.total for i in lease.invoices.all())
    pay(owner, lease, total_billed, D(2026, 10, 6))
    category = expense_services.categories(owner.organization).get(name="Repairs and maintenance")
    expense_services.record_expense(owner, prop, category=category, description="Gate", amount="2500",
                                    paid_on=D(2026, 10, 12), method="BANK")
    st = statements.statement(owner, str(jane.public_id), JAN)
    assert deposits.held(lease) == money(20000) and st.deposit == 0  # held for the tenant, never the owner's income
    assert (st.rent, st.utilities) == (money(20000), money(1000))
    # Collected excludes the deposit; the fee is 10% of rent plus the fixed 1,500, not on water or deposit.
    assert st.collected == money(21000)
    assert st.fee == money(2000 + 1500)
    assert st.due == money(21000 - 3500 - 2500)
    # Everything the tenant paid is accounted for: owner + fee + expenses.
    paid = sum(p.amount for p in lease.payments.all())
    assert st.due + st.fee + st.expenses == paid


def test_a_reversed_payment_takes_back_the_owner_money_and_the_fee(owner, jane, prop):
    lease = make_lease(owner, prop)
    bill(lease, JAN)
    p = pay(owner, lease, 20000, D(2026, 10, 6))
    before = statements.statement(owner, str(jane.public_id), JAN)
    assert (before.collected, before.fee) == (money(20000), money(3500))
    payment_services.reverse_payment(owner, p, reason="Bounced")
    after = statements.statement(owner, str(jane.public_id), JAN)
    assert after.collected == 0 and after.fee == money(1500) and after.due == money(-1500)


def test_co_owners_shares_add_back_to_what_is_due(owner, jane, prop):
    peter = property_services.create_owner(owner, name="Peter", phone="+254712000222")
    mary = property_services.create_owner(owner, name="Mary", phone="+254712000333")
    property_services.set_shares(owner, prop, [(jane, Decimal("33.33")), (peter, Decimal("33.33")),
                                               (mary, Decimal("33.34"))])
    lease = make_lease(owner, prop, rent=10001)
    bill(lease, JAN)
    pay(owner, lease, 10001, D(2026, 10, 6))
    sts = [statements.statement(owner, str(o.public_id), JAN) for o in (jane, peter, mary)]
    whole = sts[0].whole_due
    assert all(s.whole_due == whole for s in sts)
    assert sum(s.due for s in sts) == whole, (whole, [s.due for s in sts])
