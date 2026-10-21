"""The arrears analysis report (D-132): owed vs overdue, ageing, recurring late payers, recovery, rights."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from reports import operations
from reports.tests.test_income import bill, make_lease, pay

pytestmark = pytest.mark.django_db

D = datetime.date
TODAY = D(2026, 3, 20)
START, END = D(2026, 3, 1), D(2026, 3, 31)


@pytest.fixture
def owner():
    return make_org(name="Acme")


@pytest.fixture
def prop(owner):
    return make_property(owner.organization, name="Acacia Court")


def report(owner, **kw):
    return operations.arrears(owner, START, END, today=TODAY, **kw)


def test_nothing_owed(owner):
    r = report(owner)
    assert (r.owed, r.overdue, r.leases, r.invoices, r.recovered) == (0, 0, 0, 0, 0)
    assert r.top == [] and r.recurring == []


def test_owed_and_overdue_are_apart(owner, prop):
    lease = make_lease(owner, prop, code="A1", start=D(2026, 1, 1))
    bill(lease, D(2026, 1, 1), D(2026, 3, 1))  # January overdue; March due on the 5th, late after the 8th
    r = report(owner)
    assert r.leases == 1 and r.invoices == 2
    assert r.overdue == Decimal(40000) and r.owed == Decimal(40000)
    assert r.top[0].overdue == Decimal(40000) and r.top[0].days > 0
    assert r.by_property == [("Acacia Court", Decimal(40000), 1)]


def test_not_yet_due_is_not_overdue(owner, prop):
    lease = make_lease(owner, prop, code="A1", start=D(2026, 3, 1))
    bill(lease, D(2026, 4, 1))  # next month's invoice, billed early
    r = report(owner)
    assert r.owed == Decimal(20000) and r.overdue == 0 and r.leases == 0 and r.top == []


def test_minimum_filter_and_other_organizations(owner, prop):
    bill(make_lease(owner, prop, code="A1", start=D(2026, 1, 1)), D(2026, 1, 1))
    bill(make_lease(owner, prop, code="A2", start=D(2026, 1, 1), rent=5000), D(2026, 1, 1))
    other = make_org(name="Other")
    bill(make_lease(other, make_property(other.organization), code="Z1", start=D(2026, 1, 1)), D(2026, 1, 1))
    assert len(report(owner).top) == 2
    assert [x.overdue for x in report(owner, min_amount=Decimal(10000)).top] == [Decimal(20000)]
    assert report(owner).overdue == Decimal(25000)


def test_recovered_counts_late_payments_in_the_period(owner, prop):
    lease = make_lease(owner, prop, code="A1", start=D(2026, 1, 1))
    bill(lease, D(2026, 1, 1))
    pay(owner, lease, 5000, D(2026, 3, 10))  # late: after 8 January... in the period
    pay(owner, lease, 1000, D(2026, 2, 10))  # late but another period
    r = report(owner)
    assert r.recovered == Decimal(5000) and r.recovered_count == 1


def test_recurring_late_payers(owner, prop):
    lease = make_lease(owner, prop, code="A1", start=D(2025, 10, 1))
    months = [D(2025, 12, 1), D(2026, 1, 1), D(2026, 2, 1)]
    bill(lease, *months)  # none paid
    r = report(owner)
    assert [(x.lease.pk, x.late) for x in r.recurring] == [(lease.pk, 3)]


def test_needs_both_rights(owner, prop):
    caretaker = add_member(owner.organization, "caretaker", properties=[prop])
    with pytest.raises(PermissionDenied):
        report(caretaker)


def test_page_and_csv(client, owner, prop):
    bill(make_lease(owner, prop, code="A1", start=D(2026, 1, 1)), D(2026, 1, 1))
    login(client, owner)
    assert client.get(reverse("reports:arrears")).status_code == 200
    assert client.get(reverse("reports:arrears"), {"min": "abc"}).status_code == 200
    body = client.get(reverse("reports:arrears"), {"format": "csv"}).content.decode("utf-8-sig")
    assert "By lease" in body and "Tenant A1" in body
    assert "Arrears analysis" in client.get(reverse("reports:centre")).content.decode()


def test_notes_say_what_the_page_shows(owner, prop):
    from reports import report_notes

    assert report_notes.arrears(report(owner), "KES")[0].text == "Nothing is owed."
    bill(make_lease(owner, prop, code="A1", start=D(2026, 1, 1)), D(2026, 1, 1))
    texts = [h.text for h in report_notes.arrears(report(owner), "KES")]
    assert "overdue on 1 lease" in texts[0]


def test_ageing_slices_open_receivables_for_the_same_property(client, owner, prop):
    bill(make_lease(owner, prop, code="A1", start=D(2026, 1, 1)), D(2026, 1, 1))
    login(client, owner)
    html = client.get(reverse("reports:arrears"), {"property": str(prop.public_id)}).content.decode()
    assert reverse("reports:receivables") in html and str(prop.public_id) in html
