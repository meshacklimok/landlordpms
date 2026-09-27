"""Invoice generation, proration, voiding, opening balances and the ledger (doc 11 §8, §25; doc 14 A2–A3)."""

import datetime
import threading
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction

from accounts.tests.factories import add_member, make_org, make_property
from audit.models import AuditEvent
from billing import invoicing
from billing.models import ChargeType, Invoice, InvoiceLine, LedgerEntry
from billing.services import ensure_default_charge_types
from leases import services as lease_services
from properties import services as property_services
from tenants import services as tenant_services

pytestmark = pytest.mark.django_db

D = datetime.date
JAN, FEB, MAR, APR = D(2026, 1, 1), D(2026, 2, 1), D(2026, 3, 1), D(2026, 4, 1)


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def prop(owner):
    return make_property(owner.organization)


def make_lease(owner, prop, *, code="A1", start=JAN, rent=15000, **kw):
    unit = property_services.create_unit(owner, prop, code=code)
    tenant = tenant_services.create_tenant(owner, name=f"Tenant {code}", phone=f"07123456{len(code):02d}")
    lease = lease_services.create_lease(owner, unit=unit, tenants=[tenant], start_date=start, end_date=None,
                                        rent=rent, **kw)
    return lease_services.activate_lease(owner, lease)


def water(org):
    ensure_default_charge_types(org)
    return ChargeType.objects.get(organization=org, category=ChargeType.Category.WATER)


def bill(lease, month, today=None, actor=None):
    return invoicing.generate_lease_month(lease, month, today=today or month - datetime.timedelta(days=5),
                                          actor=actor)


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def test_full_month_is_billed_in_advance_with_due_and_overdue_dates(owner, prop):
    lease = make_lease(owner, prop, due_day=5, grace_days=3)
    invoice = bill(lease, FEB, today=D(2026, 1, 27))
    assert invoice.status == Invoice.Status.ISSUED
    assert invoice.number == "INV-2026-000001"
    assert (invoice.period_start, invoice.period_end) == (FEB, D(2026, 2, 28))
    assert (invoice.issue_date, invoice.due_date, invoice.overdue_after) == (D(2026, 1, 27), D(2026, 2, 5),
                                                                              D(2026, 2, 8))
    [line] = invoice.lines.all()
    assert (line.amount, line.description, line.billing_month) == (Decimal("15000.00"), "Rent February 2026", FEB)
    assert invoice.total == invoice.subtotal == Decimal("15000.00") and invoice.tax_total == 0
    entry = LedgerEntry.objects.get(invoice=invoice)
    assert (entry.kind, entry.amount, entry.entry_date) == (LedgerEntry.Kind.INVOICE, Decimal("15000.00"),
                                                            D(2026, 1, 27))
    assert invoicing.lease_balance(lease) == Decimal("15000.00")
    assert not invoice.is_overdue(D(2026, 2, 8)) and invoice.is_overdue(D(2026, 2, 9))
    assert AuditEvent.objects.filter(action="invoice.issue", object_id=str(invoice.public_id)).exists()


def test_generation_is_idempotent(owner, prop):
    lease = make_lease(owner, prop)
    assert bill(lease, FEB) is not None
    assert bill(lease, FEB) is None
    assert invoicing.generate_month(owner.organization, FEB, actor=owner).invoices == []
    assert InvoiceLine.objects.filter(lease=lease).count() == 1
    assert invoicing.lease_balance(lease) == Decimal("15000.00")


def test_the_database_refuses_a_second_line_for_the_same_month(owner, prop):
    lease = make_lease(owner, prop)
    invoice = bill(lease, FEB)
    line = invoice.lines.get()
    with pytest.raises(IntegrityError), transaction.atomic():
        InvoiceLine.objects.create(organization=line.organization, invoice=invoice, lease=lease,
                                   charge_type=line.charge_type, description="again", unit_price=1, amount=1,
                                   billing_month=FEB)


def test_move_in_mid_month_is_prorated_by_days(owner, prop):
    lease = make_lease(owner, prop, start=D(2026, 1, 20))
    invoice = bill(lease, JAN, today=D(2026, 1, 20))
    [line] = invoice.lines.all()
    # 12 of 31 days: 15000 × 12 ÷ 31 = 5806.451…
    assert line.amount == Decimal("5806.45")
    assert (line.service_start, line.service_end) == (D(2026, 1, 20), D(2026, 1, 31))
    assert "20–31 Jan" in line.description
    # Due on the move-in day, not the 1st, which has passed.
    assert invoice.due_date == D(2026, 1, 20)


def test_leap_february_is_prorated_over_29_days(owner, prop):
    lease = make_lease(owner, prop, start=D(2028, 2, 20), rent=29000)
    [line] = bill(lease, D(2028, 2, 1), today=D(2028, 2, 20)).lines.all()
    assert line.amount == Decimal("10000.00")


