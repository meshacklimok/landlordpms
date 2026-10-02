"""Payments read queries (doc 11 §9)."""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Count, DecimalField, Exists, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from accounts.permissions import can
from billing.models import LedgerEntry
from billing.selectors import _in_scope
from core.money import ZERO
from leases.models import Lease, LeaseTenant
from leases.services import visible_leases

from .models import Payment

Status = Payment.Status

# Keys match Payment.state, so a filter, a tab and a badge all mean the same thing.
STATUS_FILTERS = {
    "pending": Q(status=Status.PENDING_REVIEW),
    "confirmed": Q(status=Status.CONFIRMED),
    "rejected": Q(status=Status.REVERSED, confirmed_at__isnull=True),
    "reversed": Q(status=Status.REVERSED, confirmed_at__isnull=False),
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
    return (visible_payments(membership).filter(status=Status.PENDING_REVIEW)
            .select_related("recorded_by").order_by("paid_at", "pk"))


def filter_payments(payments, *, status="", q="", method="", property=None, date_from=None, date_to=None):
    if status in STATUS_FILTERS:
        payments = payments.filter(STATUS_FILTERS[status])
    if method:
        payments = payments.filter(method=method)
    if property is not None:
        payments = payments.filter(lease__unit__property=property)
    if date_from:
        payments = payments.filter(paid_at__gte=date_from)
    if date_to:
        payments = payments.filter(paid_at__lte=date_to)
    if q:
        payments = payments.filter(
            Q(reference__icontains=q) | Q(lease__number__icontains=q) | Q(lease__unit__code__icontains=q)
            | Q(lease__unit__payment_reference__icontains=q)
            | Q(receipt__number__icontains=q) | Q(tenant__name__icontains=q)
            | Q(lease_id__in=LeaseTenant.objects.filter(tenant__name__icontains=q).values("lease_id")))
    return payments


# ---------------------------------------------------------------------------
# Totals
# ---------------------------------------------------------------------------


@dataclass
class Total:
    """How many payments, and how much money per currency (leases may differ in currency)."""

    count: int = 0
    amounts: dict[str, Decimal] = field(default_factory=dict)


def totals_by_state(payments) -> dict[str, Total]:
    """One query: count and sum for every state, plus "all". Every key is always present."""
    aggregates = {}
    for key, condition in {"all": Q(), **STATUS_FILTERS}.items():
        aggregates[f"{key}_n"] = Count("pk", filter=condition)
        aggregates[f"{key}_sum"] = Sum("amount", filter=condition)
    totals = {key: Total() for key in ("all", *STATUS_FILTERS)}
    for row in payments.order_by().values("lease__currency").annotate(**aggregates):
        for key, total in totals.items():
            total.count += row[f"{key}_n"]
            if row[f"{key}_sum"]:
                total.amounts[row["lease__currency"]] = row[f"{key}_sum"]
    return totals


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------


def same_reference(organization, reference: str):
    """Live (not rejected or reversed) payments in the organization that used this reference."""
    reference = (reference or "").strip()
    if not reference:
        return Payment.objects.none()
    return (Payment.objects.for_org(organization).filter(reference__iexact=reference)
            .exclude(status=Status.REVERSED))


def duplicates_of(payment: Payment):
    return same_reference(payment.organization, payment.reference).exclude(pk=payment.pk)


def with_duplicate_flag(payments):
    """Adds `has_duplicate`: another live payment in the organization shares the reference."""
    others = (Payment.objects.filter(organization=OuterRef("organization"), reference__iexact=OuterRef("reference"))
              .exclude(pk=OuterRef("pk")).exclude(status=Status.REVERSED).exclude(reference=""))
    return payments.annotate(has_duplicate=Exists(others))


# ---------------------------------------------------------------------------
# Leases to record against
# ---------------------------------------------------------------------------


def payable_leases(membership, *, q=""):
    """Issued, unarchived leases the member may record on, with `balance` annotated; biggest debt first."""
    balance = (LedgerEntry.objects.filter(lease=OuterRef("pk")).order_by().values("lease")
               .annotate(s=Sum("amount")).values("s"))
    qs = (visible_leases(membership, Lease.objects.exclude(status=Lease.Status.DRAFT))
          .select_related("unit__property").prefetch_related("lease_tenants__tenant")
          .annotate(balance=Coalesce(Subquery(balance), Value(ZERO),
                                     output_field=DecimalField(max_digits=14, decimal_places=2))))
    if q:
        qs = qs.filter(
            Q(number__icontains=q) | Q(unit__code__icontains=q) | Q(unit__payment_reference__icontains=q)
            | Q(unit__property__name__icontains=q)
            | Q(pk__in=LeaseTenant.objects.filter(tenant__name__icontains=q).values("lease_id")))
    return qs.order_by("-balance", "unit__property__name", "unit__code")


def for_lease(lease):
    return (Payment.objects.filter(lease=lease).select_related("tenant", "receipt", "recorded_by")
            .prefetch_related("allocations__invoice").order_by("-paid_at", "-pk"))


def period_presets(today: datetime.date) -> list[tuple[str, datetime.date, datetime.date]]:
    """Quick date ranges for the list: (label key, from, to)."""
    month_start = today.replace(day=1)
    last_month_end = month_start - datetime.timedelta(days=1)
    return [
        ("this_month", month_start, today),
        ("last_month", last_month_end.replace(day=1), last_month_end),
        ("this_year", today.replace(month=1, day=1), today),
    ]
