"""Who sees which payments (doc 13 scoping)."""

import datetime
from decimal import Decimal

import pytest

from accounts.tests.factories import add_member, make_org, make_property
from billing.tests.test_invoicing import FEB, bill, make_lease
from payments import selectors, services
from payments.models import Payment

from .test_services import pay

pytestmark = pytest.mark.django_db

D = datetime.date


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def test_visibility_follows_capability_property_and_org():
    owner = make_org()
    org = owner.organization
    p1, p2 = make_property(org), make_property(org)
    here = pay(owner, make_lease(owner, p1, code="A1"), "100", reference="HERE")
    there = pay(owner, make_lease(owner, p2, code="B1"), "100")
    stranger = make_org()
    pay(stranger, make_lease(stranger, make_property(stranger.organization)), "100")

    assert set(selectors.visible_payments(owner)) == {here, there}
    scoped = add_member(org, "accountant", properties=[p1])
    assert list(selectors.visible_payments(scoped)) == [here]
    assert not selectors.visible_payments(add_member(org, "caretaker", all_properties=True)).exists()
    assert list(selectors.filter_payments(selectors.visible_payments(owner), q="here")) == [here]
    assert list(selectors.filter_payments(selectors.visible_payments(owner), q=here.receipt.number)) == [here]
    assert not selectors.filter_payments(selectors.visible_payments(owner), status="pending").exists()


def test_review_queue_is_for_checkers_only():
    owner = make_org()
    org = owner.organization
    lease = make_lease(owner, make_property(org))
    accountant = add_member(org, "accountant", all_properties=True)
    pending = pay(accountant, lease, "100")
    pay(owner, lease, "100")
    assert list(selectors.pending_review(owner)) == [pending]
    assert not selectors.pending_review(accountant).exists()
    manager = add_member(org, "manager", all_properties=True)
    assert list(selectors.pending_review(manager)) == [pending]


def test_states_tell_rejected_from_reversed_and_totals_add_up():
    owner = make_org()
    org = owner.organization
    lease = make_lease(owner, make_property(org))
    accountant = add_member(org, "accountant", all_properties=True)
    confirmed = pay(owner, lease, "1000")
    reversed_ = services.reverse_payment(owner, pay(owner, lease, "200"), reason="Bounced")
    rejected = services.reject_payment(owner, pay(accountant, lease, "300"), reason="Not received")
    pending = pay(accountant, lease, "400")

    assert [p.state for p in (confirmed, reversed_, rejected, pending)] == [
        "confirmed", "reversed", "rejected", "pending"]
    visible = selectors.visible_payments(owner)
    for key, payment in {"confirmed": confirmed, "reversed": reversed_, "rejected": rejected,
                         "pending": pending}.items():
        assert list(selectors.filter_payments(visible, status=key)) == [payment]

    totals = selectors.totals_by_state(visible)
    assert totals["all"].count == 4 and totals["all"].amounts == {"KES": Decimal("1900.00")}
    assert (totals["confirmed"].count, totals["confirmed"].amounts) == (1, {"KES": Decimal("1000.00")})
    assert (totals["rejected"].count, totals["reversed"].count, totals["pending"].count) == (1, 1, 1)
    assert selectors.totals_by_state(visible.none())["confirmed"].amounts == {}


def test_filters_by_method_property_and_dates():
    owner = make_org()
    org = owner.organization
    p1, p2 = make_property(org), make_property(org)
    early = pay(owner, make_lease(owner, p1, code="A1"), "100", method="MPESA", paid_at=D(2026, 1, 10))
    late = pay(owner, make_lease(owner, p2, code="B1"), "100", method="CASH", paid_at=D(2026, 2, 20))
    visible = selectors.visible_payments(owner)
    assert list(selectors.filter_payments(visible, method="MPESA")) == [early]
    assert list(selectors.filter_payments(visible, property=p2)) == [late]
    assert list(selectors.filter_payments(visible, date_from=D(2026, 2, 1))) == [late]
    assert list(selectors.filter_payments(visible, date_to=D(2026, 1, 31))) == [early]
    unit_ref = late.lease.unit.payment_reference
    assert list(selectors.filter_payments(visible, q=unit_ref)) == [late]


def test_duplicate_references_ignore_blank_and_dead_payments():
    owner = make_org()
    lease = make_lease(owner, make_property(owner.organization))
    first = pay(owner, lease, "100", reference="QAB1")
    second = pay(owner, lease, "100", reference="qab1")
    blank_a, blank_b = pay(owner, lease, "100"), pay(owner, lease, "100")
    assert list(selectors.duplicates_of(first)) == [second]
    flags = {p.pk: p.has_duplicate for p in selectors.with_duplicate_flag(Payment.objects.all())}
    assert flags == {first.pk: True, second.pk: True, blank_a.pk: False, blank_b.pk: False}
    services.reverse_payment(owner, second, reason="Entered twice")
    assert not selectors.duplicates_of(first).exists()
    assert not selectors.same_reference(owner.organization, "  ").exists()
    assert not selectors.same_reference(make_org().organization, "QAB1").exists()


def test_payable_leases_carry_balance_and_follow_scope():
    owner = make_org()
    org = owner.organization
    p1, p2 = make_property(org), make_property(org)
    owing, paid_up = make_lease(owner, p1, code="A1"), make_lease(owner, p2, code="B1")
    bill(owing, FEB)
    leases = list(selectors.payable_leases(owner))
    assert leases == [owing, paid_up]
    assert (leases[0].balance, leases[1].balance) == (Decimal("15000.00"), Decimal("0.00"))
    assert list(selectors.payable_leases(owner, q="Tenant B1")) == [paid_up]
    assert list(selectors.payable_leases(owner, q=owing.unit.payment_reference)) == [owing]
    assert list(selectors.payable_leases(add_member(org, "accountant", properties=[p2]))) == [paid_up]
