"""Archived leases: finding, restoring, and coming back when money moves (D-079)."""

import datetime

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils import timezone

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from audit.models import AuditEvent
from billing import invoicing, jobs
from billing.tests.test_invoicing import FEB, MAR, bill, make_lease
from leases import services as lease_services
from leases.models import Lease
from payments import services as payment_services
from payments.models import Payment

pytestmark = pytest.mark.django_db

D = datetime.date


@pytest.fixture
def owner():
    return make_org(name="Acme")


@pytest.fixture
def prop(owner):
    return make_property(owner.organization, name="Acacia")


@pytest.fixture
def archived(owner, prop):
    """A1 billed for February and March (re-billed to the 10th when it ended), paid in full, then archived."""
    lease = make_lease(owner, prop, code="A1")
    bill(lease, FEB)
    bill(lease, MAR)
    lease_services.end_lease(owner, lease, ended_on=D(2026, 3, 10), reason="Moved out")
    payment = payment_services.record_payment(owner, lease, amount=invoicing.lease_balance(lease),
                                              method=Payment.Method.CASH, paid_at=D(2026, 3, 9))
    assert jobs.archive_settled_leases(timezone.localdate()) == 1
    lease = Lease.all_objects.get(pk=lease.pk)
    assert lease.is_archived
    return lease, payment


def events(lease, action):
    return AuditEvent.objects.filter(action=action, object_id=str(lease.public_id))


# ---------------------------------------------------------------------------
# The daily job
# ---------------------------------------------------------------------------


def test_a_reversed_payment_brings_an_archived_lease_back(owner, archived):
    lease, payment = archived
    assert jobs.restore_unsettled_leases() == 0  # settled: nothing to do
    paid = payment.amount
    payment_services.reverse_payment(owner, payment, reason="Bounced cheque")
    assert jobs.restore_unsettled_leases() == 1
    lease.refresh_from_db()
    assert not lease.is_archived
    event = events(lease, "lease.restore").get()
    assert event.actor is None and event.changes["source"] == [None, "daily job"]
    assert event.changes["balance"][1] == str(paid)
    # Still owed, so the job does not archive it again.
    assert jobs.archive_settled_leases(timezone.localdate()) == 0
    assert jobs.run_daily()["leases_restored"] == 0


def test_a_lease_restored_by_hand_is_left_alone_for_30_days(owner, archived, monkeypatch):
    lease, _payment = archived
    lease_services.restore_lease(owner, lease)
    lease.refresh_from_db()
    assert not lease.is_archived and lease.archived_by is None
    event = events(lease, "lease.restore").get()
    assert event.actor == owner.user
    assert jobs.archive_settled_leases(timezone.localdate()) == 0
    later = timezone.now() + datetime.timedelta(days=lease_services.RESTORE_HOLD_DAYS + 1)
    monkeypatch.setattr(timezone, "now", lambda: later)
    assert jobs.archive_settled_leases(timezone.localdate()) == 1
    assert Lease.all_objects.get(pk=lease.pk).is_archived
    # Restoring a lease that is not archived does nothing.
    lease_services.restore_lease(owner, Lease.all_objects.get(pk=lease.pk))
    lease_services.restore_lease(owner, Lease.all_objects.get(pk=lease.pk))
    assert events(lease, "lease.restore").count() == 2


def test_restoring_needs_leases_terminate_in_the_same_organization(owner, prop, archived):
    lease, _payment = archived
    caretaker = add_member(owner.organization, "caretaker", properties=[prop])
    with pytest.raises(PermissionDenied):
        lease_services.restore_lease(caretaker, lease)
    other = make_org(name="Other")
    with pytest.raises(PermissionDenied):
        lease_services.restore_lease(other, lease)
    assert Lease.all_objects.get(pk=lease.pk).is_archived


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def test_the_list_hides_archived_leases_unless_asked(client, owner, prop, archived):
    lease, _payment = archived
    current = make_lease(owner, prop, code="B2")
    login(client, owner)
    url = reverse("leases:list")
    page = client.get(url)
    assert [x.pk for x in page.context["page"]] == [current.pk]
    assert b"Show archived leases" in page.content
    page = client.get(url, {"archived": "on"})
    assert [x.pk for x in page.context["page"]] == [lease.pk]
    assert b"Archived leases" in page.content and b"Back to current leases" in page.content
    assert [x.pk for x in client.get(url, {"archived": "on", "q": "B2"}).context["page"]] == []


def test_the_lease_and_unit_pages_show_it_and_restore_it(client, owner, archived):
    lease, _payment = archived
    login(client, owner)
    detail = reverse("leases:detail", args=[lease.public_id])
    page = client.get(detail)
    assert b"by the daily job" in page.content and b'value="restore"' in page.content
    unit_page = client.get(reverse("properties:unit", args=[lease.unit.public_id]))
    assert lease in unit_page.context["leases"] and b"Archived" in unit_page.content
    response = client.post(detail, {"action": "restore"})
    assert response.status_code == 302
    lease.refresh_from_db()
    assert not lease.is_archived
    assert b'value="restore"' not in client.get(detail).content


def test_a_leasing_agent_sees_the_lease_but_cannot_restore_it(client, owner, prop, archived):
    lease, _payment = archived
    agent = add_member(owner.organization, "leasing_agent", properties=[prop])
    login(client, agent)
    detail = reverse("leases:detail", args=[lease.public_id])
    page = client.get(detail)
    assert page.status_code == 200 and b"Archived on" in page.content
    assert b'value="restore"' not in page.content
    client.post(detail, {"action": "restore"})
    assert Lease.all_objects.get(pk=lease.pk).is_archived