def test_full_month_billing_when_the_organization_does_not_prorate(owner, prop):
    org = owner.organization
    org.prorate_partial_months = False
    org.save()
    lease = make_lease(owner, prop, start=D(2026, 1, 20))
    [line] = bill(lease, JAN, today=D(2026, 1, 20)).lines.all()
    assert line.amount == Decimal("15000.00") and line.description == "Rent January 2026"


def test_a_mid_month_rent_change_makes_one_line_rounded_once(owner, prop):
    lease = make_lease(owner, prop)
    lease_services.add_rent_change(owner, lease, effective_from=D(2026, 3, 15), amount=17000)
    [line] = bill(lease, MAR).lines.all()
    # (14 × 15000 + 17 × 17000) ÷ 31 = 16096.774…
    assert line.amount == Decimal("16096.77")
    assert bill(lease, APR).lines.get().amount == Decimal("17000.00")


def test_recurring_charges_are_billed_and_a_later_charge_gets_its_own_invoice(owner, prop):
    lease = make_lease(owner, prop)
    lease_services.add_charge(owner, lease, charge_type=water(owner.organization), amount=500, active_from=JAN)
    first = bill(lease, FEB)
    assert sorted(ln.amount for ln in first.lines.all()) == [Decimal("500.00"), Decimal("15000.00")]
    assert first.total == Decimal("15500.00")
    garbage = ChargeType.objects.get(organization=owner.organization, category=ChargeType.Category.GARBAGE)
    lease_services.add_charge(owner, lease, charge_type=garbage, amount=300, active_from=D(2026, 2, 15))
    extra = bill(lease, FEB, today=D(2026, 2, 15))
    [line] = extra.lines.all()
    assert line.charge_type == garbage and line.amount == Decimal("150.00")  # 14 of 28 days
    assert extra.number == "INV-2026-000002" and extra.due_date == D(2026, 2, 15)
    assert invoicing.lease_balance(lease) == Decimal("15650.00")


def test_invoice_total_is_the_sum_of_rounded_lines(owner, prop):
    lease = make_lease(owner, prop, start=D(2026, 1, 11), rent=10000)
    lease_services.add_charge(owner, lease, charge_type=water(owner.organization), amount=333,
                              active_from=D(2026, 1, 11))
    invoice = bill(lease, JAN, today=D(2026, 1, 11))
    amounts = [ln.amount for ln in invoice.lines.all()]
    # 21/31 of each: 6774.1935… and 225.5806…
    assert sorted(amounts) == [Decimal("225.58"), Decimal("6774.19")]
    assert invoice.total == sum(amounts) == Decimal("6999.77")


def test_the_last_month_stops_on_the_day_the_lease_ended(owner, prop):
    lease = make_lease(owner, prop)
    lease_services.end_lease(owner, lease, ended_on=D(2026, 3, 10), reason="Moved out")
    [line] = bill(lease, MAR).lines.all()
    assert line.amount == Decimal("4838.71")  # 10 of 31 days
    assert bill(lease, APR) is None


def test_draft_leases_are_not_billed(owner, prop):
    unit = property_services.create_unit(owner, prop, code="D1")
    tenant = tenant_services.create_tenant(owner, name="Draft", phone="0711000000")
    lease = lease_services.create_lease(owner, unit=unit, tenants=[tenant], start_date=JAN, end_date=None,
                                        rent=1000)
    assert bill(lease, FEB) is None


def test_generate_month_bills_only_the_properties_the_member_can_bill(owner, prop):
    org = owner.organization
    other = make_property(org)
    make_lease(owner, prop, code="A1")
    make_lease(owner, other, code="B1")
    scoped = add_member(org, "manager", properties=[prop])
    result = invoicing.generate_month(org, FEB, actor=scoped, today=D(2026, 1, 27))
    assert [i.lease.unit.property for i in result.invoices] == [prop]
    assert result.total == Decimal("15000.00")
    assert len(invoicing.generate_month(org, FEB, actor=owner, today=D(2026, 1, 27)).invoices) == 1


def test_generate_month_needs_the_capability_and_the_same_organization(owner, prop):
    org = owner.organization
    with pytest.raises(PermissionDenied):
        invoicing.generate_month(org, FEB, actor=add_member(org, "caretaker", all_properties=True))
    with pytest.raises(PermissionDenied):
        invoicing.generate_month(org, FEB, actor=make_org())


def test_months_to_bill_opens_next_month_within_the_lead_days(owner):
    org = owner.organization
    assert invoicing.months_to_bill(org, D(2026, 1, 26)) == [JAN]
    assert invoicing.months_to_bill(org, D(2026, 1, 27)) == [JAN, FEB]
    org.invoice_lead_days = 0
    assert invoicing.months_to_bill(org, D(2026, 1, 31)) == [JAN]


def test_the_scheduled_run_bills_every_organization_once(owner, prop):
    lease = make_lease(owner, prop)
    other = make_org()
    make_lease(other, make_property(other.organization))
    counts = invoicing.run_scheduled(today=D(2026, 1, 28))
    assert counts["invoices"] == 4  # January and February for each lease
    assert invoicing.run_scheduled(today=D(2026, 1, 29))["invoices"] == 0
    assert Invoice.objects.filter(lease=lease).count() == 2


