"""Maintenance and message delivery reports, the report centre and the shared period (D-132)."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils import timezone

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from expenses.models import Expense, ExpenseCategory
from maintenance.models import MaintenanceRequest as Request
from notifications.models import Message
from reports import operations, periods

pytestmark = pytest.mark.django_db

D = datetime.date
NOW = timezone.make_aware(datetime.datetime(2026, 3, 20, 12, 0))


def at(day, hour=9):
    return timezone.make_aware(datetime.datetime(2026, 3, day, hour))


@pytest.fixture
def owner():
    return make_org(name="Acme")


@pytest.fixture
def org(owner):
    return owner.organization


@pytest.fixture
def prop(org):
    return make_property(org, name="Acacia Court")


def req(owner, prop, *, created=at(2), status=Request.Status.NEW, kind="PLUMBING", due=None, done=None, closed=None,  # noqa: B008 - a fixed date, built once
        priority="NORMAL", n=[0]):  # noqa: B006 - the counter is meant to be shared between calls
    n[0] += 1
    r = Request.objects.create(
        organization=owner.organization, number=f"M-{n[0]}", property=prop, title="Leak", kind=kind,
        priority=priority, respond_by=created, due_at=due or created + datetime.timedelta(days=7),
        reported_by=owner.user)
    Request.objects.filter(pk=r.pk).update(created_at=created, status=status, done_at=done, closed_at=closed)
    r.refresh_from_db()
    return r


def report(owner, **kw):
    return operations.maintenance(owner, D(2026, 3, 1), D(2026, 3, 31), now=NOW, **kw)


# --- the period --------------------------------------------------------------


@pytest.mark.parametrize("key,start,end", [
    ("this_month", D(2026, 3, 1), D(2026, 3, 31)),
    ("last_month", D(2026, 2, 1), D(2026, 2, 28)),
    ("this_quarter", D(2026, 1, 1), D(2026, 3, 31)),
    ("last_quarter", D(2025, 10, 1), D(2025, 12, 31)),
    ("this_year", D(2026, 1, 1), D(2026, 12, 31)),
    ("last_year", D(2025, 1, 1), D(2025, 12, 31)),
])
def test_presets(key, start, end):
    p = periods.preset(key, D(2026, 3, 20))
    assert (p.start, p.end) == (start, end)


@pytest.mark.parametrize("key,start,end", [
    ("this_quarter", D(2026, 1, 1), D(2026, 3, 31)),
    ("last_quarter", D(2025, 10, 1), D(2025, 12, 31)),
    ("this_year", D(2025, 7, 1), D(2026, 6, 30)),
    ("last_year", D(2024, 7, 1), D(2025, 6, 30)),
])
def test_presets_follow_a_july_financial_year(key, start, end):
    p = periods.preset(key, D(2026, 3, 20), 7)
    assert (p.start, p.end) == (start, end)


def test_july_year_after_it_has_started_and_quarters_count_from_it():
    today = D(2026, 8, 5)
    assert (periods.preset("this_year", today, 7).start, periods.preset("this_year", today, 7).end) == (
        D(2026, 7, 1), D(2027, 6, 30))
    q = periods.preset("this_quarter", today, 7)
    assert (q.start, q.end) == (D(2026, 7, 1), D(2026, 9, 30))
    q = periods.preset("last_quarter", today, 7)
    assert (q.start, q.end) == (D(2026, 4, 1), D(2026, 6, 30))


def test_custom_period_is_swapped_capped_and_defaults_to_this_month():
    today = D(2026, 3, 20)
    p = periods.from_request({"period": "custom", "from": "2026-03-10", "to": "2026-03-01"}, today)
    assert (p.start, p.end) == (D(2026, 3, 1), D(2026, 3, 10))
    long = periods.from_request({"period": "custom", "from": "2020-01-01", "to": "2026-03-01"}, today)
    assert long.days == periods.MAX_DAYS and long.end == D(2026, 3, 1)
    assert periods.from_request({"period": "custom", "from": "nope"}, today).key == "this_month"
    assert periods.from_request({}, today).key == "this_month"


def test_previous_period_is_the_same_length_just_before():
    p = periods.Period("custom", D(2026, 3, 11), D(2026, 3, 20)).previous()
    assert (p.start, p.end) == (D(2026, 3, 1), D(2026, 3, 10))


# --- maintenance -------------------------------------------------------------


def test_no_requests(owner):
    r = report(owner)
    assert r.total == 0 and r.avg_hours is None and r.by_kind == [] and r.resolved_share is None


def test_resolution_time_counts_only_finished_requests(owner, prop):
    req(owner, prop, created=at(2, 9), status="DONE", done=at(2, 19))  # 10 h
    req(owner, prop, created=at(3, 9), status="CLOSED", closed=at(4, 9))  # no done time: closed time, 24 h
    req(owner, prop, created=at(5, 9))  # still open: left out of the average
    r = report(owner)
    assert (r.total, r.resolved, r.open_now) == (3, 2, 1)
    assert r.avg_hours == Decimal("17.0")
    assert r.resolved_share == Decimal(2) / 3


def test_cancelled_requests_are_not_counted(owner, prop):
    req(owner, prop, status="CANCELLED")
    req(owner, prop)
    r = report(owner)
    assert (r.total, r.cancelled) == (1, 1)


def test_overdue_is_open_and_past_fix_by_time_now(owner, prop):
    req(owner, prop, due=at(10))  # open, late
    req(owner, prop, due=at(25))  # open, not yet
    req(owner, prop, status="DONE", done=at(12), due=at(10))  # finished, not overdue
    r = report(owner)
    assert r.overdue_now == 1 and r.open_now == 2
    assert r.fixed_in_time == 0


def test_fixed_in_time_share(owner, prop):
    req(owner, prop, status="DONE", done=at(3), due=at(5))
    req(owner, prop, status="DONE", done=at(8), due=at(5))
    r = report(owner)
    assert r.fixed_in_time == 1 and r.fixed_in_time_share == Decimal("0.5")


def test_period_bounds_use_local_time(owner, prop):
    req(owner, prop, created=timezone.make_aware(datetime.datetime(2026, 3, 1, 0, 30)))
    req(owner, prop, created=timezone.make_aware(datetime.datetime(2026, 3, 31, 23, 30)))
    req(owner, prop, created=timezone.make_aware(datetime.datetime(2026, 2, 28, 23, 30)))
    req(owner, prop, created=timezone.make_aware(datetime.datetime(2026, 4, 1, 0, 30)))
    assert report(owner).total == 2


def test_costs_count_live_expenses_only(owner, prop):
    r1 = req(owner, prop, kind="PLUMBING")
    category = ExpenseCategory.objects.create(organization=owner.organization, name="Repairs")
    for amount, status in ((1000, "APPROVED"), (500, "SUBMITTED"), (700, "VOIDED"), (900, "REJECTED")):
        Expense.objects.create(organization=owner.organization, property=prop, category=category,
                               description="Fix", amount=amount, paid_on=D(2026, 3, 5), maintenance_request=r1,
                               status=status, recorded_by=owner.user, number=f"E{amount}", reference_key=f"k{amount}")
    r = report(owner)
    assert r.cost == Decimal("1500")
    assert r.by_kind[0].cost == Decimal("1500") and r.by_property[0].cost == Decimal("1500")


def test_repeated_problems_need_three_of_a_kind_for_one_place(owner, prop):
    for d in (2, 3, 4):
        req(owner, prop, created=at(d), kind="PESTS")
    req(owner, prop, kind="PLUMBING")
    repeats = report(owner).repeats
    assert len(repeats) == 1 and repeats[0]["count"] == 3 and repeats[0]["kind"] == "Pests"


def test_other_organizations_requests_are_never_counted(owner, prop):
    other = make_org(name="Other")
    req(other, make_property(other.organization))
    req(owner, prop)
    assert report(owner).total == 1


def test_a_property_filter_narrows_everything(owner, prop):
    other = make_property(owner.organization, name="Other Court")
    req(owner, prop)
    req(owner, other)
    assert report(owner).total == 2
    assert report(owner, prop=prop).total == 1


def test_a_member_sees_only_their_properties(owner, prop, org):
    other = make_property(org, name="Other Court")
    req(owner, prop)
    req(owner, other)
    manager = add_member(org, "caretaker", properties=[prop])
    assert report(manager).total == 1


def test_reports_need_their_capabilities(org):
    accountant = add_member(org, "accountant", all_properties=True)  # no maintenance.view, no messages.view
    with pytest.raises(PermissionDenied):
        operations.maintenance(accountant, D(2026, 3, 1), D(2026, 3, 31))
    with pytest.raises(PermissionDenied):
        operations.delivery(accountant, D(2026, 3, 1), D(2026, 3, 31))


# --- messages ----------------------------------------------------------------


def msg(owner, status, channel="SMS", **kw):
    m = Message.objects.create(organization=owner.organization, type="payment_receipt", channel=channel, to="0722000111",
                               status=status, **kw)
    Message.objects.filter(pk=m.pk).update(created_at=at(5))
    return m


def test_delivery_counts_and_leaves_skipped_out_of_the_failure_rate(owner):
    msg(owner, "SENT")
    msg(owner, "DELIVERED")
    msg(owner, "FAILED", error="no credit")
    msg(owner, "SKIPPED", skip_reason="OPTED_OUT")
    msg(owner, "SENT", channel="EMAIL")
    r = operations.delivery(owner, D(2026, 3, 1), D(2026, 3, 31), now=NOW)
    sms = next(c for c in r.channels if c.channel == "SMS")
    assert (sms.total, sms.attempted) == (4, 3)
    assert sms.failure_rate == Decimal(1) / 3
    assert r.failed == 1 and r.attempted == 4
    assert r.skipped == [("Recipient opted out", 1)] and r.errors == [("no credit", 1)]


def test_delivery_is_per_organization_and_dated(owner):
    msg(make_org(name="Other"), "FAILED")
    old = msg(owner, "FAILED")
    Message.objects.filter(pk=old.pk).update(created_at=timezone.make_aware(datetime.datetime(2026, 2, 1)))
    assert operations.delivery(owner, D(2026, 3, 1), D(2026, 3, 31)).total == 0


# --- pages -------------------------------------------------------------------


def test_pages_render_empty_and_full(client, owner, prop):
    login(client, owner)
    for name in ("reports:centre", "reports:maintenance", "reports:delivery"):
        assert client.get(reverse(name)).status_code == 200
    req(owner, prop, created=timezone.now())
    msg(owner, "FAILED")
    Message.objects.update(created_at=timezone.now())
    assert client.get(reverse("reports:maintenance")).status_code == 200
    assert client.get(reverse("reports:delivery")).status_code == 200


def test_csv_matches_the_screen_and_is_audited(client, owner, prop):
    login(client, owner)
    first = timezone.localdate().replace(day=1)
    req(owner, prop, created=timezone.now())
    req(owner, prop, created=timezone.now(), status="DONE", done=timezone.now())
    response = client.get(reverse("reports:maintenance"), {"format": "csv"})
    assert response.status_code == 200 and response["Content-Type"].startswith("text/csv")
    body = response.content.decode("utf-8-sig")
    rows = [line.split(",") for line in body.splitlines()]
    assert ["2", "1", "1", "0"] == rows[rows.index(["Summary"]) + 2][:4]
    assert first.isoformat() in body


def test_another_orgs_property_filter_is_not_found(client, owner, prop):
    other = make_org(name="Other")
    foreign = make_property(other.organization)
    login(client, owner)
    assert client.get(reverse("reports:maintenance"), {"property": str(foreign.public_id)}).status_code == 404


def test_delivery_rejects_a_property_filter(client, owner, prop):
    login(client, owner)
    assert client.get(reverse("reports:delivery"), {"property": str(prop.public_id)}).status_code == 404


def test_centre_lists_only_what_the_role_may_open(client, org, owner):
    accountant = add_member(org, "accountant", all_properties=True)
    login(client, accountant)
    page = client.get(reverse("reports:centre")).content.decode()
    assert reverse("reports:receivables") in page
    assert reverse("reports:maintenance") not in page and reverse("reports:delivery") not in page
    assert client.get(reverse("reports:maintenance")).status_code == 403
    assert client.get(reverse("reports:delivery")).status_code == 403


def test_a_caretaker_cannot_export_or_see_money_reports(client, org, owner, prop):
    caretaker = add_member(org, "caretaker", properties=[prop])
    login(client, caretaker)
    assert client.get(reverse("reports:centre")).status_code == 403
    assert client.get(reverse("reports:maintenance")).status_code == 200
    assert client.get(reverse("reports:maintenance"), {"format": "csv"}).status_code == 403


def test_delivery_notes_and_channel_links():
    from decimal import Decimal

    from reports import operations as ops
    from reports import report_notes

    S = ops.Message.Status
    channels = [ops.ChannelRow("SMS", "SMS", {S.SENT: 6, S.FAILED: 4}), ops.ChannelRow("EMAIL", "Email", {S.SENT: 10})]
    r = ops.DeliveryReport(datetime.date(2026, 3, 1), datetime.date(2026, 3, 31), channels,
                           [("No consent", 3)], [], Decimal("120"), None)
    texts = [h.text for h in report_notes.delivery(r, "KES")]
    assert any("fails most" in t and "SMS" in t for t in texts) and any("No consent" in t for t in texts)
    assert report_notes.NOTES["message-delivery"] is report_notes.delivery
