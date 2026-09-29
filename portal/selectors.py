"""What a tenant may see in the portal (D-055, doc 11 §18).

Hard rule: every query starts from `own_lease_ids(user)`. Nothing here takes an organization or
membership: a portal user's reach is their own live accounts and nothing else.
"""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from billing import selectors as billing
from billing.invoicing import lease_balance
from billing.models import Invoice
from core.money import ZERO
from leases.models import Lease, LeaseTenant
from maintenance.models import MaintenancePhoto, MaintenanceRequest
from mpesa import paylinks
from payments.models import Payment
from reports import metrics

from .models import TenantAccount

STATEMENT_MONTHS = 12
PAYMENTS_SHOWN = 24
REQUESTS_SHOWN = 10


def live_accounts(user):
    """The user's accounts that still work: not revoked, tenant and organization not archived."""
    if user is None or not user.is_authenticated:
        return TenantAccount.objects.none()
    return TenantAccount.objects.filter(user=user, revoked_at__isnull=True, tenant__archived_at__isnull=True,
                                        organization__archived_at__isnull=True)


def has_access(user) -> bool:
    return live_accounts(user).exists()


def own_lease_ids(user) -> list[int]:
    """The leases of the user's tenant records, drafts and archived leases left out."""
    tenant_ids = live_accounts(user).values("tenant_id")
    return list(LeaseTenant.objects.filter(tenant_id__in=tenant_ids, lease__archived_at__isnull=True)
                .exclude(lease__status=Lease.Status.DRAFT).values_list("lease_id", flat=True).distinct())


def own_leases(user):
    return (Lease.all_objects.filter(pk__in=own_lease_ids(user)).select_related("unit__property", "organization")
            .order_by("organization__name", "-start_date", "-pk"))


def own_lease(user, public_id) -> Lease | None:
    return own_leases(user).filter(public_id=public_id).first()


def own_payment(user, public_id) -> Payment | None:
    """A confirmed payment on one of the user's leases (a reversed one no longer has a receipt to show)."""
    return (Payment.objects.filter(lease_id__in=own_lease_ids(user), public_id=public_id,
                                   status=Payment.Status.CONFIRMED).select_related("receipt").first())


@dataclass
class LeaseSummary:
    lease: Lease
    balance: Decimal = ZERO
    open_invoices: list[Invoice] = field(default_factory=list)
    pay_url: str = ""

    @property
    def currency(self) -> str:
        return self.lease.currency

    @property
    def next_due(self) -> Invoice | None:
        return self.open_invoices[0] if self.open_invoices else None

    @property
    def in_credit(self) -> bool:
        return self.balance < 0

    @property
    def overdue(self) -> Decimal:
        today = timezone.localdate()
        return sum((i.outstanding for i in self.open_invoices if i.overdue_after and i.overdue_after < today), ZERO)


def summary(lease: Lease) -> LeaseSummary:
    """Call only with a lease from `own_leases`."""
    link = paylinks.link_for(lease)
    return LeaseSummary(
        lease=lease, balance=lease_balance(lease),
        open_invoices=list(Invoice.objects.filter(lease=lease, status__in=Invoice.OPEN).order_by("due_date", "pk")),
        pay_url=paylinks.url(link) if link else "")


def statement(lease: Lease, today: datetime.date | None = None) -> dict:
    """The last 12 months, this one included, with what was owed before brought forward."""
    start = metrics.add_months(metrics.month_start(today or timezone.localdate()), 1 - STATEMENT_MONTHS)
    return billing.statement(lease, start=start)


def payments(lease: Lease) -> list[Payment]:
    return list(Payment.objects.filter(lease=lease, status=Payment.Status.CONFIRMED).select_related("receipt")
                .order_by("-paid_at", "-pk")[:PAYMENTS_SHOWN])


# ---------------------------------------------------------------------------
# Repairs (D-068 item 7)
# ---------------------------------------------------------------------------


def own_requests(user):
    """Maintenance requests the tenant follows: those on their leases that they or staff tied to the lease."""
    return (MaintenanceRequest.objects.filter(lease_id__in=own_lease_ids(user))
            .select_related("unit__property", "organization", "lease").order_by("-created_at", "-pk"))


def own_request(user, public_id):
    return own_requests(user).filter(public_id=public_id).first()


def tenant_updates(req) -> list:
    """Status changes, their comments and the notes staff chose to share; never internal notes."""
    return list(req.updates.filter(shared_with_tenant=True).select_related("by"))


def tenant_photos(req, user) -> list:
    return list(req.photos.filter(Q(shared_with_tenant=True) | Q(uploaded_by=user)))


def own_photo(user, public_id):
    return (MaintenancePhoto.objects.filter(request__lease_id__in=own_lease_ids(user), public_id=public_id)
            .filter(Q(shared_with_tenant=True) | Q(uploaded_by=user)).first())
