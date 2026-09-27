"""Payments read queries (doc 11 §9)."""

from django.db.models import Q

from accounts.permissions import can
from billing.selectors import _in_scope
from leases.models import LeaseTenant

from .models import Payment

STATUS_FILTERS = {
    "pending": Payment.Status.PENDING_REVIEW,
    "confirmed": Payment.Status.CONFIRMED,
    "reversed": Payment.Status.REVERSED,
}


def visible_payments(membership):
    """Payments on the properties the member may see, archived leases included."""
    if not can(membership, "payments.view"):
        return Payment.objects.none()
    qs = (Payment.objects.for_org(membership.organization)
          .select_related("lease__unit__property", "tenant", "receipt", "payment_account"))
    return _in_scope(membership, qs, "lease__unit__property_id")


def pending_review(membership):
    if not can(membership, "payments.confirm"):
        return Payment.objects.none()
    return visible_payments(membership).filter(status=Payment.Status.PENDING_REVIEW).order_by("paid_at", "pk")


def filter_payments(payments, *, status="", q=""):
    if status in STATUS_FILTERS:
        payments = payments.filter(status=STATUS_FILTERS[status])
    if q:
        payments = payments.filter(
            Q(reference__icontains=q) | Q(lease__number__icontains=q) | Q(lease__unit__code__icontains=q)
            | Q(receipt__number__icontains=q) | Q(tenant__name__icontains=q)
            | Q(lease_id__in=LeaseTenant.objects.filter(tenant__name__icontains=q).values("lease_id")))
    return payments


def for_lease(lease):
    return (Payment.objects.filter(lease=lease).select_related("tenant", "receipt", "recorded_by")
            .prefetch_related("allocations__invoice").order_by("-paid_at", "-pk"))
