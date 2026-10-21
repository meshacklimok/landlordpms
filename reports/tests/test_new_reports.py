"""How tenants pay, the property scorecard and the deposits report (D-135)."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from billing import deposits as deposit_services
from leases.models import Lease
from reports import behaviour, deposit_report, scorecard
from reports.tests.test_income import bill, make_lease, pay

pytestmark = pytest.mark.django_db

D = datetime.date
TODAY = D(2025, 4, 20)
START, END = D(2025, 1, 1), D(2025, 3, 31)


@pytest.fixture
def owner():
    return make_org(name="Acme")


@pytest.fixture
def prop(owner):
    return make_property(owner.organization, name="Acacia Court")


def test_behaviour_days_bands_and_ranking(owner, prop):
    steady = make_lease(owner, prop, code="S1")
    slow = make_lease(owner, prop, code="S2")
    # Rent is due on the 5th with three grace days.
    bill(steady, D(2025, 1, 1), D(2025, 2, 1))
    bill(slow, D(2025, 1, 1), D(2025, 2, 1), D(2025, 3, 1))
    pay(owner, steady, 20000, D(2025, 1, 5))  # on the due date
    pay(owner, steady, 20000, D(2025, 1, 31))  # for February, paid early
    pay(owner, slow, 20000, D(2025, 1, 15))  # ten days late
    pay(owner, slow, 20000, D(2025, 2, 25))  # twenty days late
    r = behaviour.behaviour(owner, START, END, today=TODAY)
    assert (r.invoices, r.paid, r.unpaid) == (5, 4, 1)
    assert r.in_time == 2 and r.in_time_share == Decimal("0.5")
    counts = {b.key: b.count for b in r.bands}
    assert counts == {"early": 1, "due": 1, "d1_7": 0, "d8_14": 1, "d15_30": 1, "d31": 0}
    assert [x.lease.pk for x in r.slowest] == [slow.pk, steady.pk]
    assert r.slowest[0].average == Decimal("15.0") and r.slowest[0].unpaid == 1
    assert r.steadiest[0].lease.pk == steady.pk
    assert r.average == Decimal("6.2") and r.median == Decimal("5.0")


def test_behaviour_leaves_out_not_yet_due(owner, prop):
    lease = make_lease(owner, prop, code="N1")
    bill(lease, D(2025, 4, 1))
    r = behaviour.behaviour(owner, D(2025, 4, 1), D(2025, 4, 30), today=D(2025, 4, 3))
    assert r.invoices == 0 and r.average is None


def test_scorecard_ranks_properties(owner, prop):
    other = make_property(owner.organization, name="Baobab House")
    a = make_lease(owner, prop, code="A1")
    make_lease(owner, other, code="B1")
    bill(a, D(2025, 1, 1))
    pay(owner, a, 20000, D(2025, 1, 6))
    card = scorecard.scorecard(owner, START, END, today=TODAY)
    assert [r.property.name for r in card.rows] == ["Acacia Court", "Baobab House"]
    first = card.rows[0]
    assert (first.units, first.occupied, first.income) == (1, 1, Decimal("20000.00"))
    assert first.noi_per_unit == Decimal("20000.00") and first.collection == Decimal(1)
    assert card.total.units == 2 and card.total.income == Decimal("20000.00")
    assert card.rows[1].noi_per_unit == 0


def test_scorecard_one_property(owner, prop):
    make_lease(owner, prop, code="A1")
    card = scorecard.scorecard(owner, START, END, prop, today=TODAY)
    assert len(card.rows) == 1 and card.rows[0].units == 1


def test_deposits_held_moves_and_to_settle(owner, prop):
    ended = make_lease(owner, prop, code="E1")
    live = make_lease(owner, prop, code="L1")
    deposit_services.record_received(owner, ended, amount=30000, entry_date=D(2025, 1, 10))
    deposit_services.record_received(owner, live, amount=20000, entry_date=D(2025, 2, 10))
    deposit_services.deduct(owner, ended, amount=5000, reason="Repairs", entry_date=D(2025, 3, 20))
    Lease.objects.filter(pk=ended.pk).update(status=Lease.Status.ENDED, ended_on=D(2025, 3, 31))
    r = deposit_report.deposits(owner, START, END, today=TODAY)
    assert r.enabled and r.held == Decimal("45000.00") and r.holding == 2
    assert r.received == Decimal("50000.00") and r.deducted == Decimal("5000.00") and r.refunded == 0
    assert r.deduction_share == Decimal(1)
    assert [x.lease.pk for x in r.to_settle] == [ended.pk]
    assert r.to_settle_total == Decimal("25000.00") and r.to_settle[0].days == 20
    jan, feb, mar = r.months
    assert jan[1] == Decimal("30000.00") and feb[1] == Decimal("20000.00") and mar[2] == Decimal("5000.00")


def test_reports_need_financial_capability(owner, prop):
    caretaker = add_member(owner.organization, "caretaker", properties=[prop])
    for call in (behaviour.behaviour, scorecard.scorecard, deposit_report.deposits):
        with pytest.raises(PermissionDenied):
            call(caretaker, START, END, today=TODAY)


@pytest.mark.parametrize("name", ["behaviour", "scorecard", "deposits"])
def test_pages_and_csv(client, owner, prop, name):
    lease = make_lease(owner, prop, code="P1")
    bill(lease, D(2025, 1, 1))
    pay(owner, lease, 20000, D(2025, 1, 12))
    deposit_services.record_received(owner, lease, amount=10000, entry_date=D(2025, 1, 2))
    login(client, owner)
    query = {"period": "custom", "from": "2025-01-01", "to": "2025-03-31"}
    assert client.get(reverse(f"reports:{name}"), query).status_code == 200
    csv = client.get(reverse(f"reports:{name}"), {**query, "format": "csv"})
    assert csv.status_code == 200 and "Scope" in csv.content.decode("utf-8-sig")
    assert client.get(reverse("reports:centre")).status_code == 200


def test_health_score_reweights_missing_parts():
    from decimal import Decimal as D

    from reports import health
    full = health.points(collection=D("0.9"), occupancy=D("1"), overdue=D("0"), billed=D("100"), open_repairs=2,
                         overdue_repairs=0, recent_repairs=2, income=D("100"), expenses=D("30"))
    assert full.score == 97 and not full.provisional
    # No rent billed, no income, no repairs: only occupancy (25%) is measurable, so there is no score.
    bare = health.points(collection=None, occupancy=D("1"), overdue=None, billed=None, open_repairs=0,
                         overdue_repairs=0, recent_repairs=0, income=D("0"), expenses=D("0"))
    assert bare.score is None
    # Expenses and repairs missing: re-weighted over the other 75%, marked provisional.
    some = health.points(collection=D("0.8"), occupancy=D("0.8"), overdue=D("0"), billed=D("100"), open_repairs=0,
                         overdue_repairs=0, recent_repairs=0, income=D("0"), expenses=D("0"))
    assert some.score == 85 and some.provisional
    heavy = health.points(collection=D("1"), occupancy=D("1"), overdue=D("200"), billed=D("100"), open_repairs=2,
                          overdue_repairs=2, recent_repairs=2, income=D("100"), expenses=D("90"))
    assert [p.points for p in heavy.parts] == [100, 100, 0, 0, 0]


def test_scorecard_rows_carry_health(owner, prop):
    make_lease(owner, prop, code="H2")
    card = scorecard.scorecard(owner, START, END, today=TODAY)
    assert all(r.health is not None for r in card.rows)


def test_health_parts_link_to_their_records_only_when_allowed(owner, prop):
    make_lease(owner, prop, code="H1")
    row = scorecard.scorecard(owner, START, END, today=TODAY).rows[0]
    urls = {p.key: p.url for p in row.health.parts}
    assert urls["maintenance"].endswith(f"?property={prop.public_id}") and urls["expenses"].startswith("/")
    assert urls["collection"] == ""
