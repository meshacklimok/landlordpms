"""The daily job: billing plus the lease and tenant housekeeping that depends on the date."""

import datetime

from django.db import transaction
from django.utils import timezone

from audit import services as audit
from core.money import ZERO
from leases.models import Lease, LeaseTenant
from leases.services import sync_tenant_status
from notifications import triggers
from tenants.models import Tenant

from . import deposits, invoicing
from .models import InvoiceLine

CLOSED = (Lease.Status.ENDED, Lease.Status.TERMINATED, Lease.Status.RENEWED)


def sync_moved_out_tenants(today: datetime.date) -> int:
    """Tenants still marked ACTIVE whose last lease closed before today become FORMER (doc 11 §6)."""
    moved_out = LeaseTenant.objects.filter(lease__status__in=CLOSED, lease__ended_on__lt=today).values("tenant_id")
    changed = 0
    for tenant in Tenant.all_objects.filter(status=Tenant.Status.ACTIVE, pk__in=moved_out):
        if sync_tenant_status(tenant, today) != Tenant.Status.ACTIVE:
            changed += 1
    return changed


def is_settled(lease: Lease) -> bool:
    """Nothing owed either way, no deposit held, and nothing left to bill for its last month.

    A lease that was never billed (it closed before go-live) has nothing left to bill.
    """
    if invoicing.lease_balance(lease) != ZERO or deposits.held(lease) != ZERO:
        return False
    lines = InvoiceLine.objects.filter(lease=lease, is_void=False)
    if not lines.exists():
        return True
    last = invoicing.month_start(lease.ended_on)
    billed = set(lines.filter(billing_month=last).values_list("charge_type_id", flat=True))
    return all(ln.charge_type.pk in billed for ln in invoicing.lines_for_month(lease, last))


def archive_settled_leases(today: datetime.date) -> int:
    """Closed leases whose tenants have left and whose money is settled leave the working lists."""
    archived = 0
    candidates = Lease.objects.filter(status__in=CLOSED, ended_on__lt=today).select_related("organization")
    for lease in candidates:
        with transaction.atomic():
            lease = Lease.objects.select_for_update().filter(pk=lease.pk).first()
            if lease is None or not is_settled(lease):
                continue
            lease.archive(None)
            audit.record("lease.archive", actor=None, organization=lease.organization, obj=lease,
                         changes={"archived": [False, True], "source": [None, "daily job"]})
            archived += 1
    return archived


def run_daily(today: datetime.date | None = None) -> dict[str, int]:
    today = today or timezone.localdate()
    counts = invoicing.run_scheduled(today)
    counts["tenants_moved_out"] = sync_moved_out_tenants(today)
    counts["leases_archived"] = archive_settled_leases(today)
    counts.update(triggers.send_reminders(today))
    return counts
