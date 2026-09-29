"""The home page for each member (D-056): work by capability, counts in scope, no figures leaked."""

import datetime

import pytest

from accounts.tests.factories import add_member, make_org, make_property
from billing import followups
from billing.models import FollowUp
from inspections import services as inspection_services
from leases import services as lease_services
from leases.models import Lease
from payments import services as payment_services
from properties import services as property_services
from reports import home
from reports.tests.test_income import bill, make_lease
from tenants import services as tenant_services

pytestmark = pytest.mark.django_db

D = datetime.date
JAN, MAR = D(2025, 1, 1), D(2025, 3, 1)


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def prop(owner):
    return make_property(owner.organization, name="Acacia Court")


@pytest.fixture
def portfolio(owner, prop):
    """One of each kind of work, on one property, as of 1 March 2025.

    A1 owes January (overdue), A2 ends within 60 days and has a pending payment and a draft move-in
    report, A3 has only a draft lease (so it is available to let).
    """
    overdue = make_lease(owner, prop, code="A1", start=JAN)
    bill(overdue, JAN)
    ending = make_lease(owner, prop, code="A2", start=JAN)
    Lease.all_objects.filter(pk=ending.pk).update(end_date=D(2025, 4, 15))
    accountant = add_member(owner.organization, "accountant", all_properties=True)
    payment_services.record_payment(accountant, ending, amount=5000, method="CASH", paid_at=D(2025, 2, 20))
    inspection_services.start(owner, ending, "MOVE_IN", inspected_on=JAN)
    unit = property_services.create_unit(owner, prop, code="A3")
    tenant = tenant_services.create_tenant(owner, name="Draft Tenant", phone="0712999888")
    lease_services.create_lease(owner, unit=unit, tenants=[tenant], start_date=MAR, end_date=None, rent=15000,
                                due_day=5, grace_days=3)
    return {"overdue": overdue, "ending": ending}


def keys(h):
    return {t.key: t.count for t in h.tasks}


# (template, tasks, sees expiring leases, sees reports to finish, figures: "financial" | "summary")
TABLE = [
    ("owner", {"confirm": 1, "call": 1, "drafts": 1, "vacant": 1}, True, True, "financial"),
    ("manager", {"confirm": 1, "call": 1, "drafts": 1, "vacant": 1}, True, True, "financial"),
    ("accountant", {"call": 1}, True, False, "financial"),
    ("caretaker", {}, False, True, "summary"),
    ("leasing_agent", {"vacant": 1}, True, True, "summary"),
    ("maintenance_manager", {}, False, False, "summary"),
    ("maintenance_staff", {}, False, False, "summary"),
    ("viewer", {}, True, False, "financial"),
]


@pytest.mark.parametrize("template,tasks,expiring,reports,figures", TABLE)
def test_each_default_role_sees_its_own_home(owner, portfolio, template, tasks, expiring, reports, figures):
    m = owner if template == "owner" else add_member(owner.organization, template, all_properties=True)
    h = home.home(m, today=MAR)
    assert keys(h) == tasks
    assert (h.expiring_count == 1) is expiring
    assert bool(h.reports) is reports
    assert (h.board is not None, h.summary is not None) == (figures == "financial", figures == "summary")


def test_the_summary_counts_without_amounts(owner, portfolio):
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    h = home.home(caretaker, today=MAR)
    assert h.summary.overdue_leases == 1
    assert (h.summary.occupancy.occupied, h.summary.occupancy.rentable) == (2, 3)


def test_a_scoped_member_sees_only_their_properties(owner, portfolio):
    other = make_property(owner.organization, name="Baobab Flats")
    manager = add_member(owner.organization, "manager", properties=[other])
    h = home.home(manager, today=MAR)
    assert h.tasks == [] and h.expiring == [] and h.reports == []
    assert h.board.occupancy.rentable == 0


def test_promises_and_broken_promises(owner, portfolio):
    lease = portfolio["overdue"]
    followups.record_follow_up(owner, lease, outcome=FollowUp.Outcome.PROMISED, promised_on=D(2099, 1, 1))
    FollowUp.objects.filter(lease=lease).update(promised_on=MAR)
    h = home.home(owner, today=MAR)
    assert keys(h)["promised"] == 1 and "call" not in keys(h)
    broken = home.home(owner, today=D(2025, 3, 2))
    assert keys(broken)["call"] == 1
    assert next(t for t in broken.tasks if t.key == "call").detail == "1 broken promise"


def test_nothing_waiting(owner):
    h = home.home(owner, today=MAR)
    assert not h.has_work


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


def test_the_page_lists_the_work_with_links(client, owner, portfolio):
    client.force_login(owner.user)
    page = client.get("/").content.decode()
    assert 'data-task="confirm"' in page and "/payments/review/" in page
    assert "/leases/?status=DRAFT" in page and "/properties/units/?status=vacant" in page
    assert "Condition reports to finish" in page
    assert "Open the dashboard" in page


def test_the_caretaker_page_shows_counts_but_no_money_or_names(client, owner, portfolio):
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    client.force_login(caretaker.user)
    page = client.get("/").content.decode()
    assert "Leases with rent overdue" in page and "2 of 3 units" in page
    assert "KES" not in page and "Open the dashboard" not in page
    assert "/payments/review/" not in page and "/billing/call-list/" not in page


def test_an_empty_home_says_so(client, owner):
    client.force_login(owner.user)
    assert "Nothing is waiting for you." in client.get("/").content.decode()
