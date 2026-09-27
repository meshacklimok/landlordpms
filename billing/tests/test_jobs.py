"""The daily job: billing, moved-out tenants and archiving settled leases."""

import datetime

import pytest
from django.core.management import call_command
from django.utils import timezone

from accounts.tests.factories import make_org, make_property
from audit.models import AuditEvent
from billing import deposits, invoicing, jobs
from billing.models import Invoice
from leases import services as lease_services
from leases.models import Lease
from tenants.models import Tenant

from .test_invoicing import FEB, MAR, bill, make_lease

pytestmark = pytest.mark.django_db

D = datetime.date
DAY = datetime.timedelta(days=1)


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def prop(owner):
    return make_property(owner.organization)


def settle(owner, lease):
    """Clears what is owed through the deposit, the only credit before Phase 4 payments."""
    owed = invoicing.lease_balance(lease)
    deposits.record_received(owner, lease, amount=owed, entry_date=lease.start_date)
    deposits.deduct(owner, lease, amount=owed, reason="Final rent", apply_to_balance=True)


def test_a_tenant_becomes_former_the_day_after_moving_out(owner, prop):
    today = timezone.localdate()
    lease = make_lease(owner, prop)
    lease_services.end_lease(owner, lease, ended_on=today, reason="Moved out")
    tenant = lease.primary_tenant
    tenant.refresh_from_db()
    assert tenant.status == Tenant.Status.ACTIVE  # still there today
    assert jobs.sync_moved_out_tenants(today) == 0
    assert jobs.sync_moved_out_tenants(today + DAY) == 1
    tenant.refresh_from_db()
    assert tenant.status == Tenant.Status.FORMER


def test_settled_closed_leases_are_archived(owner, prop):
    today = timezone.localdate()
    lease = make_lease(owner, prop)
    bill(lease, FEB)
    bill(lease, MAR)
    lease_services.end_lease(owner, lease, ended_on=D(2026, 3, 10), reason="Moved out")
    assert not jobs.is_settled(lease)  # March is owed
    settle(owner, lease)
    assert jobs.is_settled(lease)
    assert jobs.archive_settled_leases(today) == 1
    assert Lease.all_objects.get(pk=lease.pk).is_archived
    assert AuditEvent.objects.filter(action="lease.archive", object_id=str(lease.public_id)).exists()


def test_a_lease_is_not_archived_while_money_or_a_final_bill_is_outstanding(owner, prop):
    today = timezone.localdate()
    held = make_lease(owner, prop, code="H1")
    deposits.record_received(owner, held, amount=5000, entry_date=held.start_date)
    lease_services.end_lease(owner, held, ended_on=D(2026, 1, 20), reason="Moved out")

    unbilled = make_lease(owner, prop, code="U1")
    bill(unbilled, FEB)
    lease_services.end_lease(owner, unbilled, ended_on=D(2026, 3, 10), reason="Moved out")
    settle(owner, unbilled)  # February is paid, but March was never billed

    never = make_lease(owner, prop, code="N1")
    lease_services.end_lease(owner, never, ended_on=D(2026, 1, 20), reason="Closed before go-live")

    assert jobs.archive_settled_leases(today) == 1
    assert [x.is_archived for x in Lease.all_objects.filter(pk__in=[held.pk, unbilled.pk, never.pk])
            .order_by("pk")] == [False, False, True]
    assert jobs.archive_settled_leases(today) == 0


def test_the_daily_command_runs_everything(owner, prop, capsys):
    make_lease(owner, prop)
    call_command("billing_daily")
    out = capsys.readouterr().out
    assert "organizations: 1" in out and "errors: 0" in out and "leases archived: 0" in out
    assert Invoice.objects.exists()
