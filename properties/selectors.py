"""Unit list queries: occupancy filters and status counts done in the database (doc 11 §20)."""

from django.db.models import Count, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.permissions import visible_properties
from leases.services import with_occupancy

from .models import Property, Unit

Manual = Unit.ManualStatus

# Filter key -> (label, condition on a with_occupancy queryset). "Vacant" means ready to let.
STATUS_FILTERS = {
    "vacant": (_("Available"), Q(is_occupied=False, manual_status=Manual.NORMAL)),
    "occupied": (_("Occupied"), Q(is_occupied=True)),
    "reserved": (_("Reserved"), Q(is_occupied=False, manual_status=Manual.RESERVED)),
    "maintenance": (_("Under maintenance"), Q(is_occupied=False, manual_status=Manual.UNDER_MAINTENANCE)),
    "inactive": (_("Inactive"), Q(is_occupied=False, manual_status=Manual.INACTIVE)),
}


def visible_units(membership):
    """Live units on live properties the member may see, with `is_occupied` annotated."""
    props = visible_properties(membership, Property.objects.all())
    return with_occupancy(Unit.objects.for_org(membership.organization).filter(property__in=props))


def filter_units(units, *, status="", q=""):
    if status in STATUS_FILTERS:
        units = units.filter(STATUS_FILTERS[status][1])
    if q:
        units = units.filter(Q(code__icontains=q) | Q(payment_reference__icontains=q) | Q(type_label__icontains=q))
    return units


def status_counts(units) -> list[tuple[str, str, int]]:
    """(key, label, count) for the non-empty statuses, in one query."""
    totals = units.order_by().aggregate(**{key: Count("pk", filter=cond) for key, (_label, cond) in
                                          STATUS_FILTERS.items()})
    return [(key, STATUS_FILTERS[key][0], n) for key, n in totals.items() if n]


def is_available_to_let(unit: Unit) -> bool:
    """Free now, not promised to anyone later, and not held back by its manual status."""
    from leases.models import Lease

    if unit.manual_status != Manual.NORMAL:
        return False
    today = timezone.localdate()
    taken = Lease.objects.filter(unit=unit).exclude(status=Lease.Status.DRAFT).filter(
        Q(status=Lease.Status.ACTIVE) | Q(ended_on__gte=today))
    return not taken.exists()


def shared_unit(token: str):
    """The live unit behind a public link, or None. Frozen, lapsed or archived organizations show nothing."""
    from accounts.models import Organization

    return (Unit.objects.filter(share_token=token, property__archived_at__isnull=True,
                                organization__status=Organization.Status.ACTIVE,
                                organization__archived_at__isnull=True)
            .select_related("property", "organization", "shared_by").first())