# ---------------------------------------------------------------------------
# Voiding
# ---------------------------------------------------------------------------


def test_void_credits_the_ledger_and_frees_the_month(owner, prop):
    lease = make_lease(owner, prop)
    invoice = bill(lease, FEB)
    with pytest.raises(ValidationError):
        invoicing.void_invoice(owner, invoice, reason=" ")
    invoicing.void_invoice(owner, invoice, reason="Wrong rent")
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.VOID and invoice.number == "INV-2026-000001"
    assert invoice.outstanding == 0
    assert invoicing.lease_balance(lease) == 0
    assert LedgerEntry.objects.get(invoice=invoice, kind=LedgerEntry.Kind.INVOICE_VOID).amount == Decimal("-15000")
    again = bill(lease, FEB)
    assert again.number == "INV-2026-000002"
    with pytest.raises(ValidationError):
        invoicing.void_invoice(owner, invoice, reason="twice")


def test_void_needs_the_capability_scope_and_organization(owner, prop):
    lease = make_lease(owner, prop)
    invoice = bill(lease, FEB)
    org = owner.organization
    accountant = add_member(org, "accountant", all_properties=True)
    assert not invoicing.can_void(accountant, invoice)
    with pytest.raises(PermissionDenied):
        invoicing.void_invoice(accountant, invoice, reason="x")
    with pytest.raises(PermissionDenied):
        invoicing.void_invoice(add_member(org, "manager", properties=[make_property(org)]), invoice, reason="x")
    with pytest.raises(PermissionDenied):
        invoicing.void_invoice(make_org(), invoice, reason="x")
    assert invoicing.can_void(owner, invoice)


# ---------------------------------------------------------------------------
# Opening balances
# ---------------------------------------------------------------------------


def test_opening_balance_is_changed_by_reversal_never_by_edit(owner, prop):
    lease = make_lease(owner, prop)
    first = invoicing.set_opening_balance(owner, lease, amount="5,000", as_of=JAN, reason="Notebook")
    assert invoicing.lease_balance(lease) == Decimal("5000.00")
    second = invoicing.set_opening_balance(owner, lease, amount="-2000", as_of=JAN)
    assert invoicing.opening_balance(lease) == second and second.amount == Decimal("-2000.00")
    assert LedgerEntry.objects.get(reversal_of=first).amount == Decimal("-5000.00")
    assert invoicing.lease_balance(lease) == Decimal("-2000.00")
    assert invoicing.set_opening_balance(owner, lease, amount="0", as_of=JAN) is None
    assert invoicing.opening_balance(lease) is None and invoicing.lease_balance(lease) == 0
    assert AuditEvent.objects.filter(action="lease.opening_balance").count() == 3


def test_opening_balance_needs_invoices_adjust(owner, prop):
    lease = make_lease(owner, prop)
    with pytest.raises(PermissionDenied):
        invoicing.set_opening_balance(add_member(owner.organization, "accountant", all_properties=True), lease,
                                      amount=1, as_of=JAN)
    with pytest.raises(PermissionDenied):
        invoicing.set_opening_balance(make_org(), lease, amount=1, as_of=JAN)


# ---------------------------------------------------------------------------
# The ledger cannot be edited
# ---------------------------------------------------------------------------


def test_ledger_rows_are_append_only_and_signed_by_kind(owner, prop):
    lease = make_lease(owner, prop)
    entry = LedgerEntry.objects.get(invoice=bill(lease, FEB))
    entry.reason = "edited"
    with pytest.raises(ValueError):
        entry.save()
    with pytest.raises(ValueError):
        entry.delete()
    with pytest.raises(IntegrityError), transaction.atomic():
        LedgerEntry.objects.create(organization=lease.organization, lease=lease, entry_date=FEB,
                                   kind=LedgerEntry.Kind.PAYMENT, amount=Decimal("100"))
    with pytest.raises(IntegrityError), transaction.atomic():
        LedgerEntry.objects.create(organization=lease.organization, lease=lease, entry_date=FEB,
                                   kind=LedgerEntry.Kind.REVERSAL, amount=Decimal("100"))


# ---------------------------------------------------------------------------
# Concurrency (doc 11 §24): parallel issues never share a number
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_fifty_parallel_invoice_issues_get_distinct_numbers():
    owner = make_org()
    prop = make_property(owner.organization)
    leases = [make_lease(owner, prop, code=f"U{i}") for i in range(50)]
    numbers, errors = [], []
    barrier = threading.Barrier(len(leases))

    def issue(lease):
        try:
            barrier.wait()
            numbers.append(invoicing.generate_lease_month(lease, FEB, today=D(2026, 1, 27)).number)
        except Exception as e:  # noqa: BLE001 - reported below
            errors.append(e)
        finally:
            connection.close()

    threads = [threading.Thread(target=issue, args=(lease,)) for lease in leases]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert sorted(numbers) == [f"INV-2026-{n:06d}" for n in range(1, 51)]
