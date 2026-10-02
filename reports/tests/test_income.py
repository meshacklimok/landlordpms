"""The annual rental income pack and tax estimate (D-050)."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied

from accounts.models import Organization
from accounts.tests.factories import add_member, make_org, make_property
from audit.models import AuditEvent
from billing import invoicing
from billing.models import ChargeType
from billing.services import ensure_default_charge_types
from leases import services as lease_services
from leases.models import Lease
from payments import services as payment_services
from properties import services as property_services
from reports import income
from tenants import services as tenant_services

pytestmark = pytest.mark.django_db

D = datetime.date
NON_RESIDENT = Organization.TaxResidence.NON_RESIDENT
JAN_2025 = D(2025, 1, 1)


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def prop(owner):
    return make_property(owner.organization, name="Acacia Court")


def make_lease(owner, prop, *, code="A1", start=JAN_2025, rent=20000, water=None, **kw):
    unit = property_services.create_unit(owner, prop, code=code)
    tenant = tenant_services.create_tenant(owner, name=f"Tenant {code}", phone=f"0712{sum(map(ord, code)):06d}")
    lease = lease_services.create_lease(owner, unit=unit, tenants=[tenant], start_date=start, end_date=None,
                                        rent=rent, due_day=5, grace_days=3, **kw)
    lease = lease_services.activate_lease(owner, lease)
    if water:
        ensure_default_charge_types(owner.organization)
        charge = ChargeType.objects.get(organization=owner.organization, category=ChargeType.Category.WATER)
        lease_services.add_charge(owner, lease, charge_type=charge, amount=water)
    return lease


def bill(lease, *months):
    for month in months:
        invoicing.generate_lease_period(lease, month, today=month - datetime.timedelta(days=5))


def pay(owner, lease, amount, day):
    return payment_services.record_payment(owner, lease, amount=amount, method="CASH", paid_at=day)


@pytest.fixture
def year_2025(owner, prop):
    """Rent 20,000 and water 1,000 billed Jan to Mar; 96,000 paid, 33,000 of it beyond the invoices."""
    lease = make_lease(owner, prop, water=1000)
    bill(lease, D(2025, 1, 1), D(2025, 2, 1), D(2025, 3, 1))
    pay(owner, lease, 21000, D(2025, 1, 10))
    pay(owner, lease, 25000, D(2025, 2, 10))  # Feb in full, then 4,000 towards Mar
    pay(owner, lease, 50000, D(2025, 3, 5))  # the rest of Mar, then 33,000 not yet applied
    return lease


def money(value):
    return Decimal(value).quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# Rates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("residence", "day", "rate"), [
    ("RESIDENT", D(2023, 12, 1), "0.10"),
    ("RESIDENT", D(2024, 1, 1), "0.075"),
    ("NON_RESIDENT", D(2026, 6, 1), None),
    ("NON_RESIDENT", D(2026, 7, 1), "0.10"),
])
def test_rates_are_dated(residence, day, rate):
    assert income.rate_on(residence, day) == (Decimal(rate) if rate else None)


def test_rates_read_as_percentages():
    assert (income.percent(Decimal("0.075")), income.percent(Decimal("0.10")), income.percent(None)) == (
        "7.5%", "10%", "—")


# ---------------------------------------------------------------------------
# The pack
# ---------------------------------------------------------------------------


def test_billed_and_received_by_month(owner, year_2025):
    pack = income.income_pack(owner, 2025)
    jan, feb, mar = pack.months[:3]
    assert (jan.rent_billed, jan.other_billed) == (money(20000), money(1000))
    assert (jan.received, jan.rent_received, jan.other_received) == (money(21000), money(20000), money(1000))
    # 25,000 in Feb: Feb in full, and 4,000 of Mar split 20:1 between rent and water.
    assert (feb.rent_received, feb.other_received) == (money("23809.52"), money("1190.48"))
    assert (mar.rent_received, mar.unapplied, mar.taxable) == (money("16190.48"), money(33000), money("49190.48"))
    t = pack.total
    assert (t.rent_billed, t.other_billed, t.received) == (money(60000), money(3000), money(96000))
    assert (t.rent_received, t.other_received, t.unapplied) == (money(60000), money(3000), money(33000))


def test_the_resident_estimate_is_monthly_rent_times_the_rate(owner, year_2025):
    pack = income.income_pack(owner, 2025)
    assert [row.tax for row in pack.months[:4]] == [money(1500), money("1785.71"), money("3689.29"), money(0)]
    assert pack.total.tax == money(6975)
    assert pack.rates == [("Jan – Dec", "7.5%")]
    assert any("outside" in note for note in pack.notes)  # 93,000 is under the band
    assert any("counted as rent" in note for note in pack.notes)


def test_a_reversed_payment_is_left_out(owner, year_2025):
    last = year_2025.payments.order_by("pk").last()
    payment_services.reverse_payment(owner, last, reason="Bounced")
    pack = income.income_pack(owner, 2025)
    assert pack.total.received == money(46000) and pack.total.unapplied == 0
    assert len(pack.receipts) == 2


def test_the_non_resident_rate_starts_in_july_2026(owner, prop):
    owner.organization.landlord_tax_residence = NON_RESIDENT
    owner.organization.save()
    lease = make_lease(owner, prop, start=D(2026, 6, 1))
    bill(lease, D(2026, 6, 1), D(2026, 7, 1))
    pay(owner, lease, 20000, D(2026, 6, 5))
    pay(owner, lease, 20000, D(2026, 7, 5))
    pack = income.income_pack(owner, 2026)
    assert (pack.months[5].tax, pack.months[6].tax) == (None, money(2000))
    assert pack.total.tax == money(2000)
    assert pack.rates == [("Jan – Jun", "—"), ("Jul – Dec", "10%")]
    assert any("not over yet" in note for note in pack.notes)
    assert any("no rate" in note for note in pack.notes)


def test_period_invoices_count_in_the_months_they_bill(owner, prop):
    lease = make_lease(owner, prop, rent=15000, billing_frequency=Lease.Frequency.QUARTERLY)
    bill(lease, D(2025, 1, 1))
    pack = income.income_pack(owner, 2025)
    assert [row.rent_billed for row in pack.months[:4]] == [money(15000)] * 3 + [money(0)]


def test_by_property_and_scoped_to_the_member(owner, prop):
    other = make_property(owner.organization, name="Baobab Flats")
    bill(make_lease(owner, prop), D(2025, 1, 1))
    pay(owner, prop.units.get().leases.get(), 20000, D(2025, 1, 6))
    b = make_lease(owner, other, code="B1", rent=30000)
    bill(b, D(2025, 1, 1))
    pay(owner, b, 30000, D(2025, 1, 7))
    assert [(r.label, r.received) for r in income.income_pack(owner, 2025).properties] == [
        ("Acacia Court", money(20000)), ("Baobab Flats", money(30000))]
    accountant = add_member(owner.organization, "accountant", properties=[prop])
    pack = income.income_pack(accountant, 2025)
    assert [r.label for r in pack.properties] == ["Acacia Court"] and pack.total.received == money(20000)
    assert any("you can see" in note for note in pack.notes)


def test_the_pack_needs_financial_reports(owner):
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    with pytest.raises(PermissionDenied):
        income.income_pack(caretaker, 2025)


def test_setting_the_tax_residence_is_audited_and_owner_only(owner):
    income.set_tax_residence(owner, NON_RESIDENT)
    owner.organization.refresh_from_db()
    assert owner.organization.landlord_tax_residence == NON_RESIDENT
    assert AuditEvent.objects.filter(action="organization.tax_residence").count() == 1
    accountant = add_member(owner.organization, "accountant", all_properties=True)
    with pytest.raises(PermissionDenied):
        income.set_tax_residence(accountant, "RESIDENT")


# ---------------------------------------------------------------------------
# Pages and exports
# ---------------------------------------------------------------------------


def test_the_page_shows_the_estimate(client, owner, year_2025):
    client.force_login(owner.user)
    page = client.get("/reports/income/?year=2025").content.decode()
    assert "Rental income 2025" in page and "KES 6,975.00" in page and "7.5%" in page


def test_the_owner_changes_residence_from_the_page(client, owner):
    client.force_login(owner.user)
    response = client.post("/reports/income/tax-residence/", {"landlord_tax_residence": NON_RESIDENT, "year": "2026"})
    assert response.status_code == 302 and response.url == "/reports/income/?year=2026"
    owner.organization.refresh_from_db()
    assert owner.organization.landlord_tax_residence == NON_RESIDENT


def test_csv_and_pdf_exports(client, owner, year_2025):
    client.force_login(owner.user)
    months = client.get("/reports/income/2025/months/").content.decode("utf-8-sig")
    assert "2025-03,60000.00" not in months  # one row per month, not a running total
    assert "Total,60000.00,3000.00,96000.00,60000.00,3000.00,0.00,33000.00,93000.00,,6975.00" in months
    assert "Confirm with your tax adviser" in months
    receipts = client.get("/reports/income/2025/receipts/").content.decode("utf-8-sig")
    assert receipts.count("\n2025-0") == 3
    pdf = client.get("/reports/income/2025/pdf/")
    assert pdf["Content-Type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    assert client.get("/reports/income/1999/months/").status_code == 404
    assert client.get("/reports/income/2025/nonsense/").status_code == 404


def test_pages_are_refused_without_the_capabilities(client, owner):
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    client.force_login(caretaker.user)
    assert client.get("/reports/income/").status_code == 403
    assert client.get("/reports/income/2026/pdf/").status_code == 403
    assert client.post("/reports/income/tax-residence/", {"landlord_tax_residence": NON_RESIDENT}).status_code == 403
