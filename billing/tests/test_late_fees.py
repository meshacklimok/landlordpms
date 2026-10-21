"""Late fees (D-129): the landlord's rule, the daily proposal, and staff charging or skipping each fee."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from accounts import services as account_services
from accounts.models import Organization
from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from audit.models import AuditEvent
from billing import invoicing, jobs, late_fees
from billing.models import ChargeType, Invoice, LateFee
from payments import services as payment_services
from reports import home

from .test_invoicing import JAN, bill, make_lease

pytestmark = pytest.mark.django_db

D = datetime.date
Mode = Organization.LateFeeMode
# Rent 15,000 due on the 5th with 3 days' grace: January's invoice is overdue from the 9th.
JAN_9 = D(2026, 1, 9)


def set_rule(org, mode, *, amount=None, percent=None, cap=None, since=JAN):
    Organization.objects.filter(pk=org.pk).update(late_fee_mode=mode, late_fee_amount=amount,
                                                  late_fee_percent=percent, late_fee_cap=cap, late_fees_from=since)
    org.refresh_from_db()


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def prop(owner):
    return make_property(owner.organization, name="Acacia Court")


@pytest.fixture
def lease(owner, prop):
    return make_lease(owner, prop, due_day=5, grace_days=3)


def pay(owner, lease, amount, day):
    return payment_services.record_payment(owner, lease, amount=amount, method="CASH", paid_at=day)


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


def test_fee_for_each_rule(owner):
    org = owner.organization
    assert late_fees.fee_for(org, Decimal("15000")) == 0  # off by default
    set_rule(org, Mode.FIXED, amount=Decimal("500"))
    assert late_fees.fee_for(org, Decimal("15000")) == Decimal("500.00")
    assert late_fees.fee_for(org, Decimal("0")) == 0
    set_rule(org, Mode.PERCENT, percent=Decimal("5"))
    assert late_fees.fee_for(org, Decimal("15000")) == Decimal("750.00")
    set_rule(org, Mode.PERCENT, percent=Decimal("5"), cap=Decimal("600"))
    assert late_fees.fee_for(org, Decimal("15000")) == Decimal("600.00")
    assert late_fees.fee_for(org, Decimal("1000")) == Decimal("50.00")
    assert late_fees.describe_rule(org) == "5% of what is unpaid, at most KES 600.00"


def test_switching_on_starts_the_clock_and_checks_the_rule(owner):
    with pytest.raises(ValidationError, match="amount"):
        account_services.update_organization(owner, late_fee_mode=Mode.FIXED, late_fee_amount=None,
                                             late_fee_percent=None, late_fee_cap=None)
    with pytest.raises(ValidationError, match="percentage"):
        account_services.update_organization(owner, late_fee_mode=Mode.PERCENT, late_fee_amount=Decimal("500"),
                                             late_fee_percent=None, late_fee_cap=None)
    account_services.update_organization(owner, late_fee_mode=Mode.PERCENT, late_fee_amount=Decimal("500"),
                                         late_fee_percent=Decimal("5"), late_fee_cap=Decimal("2000"))
    org = Organization.objects.get(pk=owner.organization.pk)
    assert (org.late_fee_mode, org.late_fee_amount, org.late_fee_percent, org.late_fee_cap) == (
        Mode.PERCENT, None, Decimal("5.00"), Decimal("2000.00"))
    assert org.late_fees_from == timezone.localdate()
    assert AuditEvent.objects.filter(action="organization.update", organization=org).exists()
    # Changing the rate later keeps the start; switching off clears the rule.
    Organization.objects.filter(pk=org.pk).update(late_fees_from=D(2026, 1, 1))
    owner.organization.refresh_from_db()
    account_services.update_organization(owner, late_fee_mode=Mode.PERCENT, late_fee_amount=None,
                                         late_fee_percent=Decimal("10"), late_fee_cap=None)
    assert Organization.objects.get(pk=org.pk).late_fees_from == D(2026, 1, 1)
    account_services.update_organization(owner, late_fee_mode=Mode.OFF, late_fee_amount=None,
                                         late_fee_percent=Decimal("10"), late_fee_cap=None)
    org.refresh_from_db()
    assert (org.late_fee_mode, org.late_fee_percent) == (Mode.OFF, None)


# ---------------------------------------------------------------------------
# The daily proposal
# ---------------------------------------------------------------------------


def test_one_fee_is_proposed_per_overdue_invoice(owner, lease):
    org = owner.organization
    set_rule(org, Mode.FIXED, amount=Decimal("500"))
    invoice = bill(lease, JAN)
    assert late_fees.propose(org, D(2026, 1, 8)) == 0  # still within the grace days
    assert late_fees.propose(org, JAN_9) == 1
    assert late_fees.propose(org, D(2026, 1, 10)) == 0  # never twice
    fee = LateFee.objects.get()
    assert (fee.invoice, fee.lease, fee.unpaid, fee.amount, fee.status) == (
        invoice, lease, Decimal("15000.00"), Decimal("500.00"), LateFee.Status.PENDING)
    # Nothing is charged until staff approve it.
    assert invoicing.lease_balance(lease) == Decimal("15000.00")


def test_old_arrears_paid_invoices_and_switched_off_get_none(owner, lease):
    org = owner.organization
    bill(lease, JAN)
    set_rule(org, Mode.FIXED, amount=Decimal("500"), since=D(2026, 1, 20))  # switched on after Jan went overdue
    assert late_fees.propose(org, D(2026, 1, 21)) == 0
    set_rule(org, Mode.OFF, since=D(2026, 1, 1))
    assert late_fees.propose(org, JAN_9) == 0
    set_rule(org, Mode.FIXED, amount=Decimal("500"))
    pay(owner, lease, 15000, D(2026, 1, 9))
    assert late_fees.propose(org, D(2026, 1, 10)) == 0


def test_a_late_fee_invoice_never_gets_a_fee_of_its_own(owner, lease):
    org = owner.organization
    set_rule(org, Mode.FIXED, amount=Decimal("500"))
    bill(lease, JAN)
    late_fees.propose(org, JAN_9)
    fee_invoice = late_fees.approve(owner, LateFee.objects.get(), today=JAN_9)
    assert late_fees.propose(org, D(2026, 2, 1)) == 0
    assert not LateFee.objects.filter(invoice=fee_invoice).exists()


def test_the_daily_job_proposes_and_clears_stale_fees(owner, lease):
    org = owner.organization
    set_rule(org, Mode.FIXED, amount=Decimal("500"))
    bill(lease, JAN)
    counts = jobs.run_daily(JAN_9)
    assert counts["late_fees_proposed"] == 1
    pay(owner, lease, 15000, D(2026, 1, 10))
    counts = jobs.run_daily(D(2026, 1, 11))
    fee = LateFee.objects.get()
    assert counts["late_fees_skipped"] == 1
    assert (fee.status, fee.reason, fee.decided_by) == (LateFee.Status.SKIPPED, late_fees.PAID_MEANWHILE, None)


def test_switching_off_skips_what_waits(owner, lease):
    org = owner.organization
    set_rule(org, Mode.FIXED, amount=Decimal("500"))
    bill(lease, JAN)
    late_fees.propose(org, JAN_9)
    set_rule(org, Mode.OFF)
    with pytest.raises(ValidationError, match="switched off"):
        late_fees.approve(owner, LateFee.objects.get(), today=JAN_9)
    late_fees.run_daily(D(2026, 1, 10))
    assert LateFee.objects.get().reason == late_fees.SWITCHED_OFF


# ---------------------------------------------------------------------------
# Charging and skipping
# ---------------------------------------------------------------------------


def test_charging_issues_an_invoice_worked_out_from_what_is_still_unpaid(owner, lease):
    org = owner.organization
    set_rule(org, Mode.PERCENT, percent=Decimal("10"))
    invoice = bill(lease, JAN)
    late_fees.propose(org, JAN_9)
    assert LateFee.objects.get().amount == Decimal("1500.00")
    pay(owner, lease, 10000, D(2026, 1, 10))  # 5,000 still unpaid
    fee_invoice = late_fees.approve(owner, LateFee.objects.get(), today=D(2026, 1, 12))
    fee = LateFee.objects.get()
    assert (fee.status, fee.unpaid, fee.amount, fee.fee_invoice) == (
        LateFee.Status.CHARGED, Decimal("5000.00"), Decimal("500.00"), fee_invoice)
    assert fee.decided_by == owner.user
    assert fee_invoice.status == Invoice.Status.ISSUED and fee_invoice.number
    assert (fee_invoice.total, fee_invoice.due_date, fee_invoice.overdue_after) == (
        Decimal("500.00"), D(2026, 1, 12), D(2026, 1, 15))
    line = fee_invoice.lines.get()
    assert line.charge_type.category == ChargeType.Category.LATE_FEE and line.billing_month is None
    assert invoice.number in line.description
    assert invoicing.lease_balance(lease) == Decimal("5500.00")
    assert AuditEvent.objects.filter(action="late_fee.charge").count() == 1
    with pytest.raises(ValidationError, match="already charged"):
        late_fees.approve(owner, fee, today=D(2026, 1, 12))


def test_a_fee_whose_invoice_was_paid_is_skipped_not_charged(owner, lease):
    org = owner.organization
    set_rule(org, Mode.FIXED, amount=Decimal("500"))
    bill(lease, JAN)
    late_fees.propose(org, JAN_9)
    pay(owner, lease, 15000, D(2026, 1, 10))
    assert late_fees.approve(owner, LateFee.objects.get(), today=D(2026, 1, 10)) is None
    assert LateFee.objects.get().status == LateFee.Status.SKIPPED
    assert invoicing.lease_balance(lease) == 0


def test_skipping_keeps_the_reason(owner, lease):
    org = owner.organization
    set_rule(org, Mode.FIXED, amount=Decimal("500"))
    bill(lease, JAN)
    late_fees.propose(org, JAN_9)
    late_fees.skip(owner, LateFee.objects.get(), reason=" Promised Friday ")
    fee = LateFee.objects.get()
    assert (fee.status, fee.reason, fee.fee_invoice) == (LateFee.Status.SKIPPED, "Promised Friday", None)
    assert AuditEvent.objects.filter(action="late_fee.skip").exists()
    assert late_fees.propose(org, D(2026, 1, 20)) == 0  # a skipped fee never comes back


def test_only_members_who_issue_invoices_on_the_property(owner, prop, lease):
    org = owner.organization
    set_rule(org, Mode.FIXED, amount=Decimal("500"))
    bill(lease, JAN)
    late_fees.propose(org, JAN_9)
    fee = LateFee.objects.get()
    caretaker = add_member(org, "caretaker", properties=[prop])
    with pytest.raises(PermissionDenied):
        late_fees.approve(caretaker, fee, today=JAN_9)
    elsewhere = add_member(org, "manager", properties=[make_property(org)])
    assert not late_fees.to_approve(elsewhere).exists()
    with pytest.raises(PermissionDenied):
        late_fees.skip(elsewhere, fee)
    other = make_org()
    with pytest.raises(PermissionDenied):
        late_fees.skip(other, fee)
    assert LateFee.objects.get().status == LateFee.Status.PENDING


# ---------------------------------------------------------------------------
# The page and the home task
# ---------------------------------------------------------------------------


def test_the_page_charges_and_skips_ticked_fees(client, owner, prop):
    org = owner.organization
    set_rule(org, Mode.FIXED, amount=Decimal("500"))
    first = make_lease(owner, prop, code="A1", due_day=5, grace_days=3)
    second = make_lease(owner, prop, code="B22", due_day=5, grace_days=3)
    bill(first, JAN)
    bill(second, JAN)
    late_fees.propose(org, JAN_9)
    login(client, owner)
    url = reverse("billing:late_fees")
    r = client.get(url)
    assert r.status_code == 200 and len(r.context["pending"]) == 2
    assert [t.key for t in home.home(owner).tasks if t.key == "late_fees"] == ["late_fees"]
    a, b = LateFee.objects.order_by("lease__unit__code")
    r = client.post(url, {"fee": [str(a.public_id)], "action": "charge"})
    assert r.status_code == 302
    r = client.post(url, {"fee": [str(b.public_id)], "action": "skip", "reason": "Disputes the amount"})
    a.refresh_from_db()
    b.refresh_from_db()
    assert a.status == LateFee.Status.CHARGED and a.fee_invoice.lease == first
    assert (b.status, b.reason) == (LateFee.Status.SKIPPED, "Disputes the amount")
    r = client.get(url)
    assert not r.context["pending"] and len(r.context["decided"]) == 2
    assert client.post(url, {"action": "charge"}).status_code == 302  # nothing ticked
    assert not [t for t in home.home(owner).tasks if t.key == "late_fees"]


def test_the_settings_page_saves_the_rule(client, owner):
    login(client, owner)
    data = {"name": owner.organization.name, "org_type": owner.organization.org_type, "invoice_lead_days": 5, "financial_year_start_month": 1,
            "prorate_partial_months": "on", "late_fee_mode": "FIXED", "late_fee_amount": "",
            "late_fee_percent": "", "late_fee_cap": ""}
    r = client.post(reverse("accounts:organization"), data)
    assert r.status_code == 400
    r = client.post(reverse("accounts:organization"), {**data, "late_fee_amount": "500"})
    assert r.status_code == 302
    org = Organization.objects.get(pk=owner.organization.pk)
    assert (org.late_fee_mode, org.late_fee_amount) == (Mode.FIXED, Decimal("500.00"))
    assert client.get(reverse("billing:late_fees")).status_code == 200


def test_a_caretaker_cannot_open_the_page(client, owner, prop):
    caretaker = add_member(owner.organization, "caretaker", properties=[prop])
    login(client, caretaker)
    assert client.get(reverse("billing:late_fees")).status_code in (302, 403, 404)
