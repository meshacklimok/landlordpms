"""The leases and turnover report (D-132): movement, expiry windows, holding over, tenant privacy."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from leases.models import Lease
from reports import operations
from reports.tests.test_income import make_lease

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


def lease(owner, prop, code, *, start=D(2025, 1, 1), end=None, status=None, ended=None):  # noqa: B008 - a fixed date, built once
    obj = make_lease(owner, prop, code=code, start=start)
    Lease.objects.filter(pk=obj.pk).update(end_date=end, ended_on=ended, **({"status": status} if status else {}))
    obj.refresh_from_db()
    return obj


def report(owner, **kw):
    return operations.leases(owner, START, END, today=TODAY, **kw)


def test_nothing(owner):
    r = report(owner)
    assert (r.active_now, r.move_ins, r.move_outs, r.in_force) == (0, 0, 0, 0)
    assert r.turnover is None and r.average_months is None and r.expiring == []


def test_movement_and_turnover(owner, prop):
    lease(owner, prop, "A1")  # stays
    lease(owner, prop, "A2", start=D(2026, 3, 10))  # moved in
    lease(owner, prop, "A3", status="ENDED", ended=D(2026, 3, 15), end=D(2026, 3, 15))  # moved out, 438 days, 14.4 months
    lease(owner, prop, "A4", status="TERMINATED", ended=D(2026, 2, 28))  # before the period
    lease(owner, prop, "A5", status="RENEWED", ended=D(2026, 3, 31))  # renewed, not a move-out
    r = report(owner)
    assert r.move_ins == 1 and r.move_outs == 1 and r.renewals == 1
    assert r.in_force == 4  # A1, A2, A3, A5; A4 ended before the period
    assert r.turnover == Decimal(1) / 4
    assert r.average_months == Decimal("14.4")
    assert r.active_now == 2


def test_expiry_windows_count_active_leases_only(owner, prop):
    lease(owner, prop, "B1", end=TODAY + datetime.timedelta(days=10))
    lease(owner, prop, "B2", end=TODAY + datetime.timedelta(days=45))
    lease(owner, prop, "B3", end=TODAY + datetime.timedelta(days=90))
    lease(owner, prop, "B4", end=TODAY + datetime.timedelta(days=91))
    lease(owner, prop, "B5", end=TODAY + datetime.timedelta(days=5), status="ENDED", ended=TODAY)
    r = report(owner)
    assert r.windows == [(30, 1), (60, 2), (90, 3)]
    assert [x.days for x in r.expiring] == [10, 45, 90]


def test_a_lease_past_its_end_date_is_flagged_not_called_expired(owner, prop):
    lease(owner, prop, "C1", end=TODAY - datetime.timedelta(days=3))
    r = report(owner)
    assert [x.days for x in r.holding_over] == [-3] and r.expiring == [] and r.active_now == 1


def test_other_organizations_and_properties_are_left_out(owner, prop):
    other = make_org(name="Other")
    lease(other, make_property(other.organization), "Z1", start=D(2026, 3, 5))
    second = make_property(owner.organization, name="Second")
    lease(owner, prop, "D1", start=D(2026, 3, 5))
    lease(owner, second, "D2", start=D(2026, 3, 6))
    assert report(owner).move_ins == 2
    assert report(owner, prop=prop).move_ins == 1


def test_tenant_names_need_the_tenants_right(owner, prop):
    lease(owner, prop, "E1", end=TODAY + datetime.timedelta(days=10))
    assert report(owner).expiring[0].tenant.name == "Tenant E1"
    caretaker = add_member(owner.organization, "caretaker", properties=[prop])
    with pytest.raises(PermissionDenied):
        report(caretaker)  # caretakers have no leases.view at all
    manager = add_member(owner.organization, "leasing_agent", properties=[prop])
    r = report(manager)
    assert r.show_tenants == bool(r.expiring and r.expiring[0].tenant)


def test_page_and_csv(client, owner, prop):
    lease(owner, prop, "F1", end=datetime.date.today() + datetime.timedelta(days=10))
    login(client, owner)
    assert client.get(reverse("reports:leases")).status_code == 200
    body = client.get(reverse("reports:leases"), {"format": "csv"}).content.decode("utf-8-sig")
    assert "Ending in the next 90 days" in body and "Tenant F1" in body


def test_stay_tenure_increase_and_relet(owner, prop):
    from leases.models import LeaseRentChange
    old = lease(owner, prop, "A1", start=D(2025, 1, 1), status=Lease.Status.ENDED, ended=D(2026, 3, 1))
    new = lease(owner, prop, "A1b", start=D(2025, 6, 1))
    Lease.objects.filter(pk=new.pk).update(unit=old.unit, start_date=D(2026, 3, 10))
    lease(owner, prop, "B1", start=D(2024, 1, 1))
    LeaseRentChange.objects.create(organization=owner.organization, lease=old, effective_from=D(2026, 3, 1),
                                   amount=Decimal("22000"))
    r = report(owner)
    assert r.relets == 1 and r.relet_days == Decimal("9.0")
    assert r.increases == 1 and r.average_increase == Decimal("0.1000")
    assert dict(r.stays)["1 to 2 years"] == 1
    assert dict(r.tenure)["2 years or more"] == 1
    assert r.stale_total == 1 and r.stale[0].days > 365
