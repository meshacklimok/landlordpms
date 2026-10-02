"""Collectability grades, promises to pay and the who-to-call list (D-052)."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from accounts.tests.factories import add_member, make_org, make_property
from billing import followups
from billing.models import FollowUp
from leases.models import Lease
from reports import metrics
from reports.tests.test_income import bill, make_lease, pay

pytestmark = pytest.mark.django_db

D = datetime.date
Outcome = FollowUp.Outcome


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def prop(owner):
    return make_property(owner.organization, name="Acacia Court")


def grade(lease, today):
    return metrics.grades([lease], today)[lease.pk]


# ---------------------------------------------------------------------------
# Grades (invoices due on the 5th with 3 days' grace: late after the 8th)
# ---------------------------------------------------------------------------


def test_always_in_time_is_a(owner, prop):
    lease = make_lease(owner, prop, start=D(2025, 1, 1))
    months = [D(2025, m, 1) for m in range(1, 9)]
    bill(lease, *months)
    for month in months:
        pay(owner, lease, 20000, month.replace(day=8))
    g = grade(lease, D(2025, 9, 1))
    # Only the last six invoices past due count.
    assert (g.grade, g.invoices, g.on_time, g.average_late) == ("A", 6, 6, Decimal("0.0"))


def test_one_invoice_is_new(owner, prop):
    lease = make_lease(owner, prop)
    bill(lease, D(2025, 1, 1), D(2025, 2, 1))
    pay(owner, lease, 40000, D(2025, 1, 6))
    g = grade(lease, D(2025, 2, 8))  # Feb is not past due yet
    assert (g.grade, g.label, g.invoices) == (None, "New", 1)


def test_average_days_late(owner, prop):
    lease = make_lease(owner, prop)
    bill(lease, D(2025, 1, 1), D(2025, 2, 1))
    pay(owner, lease, 20000, D(2025, 1, 18))  # 10 days late
    pay(owner, lease, 20000, D(2025, 2, 8))  # in time
    g = grade(lease, D(2025, 3, 1))
    assert (g.grade, g.on_time, g.average_late) == ("B", 1, Decimal("5.0"))


def test_a_part_payment_is_not_paid_until_the_invoice_is_covered(owner, prop):
    lease = make_lease(owner, prop)
    bill(lease, D(2025, 1, 1), D(2025, 2, 1))
    pay(owner, lease, 10000, D(2025, 1, 6))
    pay(owner, lease, 10000, D(2025, 1, 28))  # Jan settled 20 days late
    pay(owner, lease, 20000, D(2025, 2, 8))
    assert grade(lease, D(2025, 3, 1)).grade == "C"


def test_an_old_unpaid_invoice_caps_the_grade(owner, prop):
    lease = make_lease(owner, prop)
    bill(lease, D(2025, 1, 1), D(2025, 2, 1), D(2025, 3, 1))
    pay(owner, lease, 40000, D(2025, 1, 6))  # Jan and Feb in time, Mar never paid
    # 20 Apr: Mar is 43 days late, average 14.3 (C), capped at D.
    g = grade(lease, D(2025, 4, 20))
    assert (g.grade, g.open_late) == ("D", 43)
    assert grade(lease, D(2025, 5, 20)).grade == "E"


def test_the_grade_follows_the_tenancy_across_a_renewal(owner, prop):
    old = make_lease(owner, prop, code="A1")
    bill(old, D(2025, 1, 1), D(2025, 2, 1))
    pay(owner, old, 40000, D(2025, 1, 6))
    new = make_lease(owner, prop, code="A2", start=D(2025, 3, 1))
    Lease.all_objects.filter(pk=new.pk).update(previous_lease=old)
    bill(new, D(2025, 3, 1))
    pay(owner, new, 20000, D(2025, 3, 7))
    g = grade(new, D(2025, 4, 1))
    assert (g.grade, g.invoices) == ("A", 3)


# ---------------------------------------------------------------------------
# Follow-ups and promises (these use the real date: a follow-up is stamped now)
# ---------------------------------------------------------------------------

TODAY = timezone.localdate()


def owing_lease(owner, prop, code="A1", rent=20000):
    """A lease with two months overdue."""
    first = metrics.add_months(metrics.month_start(TODAY), -3)
    lease = make_lease(owner, prop, code=code, start=first, rent=rent)
    bill(lease, first, metrics.add_months(first, 1))
    return lease


def test_a_promise_needs_a_date_in_the_future(owner, prop):
    lease = owing_lease(owner, prop)
    with pytest.raises(ValidationError):
        followups.record_follow_up(owner, lease, outcome=Outcome.PROMISED)
    with pytest.raises(ValidationError):
        followups.record_follow_up(owner, lease, outcome=Outcome.PROMISED, promised_on=TODAY - datetime.timedelta(1))
    # Other outcomes drop any promise fields.
    f = followups.record_follow_up(owner, lease, outcome=Outcome.NO_ANSWER, promised_on=TODAY)
    assert f.promised_on is None and f.created_by == owner.user


def test_promise_states(owner, prop):
    lease = owing_lease(owner, prop)
    followups.record_follow_up(owner, lease, outcome=Outcome.PROMISED, promised_on=TODAY + datetime.timedelta(3),
                               promised_amount=Decimal(5000))
    board = followups.call_list(owner, TODAY)
    assert [r.lease for r in board.promised] == [lease] and board.promised[0].promise == followups.PENDING
    # Past the date with nothing paid: broken, and back on the list to call.
    later = followups.call_list(owner, TODAY + datetime.timedelta(5))
    assert [r.lease for r in later.to_call] == [lease] and later.to_call[0].promise == followups.BROKEN
    pay(owner, lease, 5000, TODAY)
    kept = followups.call_list(owner, TODAY + datetime.timedelta(5))
    assert kept.to_call[0].promise == followups.KEPT


def test_a_promise_without_an_amount_is_kept_by_any_payment(owner, prop):
    lease = owing_lease(owner, prop)
    f = followups.record_follow_up(owner, lease, outcome=Outcome.PROMISED, promised_on=TODAY)
    assert followups.promise_state(f, [], TODAY) == followups.PENDING
    assert followups.promise_state(f, [(TODAY, Decimal(100))], TODAY) == followups.KEPT
    # A payment before the promise was made does not count.
    assert followups.promise_state(f, [(TODAY - datetime.timedelta(1), Decimal(100))],
                                   TODAY + datetime.timedelta(1)) == followups.BROKEN


def test_the_list_order(owner, prop):
    small = owing_lease(owner, prop, code="A1", rent=10000)
    big = owing_lease(owner, prop, code="A2", rent=30000)
    broken = owing_lease(owner, prop, code="A3", rent=5000)
    called = owing_lease(owner, prop, code="A4")
    followups.record_follow_up(owner, broken, outcome=Outcome.PROMISED, promised_on=TODAY)
    followups.record_follow_up(owner, called, outcome=Outcome.SPOKE, note="Will come by")
    board = followups.call_list(owner, TODAY + datetime.timedelta(1))
    assert [r.lease for r in board.to_call] == [broken, big, called, small]  # yesterday's call is due again
    today_board = followups.call_list(owner, TODAY)
    assert [r.lease for r in today_board.done_today] == [called]
    assert [r.overdue for r in today_board.to_call] == [Decimal(60000), Decimal(20000)]


def test_filters(owner, prop):
    other = make_property(owner.organization, name="Baobab Flats")
    a = owing_lease(owner, prop, code="A1")
    b = owing_lease(owner, other, code="B1")
    assert [r.lease for r in followups.call_list(owner, TODAY, property=other).to_call] == [b]
    assert followups.call_list(owner, TODAY, grade="A").count == 0
    assert {r.lease for r in followups.call_list(owner, TODAY, grade=grade(a, TODAY).grade or "new").to_call} == {a, b}


def test_scope_and_permissions(owner, prop):
    other = make_property(owner.organization, name="Baobab Flats")
    mine = owing_lease(owner, prop, code="A1")
    theirs = owing_lease(owner, other, code="B1")
    manager = add_member(owner.organization, "manager", properties=[prop])
    assert [r.lease for r in followups.call_list(manager, TODAY).to_call] == [mine]
    with pytest.raises(PermissionDenied):
        followups.record_follow_up(manager, theirs, outcome=Outcome.SPOKE)
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    assert followups.call_list(caretaker, TODAY).count == 0
    with pytest.raises(PermissionDenied):
        followups.record_follow_up(caretaker, mine, outcome=Outcome.SPOKE)
    stranger = make_org()
    with pytest.raises(PermissionDenied):
        followups.record_follow_up(stranger, mine, outcome=Outcome.SPOKE)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def test_the_page(client, owner, prop):
    owing_lease(owner, prop)
    client.force_login(owner.user)
    page = client.get("/billing/call-list/").content.decode()
    assert "Tenant A1" in page and 'href="tel:' in page and "KES 40,000.00 overdue" in page
    assert "Record a call" in page and "0 of 2 paid in time" in page


def test_recording_a_promise(client, owner, prop):
    lease = owing_lease(owner, prop)
    client.force_login(owner.user)
    url = f"/billing/call-list/{lease.public_id}/"
    response = client.post(url, {"outcome": "PROMISED", "promised_on": "", "next_query": "grade=E"})
    assert response.status_code == 200 and "Enter the date they will pay by." in response.content.decode()
    assert not FollowUp.objects.exists()
    response = client.post(url, {"outcome": "PROMISED", "promised_on": (TODAY + datetime.timedelta(2)).isoformat(),
                                 "promised_amount": "15000", "note": "Salary on Friday", "next_query": "grade=E"})
    assert response.status_code == 302 and response["Location"] == "/billing/call-list/?grade=E"
    f = FollowUp.objects.get()
    assert (f.lease, f.promised_amount, f.note) == (lease, Decimal(15000), "Salary on Friday")
    page = client.get("/billing/call-list/").content.decode()
    assert "Salary on Friday" in page and "KES 15,000.00" in page


def test_pages_are_refused_without_the_capabilities(client, owner, prop):
    lease = owing_lease(owner, prop)
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    client.force_login(caretaker.user)
    assert client.get("/billing/call-list/").status_code == 403
    assert client.post(f"/billing/call-list/{lease.public_id}/", {"outcome": "SPOKE"}).status_code == 403
