"""Daily arrears snapshots and arrears at month end (D-077)."""

import datetime
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from accounts.models import Organization
from accounts.tests.factories import add_member, make_org, make_property
from properties.models import Property
from reports import metrics, snapshots
from reports.models import DailySnapshot
from reports.tests.test_income import bill, make_lease, pay

pytestmark = pytest.mark.django_db

D = datetime.date
JAN, FEB, MAR = D(2025, 1, 1), D(2025, 2, 1), D(2025, 3, 1)
JAN_31 = D(2025, 1, 31)


def money(value):
    return Decimal(value).quantize(Decimal("0.01"))


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def org(owner):
    return owner.organization


@pytest.fixture
def prop(org):
    return make_property(org, name="Acacia Court")


@pytest.fixture
def lease(owner, prop):
    """Rent 20,000 due on the 5th with 3 days' grace, billed for January."""
    lease = make_lease(owner, prop)
    bill(lease, JAN)
    return lease


def row(prop, day):
    return DailySnapshot.objects.get(property=prop, day=day)


# ---------------------------------------------------------------------------
# Taking snapshots
# ---------------------------------------------------------------------------


def test_one_row_per_property_matching_the_dashboard(owner, org, prop, lease):
    empty = make_property(org, name="Baobab Flats")
    bill(lease, FEB)
    assert snapshots.take(org, D(2025, 3, 1)) == 2
    snap = row(prop, D(2025, 3, 1))
    live = metrics.arrears(metrics.scope(owner), D(2025, 3, 1))
    assert [snap.d1_30, snap.d31_60, snap.d61_90, snap.d90] == [amount for _k, _l, amount in live.buckets]
    assert (snap.arrears(), snap.leases, snap.rebuilt) == (money(40000), 1, False)
    assert row(empty, D(2025, 3, 1)).arrears() == 0  # a zero, not a gap


def test_running_again_the_same_day_replaces_the_row(owner, org, prop, lease):
    snapshots.take(org, JAN_31)
    pay(owner, lease, 5000, JAN_31)
    snapshots.take(org, JAN_31)
    assert DailySnapshot.objects.count() == 1
    assert row(prop, JAN_31).d1_30 == money(15000)


def test_a_snapshot_keeps_the_day_as_it_stood(owner, org, prop, lease):
    snapshots.take(org, JAN_31)
    pay(owner, lease, 20000, D(2025, 1, 20))  # recorded later, dated before the snapshot
    assert metrics.arrears(metrics.scope(owner), JAN_31).total == 0
    assert row(prop, JAN_31).arrears() == money(20000)


def test_backfill_rebuilds_from_the_ledger_and_keeps_recorded_days(owner, org, prop, lease):
    snapshots.take(org, JAN_31)
    pay(owner, lease, 20000, D(2025, 1, 20))
    assert snapshots.backfill(org, 3, today=FEB) == 3  # Jan 29, 30 and 31
    assert row(prop, JAN_31).arrears() == money(20000) and not row(prop, JAN_31).rebuilt
    rebuilt = row(prop, D(2025, 1, 29))
    assert rebuilt.rebuilt and rebuilt.arrears() == 0
    # Only entries dated up to the day count: before the payment, January was overdue.
    snapshots.take(org, D(2025, 1, 15), rebuilt=True)
    assert row(prop, D(2025, 1, 15)).d1_30 == money(20000)


def test_an_archived_property_with_arrears_keeps_its_row(org, prop, lease):
    Property.all_objects.filter(pk=prop.pk).update(archived_at=timezone.now())
    snapshots.take(org, JAN_31)
    assert row(prop, JAN_31).arrears() == money(20000)


def test_the_job_skips_frozen_organizations(org, prop, lease):
    frozen = make_org().organization
    make_property(frozen)
    Organization.objects.filter(pk=frozen.pk).update(status=Organization.Status.FROZEN)
    assert snapshots.take_all(JAN_31) == {"organizations": Organization.objects.count() - 1,
                                          "rows": DailySnapshot.objects.count()}
    assert not DailySnapshot.objects.filter(organization=frozen).exists()


def test_backfill_command(org, prop, lease):
    call_command("backfill_snapshots", "--days", "2", "--org", str(org.pk), stdout=StringIO())
    assert DailySnapshot.objects.filter(organization=org, rebuilt=True).count() == 2
    with pytest.raises(CommandError):
        call_command("backfill_snapshots", "--days", "0", stdout=StringIO())
    with pytest.raises(CommandError):
        call_command("backfill_snapshots", "--org", "999999", stdout=StringIO())


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def test_history_uses_each_months_last_snapshot_and_leaves_gaps(owner, org, prop, lease):
    snapshots.take(org, D(2025, 1, 20))
    snapshots.take(org, JAN_31)
    pay(owner, lease, 20000, D(2025, 3, 2))
    snapshots.take(org, D(2025, 3, 3), rebuilt=True)
    points = snapshots.history(metrics.scope(owner), MAR, count=3)
    assert [p.month for p in points] == [JAN, FEB, MAR]
    jan, feb, mar = points
    assert (jan.day, jan.total, jan.leases, jan.rebuilt) == (JAN_31, money(20000), 1, False)
    assert feb.day is None and feb.total is None
    assert (mar.total, mar.rebuilt) == (0, True)


def test_history_counts_only_the_members_properties(owner, org, prop, lease):
    other = make_property(org, name="Baobab Flats")
    bill(make_lease(owner, other, code="B1", rent=30000), JAN)
    snapshots.take(org, JAN_31)
    manager = add_member(org, "manager", properties=[prop])
    assert snapshots.history(metrics.scope(manager), JAN, count=1)[0].total == money(20000)
    assert snapshots.history(metrics.scope(owner), JAN, count=1)[0].total == money(50000)
    assert snapshots.history(metrics.scope(owner, other), JAN, count=1)[0].total == money(30000)


# ---------------------------------------------------------------------------
# The dashboard
# ---------------------------------------------------------------------------


def test_the_dashboard_says_when_nothing_is_recorded(client, owner, lease):
    client.force_login(owner.user)
    page = client.get("/reports/?month=2025-01").content.decode()
    assert "Recorded every night from now on" in page and 'id="history-chart"' not in page


def test_the_dashboard_and_csv_show_arrears_at_month_end(client, owner, org, lease):
    snapshots.take(org, JAN_31)
    snapshots.take(org, D(2025, 2, 10), rebuilt=True)
    client.force_login(owner.user)
    response = client.get("/reports/?month=2025-02")
    assert response.context["chart"]["history_labels"][-2:] == ["Jan 2025", "Feb 2025"]
    first = response.context["chart"]["history"][0]
    assert first["data"][-2] == 20000.0 and first["data"][0] is None
    page = response.content.decode()
    assert 'id="history-chart"' in page and "Feb 2025 *" in page and "Not recorded" in page
    body = client.get("/reports/dashboard.csv?month=2025-02").content.decode("utf-8-sig")
    assert "Arrears at month end" in body and "2025-01,2025-01-31,20000.00" in body
