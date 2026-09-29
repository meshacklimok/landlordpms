"""Metric definitions and the dashboard (doc 11 §26, D-051), checked against hand-worked figures."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied

from accounts.tests.factories import add_member, make_org, make_property
from billing import invoicing
from leases.models import Lease
from payments import services as payment_services
from properties.models import Unit
from reports import metrics
from reports.tests.test_income import bill, make_lease, pay

pytestmark = pytest.mark.django_db

D = datetime.date
JAN, FEB, MAR = D(2025, 1, 1), D(2025, 2, 1), D(2025, 3, 1)


def money(value):
    return Decimal(value).quantize(Decimal("0.01"))


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def prop(owner):
    return make_property(owner.organization, name="Acacia Court")


def month(rows, day):
    return next(r for r in rows if r.month == day)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_month_arithmetic():
    assert metrics.add_months(D(2025, 11, 1), 3) == D(2026, 2, 1)
    assert metrics.add_months(JAN, -1) == D(2024, 12, 1)
    assert metrics.month_end(D(2024, 2, 1)) == D(2024, 2, 29)
    assert metrics.rate(0, 0) is None and metrics.rate(1, 4) == Decimal("0.25")


# ---------------------------------------------------------------------------
# Occupancy
# ---------------------------------------------------------------------------


def test_occupancy_counts_rentable_units_only(owner, prop):
    make_lease(owner, prop, code="A1", start=JAN)
    make_lease(owner, prop, code="A2", start=D(2025, 6, 1))  # not moved in yet in March
    reserved = make_lease(owner, prop, code="A3", start=D(2025, 6, 1)).unit
    Unit.objects.filter(pk=reserved.pk).update(manual_status=Unit.ManualStatus.RESERVED)
    inactive = make_lease(owner, prop, code="A4", start=D(2025, 6, 1)).unit
    Unit.objects.filter(pk=inactive.pk).update(manual_status=Unit.ManualStatus.INACTIVE)
    occ = metrics.occupancy(metrics.scope(owner), MAR)
    assert (occ.rentable, occ.occupied, occ.vacant, occ.reserved) == (3, 1, 2, 1)
    assert occ.rate == Decimal(1) / Decimal(3)
    assert metrics.occupancy(metrics.scope(owner), D(2025, 6, 1)).occupied == 3


def test_an_ended_lease_stops_occupying(owner, prop):
    lease = make_lease(owner, prop, start=JAN)
    Lease.all_objects.filter(pk=lease.pk).update(status=Lease.Status.ENDED, ended_on=D(2025, 2, 28))
    sc = metrics.scope(owner)
    assert metrics.occupancy(sc, D(2025, 2, 28)).occupied == 1
    assert metrics.occupancy(sc, MAR).occupied == 0


# ---------------------------------------------------------------------------
# Expected, collected, cash
# ---------------------------------------------------------------------------


def test_collected_takes_the_rent_share_of_each_payment(owner, prop):
    lease = make_lease(owner, prop, water=1000)
    bill(lease, JAN, FEB)
    pay(owner, lease, 10500, D(2025, 1, 10))  # half of Jan's 21,000: rent 10,000, water 500
    rows = metrics.months(metrics.scope(owner), JAN, MAR)
    jan, feb, mar = rows
    assert (jan.expected, jan.collected, jan.cash, jan.rate) == (money(20000), money(10000), money(10500),
                                                                 Decimal("0.5"))
    assert (feb.expected, feb.collected, feb.rate) == (money(20000), money(0), Decimal(0))
    assert (mar.expected, mar.rate) == (money(0), None)  # nothing billed is not 0%


def test_cash_is_by_payment_date_and_collected_by_billed_month(owner, prop):
    lease = make_lease(owner, prop)
    bill(lease, JAN)
    pay(owner, lease, 20000, D(2025, 2, 3))  # Jan's rent, paid late in Feb
    jan, feb = metrics.months(metrics.scope(owner), JAN, FEB)
    assert (jan.collected, jan.cash) == (money(20000), money(0))
    assert (feb.collected, feb.cash) == (money(0), money(20000))


def test_a_quarterly_invoice_counts_in_its_three_months(owner, prop):
    lease = make_lease(owner, prop, rent=10000, billing_frequency=Lease.Frequency.QUARTERLY)
    bill(lease, JAN)
    pay(owner, lease, 15000, D(2025, 1, 10))
    rows = metrics.months(metrics.scope(owner), JAN, D(2025, 4, 1))
    assert [(r.expected, r.collected) for r in rows] == [(money(10000), money(5000))] * 3 + [(money(0), money(0))]


def test_reversed_payments_and_void_invoices_are_left_out(owner, prop):
    lease = make_lease(owner, prop)
    bill(lease, JAN, FEB)
    payment = pay(owner, lease, 20000, D(2025, 1, 6))
    payment_services.reverse_payment(owner, payment, reason="Bounced")
    feb_invoice = lease.invoices.get(period_start=FEB)
    invoicing.void_invoice(owner, feb_invoice, reason="Billed by mistake")
    jan, feb = metrics.months(metrics.scope(owner), JAN, FEB)
    assert (jan.expected, jan.collected, jan.cash) == (money(20000), money(0), money(0))
    assert feb.expected == money(0)


def test_trend_is_twelve_months_ending_on_the_month(owner):
    rows = metrics.trend(metrics.scope(owner), D(2025, 3, 15))
    assert len(rows) == 12 and rows[0].month == D(2024, 4, 1) and rows[-1].month == MAR


# ---------------------------------------------------------------------------
# Arrears and scope
# ---------------------------------------------------------------------------


def test_arrears_by_days_overdue(owner, prop):
    lease = make_lease(owner, prop)  # due on the 5th
    bill(lease, JAN, FEB, MAR)
    arrears = metrics.arrears(metrics.scope(owner), D(2025, 3, 1))
    # Jan is 55 days past due, Feb 24; Mar is not due yet and is left out.
    assert [(key, amount) for key, _label, amount in arrears.buckets] == [
        ("d1_30", money(20000)), ("d31_60", money(20000)), ("d61_90", money(0)), ("d90", money(0))]
    assert (arrears.total, arrears.leases) == (money(40000), 1)


def test_a_scoped_member_sees_only_their_properties(owner, prop):
    other = make_property(owner.organization, name="Baobab Flats")
    a = make_lease(owner, prop, code="A1")
    b = make_lease(owner, other, code="B1", rent=30000)
    bill(a, JAN)
    bill(b, JAN)
    manager = add_member(owner.organization, "manager", properties=[prop])
    sc = metrics.scope(manager)
    assert sc.is_partial
    assert metrics.months(sc, JAN, JAN)[0].expected == money(20000)
    assert metrics.occupancy(sc, JAN).rentable == 1
    assert metrics.arrears(sc, MAR).total == money(20000)
    # Asking for a property they cannot see falls back to their own.
    assert metrics.scope(manager, other).selected_property is None
    # The owner can narrow to one property.
    only_b = metrics.scope(owner, other)
    assert metrics.months(only_b, JAN, JAN)[0].expected == money(30000)
    assert metrics.arrears(only_b, MAR).total == money(30000)
    assert not metrics.scope(owner).is_partial


def test_other_organizations_never_count(owner, prop):
    stranger = make_org()
    bill(make_lease(stranger, make_property(stranger.organization), code="Z1"), JAN)
    board = metrics.dashboard(owner, month=JAN, today=MAR)
    assert board.current.expected == 0 and board.occupancy.rentable == 0 and board.arrears.total == 0


def test_the_dashboard_needs_financial_figures(owner):
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    with pytest.raises(PermissionDenied):
        metrics.dashboard(caretaker)


def test_the_dashboard_uses_the_months_last_day_for_a_past_month(owner, prop):
    make_lease(owner, prop, start=D(2025, 2, 10))
    board = metrics.dashboard(owner, month=JAN, today=MAR)
    assert board.day == D(2025, 1, 31) and board.occupancy.occupied == 0
    assert metrics.dashboard(owner, month=MAR, today=D(2025, 3, 4)).day == D(2025, 3, 4)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def test_the_page_shows_the_five_numbers(client, owner, prop):
    lease = make_lease(owner, prop, water=1000)
    bill(lease, JAN)
    pay(owner, lease, 10500, D(2025, 1, 10))
    client.force_login(owner.user)
    page = client.get("/reports/?month=2025-01").content.decode()
    assert "Jan 2025" in page and "KES 20,000.00" in page and "KES 10,000.00" in page and "50%" in page
    assert "1 of 1 units" in page
    assert "date_from=2025-01-01" in page  # cash received drills down to the payments list
    assert 'id="dashboard-data"' in page and "chart.umd.min.js" in page


def test_the_page_says_when_nothing_was_billed(client, owner):
    client.force_login(owner.user)
    page = client.get("/reports/?month=nonsense").content.decode()
    assert "No rent billed yet" in page and "No units yet" in page


def test_the_csv_export(client, owner, prop):
    lease = make_lease(owner, prop)
    bill(lease, JAN)
    pay(owner, lease, 20000, D(2025, 1, 6))
    client.force_login(owner.user)
    response = client.get("/reports/dashboard.csv?month=2025-01")
    body = response.content.decode("utf-8-sig")
    assert response["Content-Disposition"] == 'attachment; filename="dashboard-2025-01.csv"'
    assert "2025-01,20000.00,20000.00,100.0,20000.00" in body
    assert "Acacia Court,1," in body


def test_pages_are_refused_without_the_capabilities(client, owner):
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    client.force_login(caretaker.user)
    assert client.get("/reports/").status_code == 403
    assert client.get("/reports/dashboard.csv").status_code == 403
    assert "Open the dashboard" not in client.get("/").content.decode()


def test_the_home_page_shows_collections(client, owner, prop):
    make_lease(owner, prop)
    client.force_login(owner.user)
    page = client.get("/").content.decode()
    assert "Open the dashboard" in page and "(1/1)" in page
