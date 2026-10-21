"""Quarterly and yearly billing (D-049): one invoice per period, counted from the lease's start month."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from accounts.tests.factories import make_org, make_property
from billing import invoicing
from billing.models import ChargeType, Invoice, InvoiceLine
from billing.services import ensure_default_charge_types
from leases import services as lease_services
from leases.models import Lease
from properties import services as property_services
from tenants import services as tenant_services

pytestmark = pytest.mark.django_db

D = datetime.date
JAN, FEB, MAR, APR, MAY, JUN, JUL = (D(2026, m, 1) for m in range(1, 8))
QUARTERLY, YEARLY = Lease.Frequency.QUARTERLY, Lease.Frequency.YEARLY
ISSUED, VOID = Invoice.Status.ISSUED, Invoice.Status.VOID


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def prop(owner):
    return make_property(owner.organization)


def make_lease(owner, prop, *, code="A1", start=JAN, rent=15000, frequency=QUARTERLY, **kw):
    unit = property_services.create_unit(owner, prop, code=code)
    tenant = tenant_services.create_tenant(owner, name=f"Tenant {code}", phone=f"07123456{len(code):02d}")
    lease = lease_services.create_lease(owner, unit=unit, tenants=[tenant], start_date=start, end_date=None,
                                        rent=rent, billing_frequency=frequency, due_day=5, grace_days=3, **kw)
    return lease_services.activate_lease(owner, lease)


def bill(lease, month, today=None):
    return invoicing.generate_lease_period(lease, month, today=today or month - datetime.timedelta(days=5))


def live(lease):
    return Invoice.objects.filter(lease=lease).exclude(status=VOID).order_by("period_start", "pk")


def rent_lines(invoice):
    return [(ln.billing_month, ln.amount) for ln in invoice.lines.filter(
        charge_type__category=ChargeType.Category.RENT).order_by("billing_month")]


# ---------------------------------------------------------------------------
# Periods
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("frequency", "month", "expected"), [
    (Lease.Frequency.MONTHLY, MAY, [MAY]),
    (QUARTERLY, FEB, [FEB, MAR, APR]),
    (QUARTERLY, APR, [FEB, MAR, APR]),
    (QUARTERLY, MAY, [MAY, JUN, JUL]),
    (QUARTERLY, JAN, [JAN]),  # before the lease: nothing to group
    (YEARLY, D(2027, 1, 20), [D(2026, m, 1) for m in range(2, 13)] + [JAN.replace(year=2027)]),
])
def test_periods_count_from_the_start_month(frequency, month, expected):
    lease = Lease(start_date=D(2026, 2, 15), billing_frequency=frequency)
    assert invoicing.period_months(lease, month) == expected


def test_a_period_crosses_the_year_end():
    lease = Lease(start_date=D(2026, 11, 3), billing_frequency=QUARTERLY)
    assert invoicing.period_months(lease, D(2027, 1, 9)) == [D(2026, 11, 1), D(2026, 12, 1), D(2027, 1, 1)]


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def test_a_quarter_is_one_invoice_with_a_line_per_month(owner, prop):
    lease = make_lease(owner, prop)
    invoice = bill(lease, JAN)
    assert rent_lines(invoice) == [(JAN, Decimal("15000.00")), (FEB, Decimal("15000.00")),
                                   (MAR, Decimal("15000.00"))]
    assert invoice.total == Decimal("45000.00")
    assert (invoice.period_start, invoice.period_end) == (JAN, D(2026, 3, 31))
    assert (invoice.due_date, invoice.overdue_after) == (D(2026, 1, 5), D(2026, 1, 8))
    assert invoice.period_label == "Jan – Mar 2026"
    # Any month of the period finds it billed.
    assert bill(lease, FEB) is None and bill(lease, MAR) is None
    assert bill(lease, APR).period_label == "Apr – Jun 2026"


def test_a_partial_first_month_is_prorated_inside_the_first_quarter(owner, prop):
    lease = make_lease(owner, prop, start=D(2026, 1, 15), rent=31000)
    invoice = bill(lease, JAN)
    assert rent_lines(invoice) == [(JAN, Decimal("17000.00")), (FEB, Decimal("31000.00")),
                                   (MAR, Decimal("31000.00"))]
    assert invoice.due_date == D(2026, 1, 15)  # never before the tenant moves in


def test_a_year_is_one_invoice(owner, prop):
    lease = make_lease(owner, prop, frequency=YEARLY, rent=10000)
    invoice = bill(lease, MAR)
    assert invoice.lines.count() == 12 and invoice.total == Decimal("120000.00")
    assert invoice.period_label == "Jan – Dec 2026"


def test_recurring_charges_go_on_the_period_invoice(owner, prop):
    lease = make_lease(owner, prop)
    ensure_default_charge_types(owner.organization)
    water = ChargeType.objects.get(organization=owner.organization, category=ChargeType.Category.WATER)
    lease_services.add_charge(owner, lease, charge_type=water, amount=500)
    invoice = bill(lease, JAN)
    assert invoice.lines.filter(charge_type=water).count() == 3
    assert invoice.total == Decimal("46500.00")


def test_the_daily_job_bills_the_next_quarter_within_the_lead_days(owner, prop):
    lease = make_lease(owner, prop)
    org = owner.organization
    invoicing.run_scheduled(D(2026, 1, 1))
    assert [i.period_label for i in live(lease)] == ["Jan – Mar 2026"]
    for day in (D(2026, 2, 1), D(2026, 2, 27), D(2026, 3, 26)):
        invoicing.run_scheduled(day)
    assert live(lease).count() == 1
    invoicing.run_scheduled(APR - datetime.timedelta(days=org.invoice_lead_days))
    assert [i.period_label for i in live(lease)] == ["Jan – Mar 2026", "Apr – Jun 2026"]
    assert live(lease).last().issue_date == D(2026, 3, 27)


def test_a_lease_activated_mid_quarter_is_billed_for_the_whole_quarter(owner, prop):
    lease = make_lease(owner, prop)
    invoicing.generate_month(owner.organization, FEB, today=D(2026, 2, 10))
    invoice = live(lease).get()
    assert invoice.period_label == "Jan – Mar 2026" and invoice.due_date == D(2026, 2, 10)


# ---------------------------------------------------------------------------
# Changes after billing
# ---------------------------------------------------------------------------


def test_ending_mid_quarter_bills_the_quarter_again_up_to_the_last_day(owner, prop):
    lease = make_lease(owner, prop, rent=28000)
    first = bill(lease, JAN)
    lease_services.end_lease(owner, lease, ended_on=D(2026, 2, 14), reason="Moved out")
    first.refresh_from_db()
    assert first.status == VOID
    again = live(lease).get()
    assert rent_lines(again) == [(JAN, Decimal("28000.00")), (FEB, Decimal("14000.00"))]
    assert again.due_date == first.due_date


def test_a_rent_change_mid_quarter_bills_the_quarter_again(owner, prop):
    lease = make_lease(owner, prop)
    first = bill(lease, JAN)
    lease_services.add_rent_change(owner, lease, effective_from=MAR, amount=18000)
    first.refresh_from_db()
    assert first.status == VOID
    assert rent_lines(live(lease).get()) == [(JAN, Decimal("15000.00")), (FEB, Decimal("15000.00")),
                                             (MAR, Decimal("18000.00"))]


def test_a_quarter_with_payments_is_kept_for_manual_correction(owner, prop):
    lease = make_lease(owner, prop)
    first = bill(lease, JAN)
    Invoice.objects.filter(pk=first.pk).update(amount_paid=100, status=Invoice.Status.PARTIALLY_PAID)
    lease_services.add_rent_change(owner, lease, effective_from=MAR, amount=18000)
    assert list(live(lease)) == [first]
    assert invoicing.rebill_from(lease, MAR, actor=owner, reason="check") == [first]


def test_a_charge_added_mid_quarter_is_billed_on_one_extra_invoice(owner, prop):
    lease = make_lease(owner, prop, rent=10000)
    bill(lease, JAN)
    ensure_default_charge_types(owner.organization)
    water = ChargeType.objects.get(organization=owner.organization, category=ChargeType.Category.WATER)
    lease_services.add_charge(owner, lease, charge_type=water, amount=2800, active_from=D(2026, 2, 15))
    extra = live(lease).last()
    assert [(ln.billing_month, ln.amount) for ln in extra.lines.order_by("billing_month")] == [
        (FEB, Decimal("1400.00")), (MAR, Decimal("2800.00"))]
    assert InvoiceLine.objects.filter(lease=lease, is_void=False).count() == 5


# ---------------------------------------------------------------------------
# Lease terms
# ---------------------------------------------------------------------------


def test_a_renewal_keeps_the_billing_frequency(owner, prop):
    lease = make_lease(owner, prop, frequency=YEARLY)
    draft = lease_services.renew_lease(owner, lease, start_date=D(2027, 1, 1))
    assert draft.billing_frequency == YEARLY


def test_an_unknown_frequency_is_refused(owner, prop):
    unit = property_services.create_unit(owner, prop, code="B1")
    tenant = tenant_services.create_tenant(owner, name="B", phone="0712000099")
    with pytest.raises(ValidationError):
        lease_services.create_lease(owner, unit=unit, tenants=[tenant], start_date=JAN, rent=1000,
                                    billing_frequency="WEEKLY")


def test_the_lease_page_shows_rent_per_invoice(client, owner, prop):
    lease = make_lease(owner, prop)
    client.force_login(owner.user)
    page = client.get(f"/leases/{lease.public_id}/").content.decode()
    assert "Quarterly" in page and "45,000.00 rent per invoice (3 months)" in page
