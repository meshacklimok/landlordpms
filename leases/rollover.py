"""Term rollover for a hostel (D-189): next semester's bookings for the students who stay.

One renewal draft per chosen lease, through the ordinary renewal (`renew_lease`): same bed or room, same students and
guardian payers, same ongoing charges. Staff then activate the drafts as they would any renewal. A lease that cannot be
renewed (it already has a draft, or the dates do not fit) is skipped with its reason and the rest carry on.
"""

import datetime

from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.models import Membership

from . import services
from .models import Lease


def candidates(prop, today: datetime.date | None = None):
    """Active leases at the hostel with an end date (a semester or a year), the soonest ending first."""
    leases = (Lease.objects.filter(unit__property=prop, status=Lease.Status.ACTIVE, end_date__isnull=False)
              .select_related("unit", "unit__room").prefetch_related("lease_tenants__tenant")
              .order_by("end_date", "unit__code"))
    out = []
    for lease in leases:
        out.append({"lease": lease, "student": lease.primary_tenant, "has_draft": services.open_successor(lease)})
    return out


def roll_over(actor: Membership, prop, *, leases, start_date: datetime.date, end_date: datetime.date | None,
              rent=None, request=None):
    """Drafts a renewal for each lease. Returns (drafts, [(lease, reason), ...])."""
    if end_date is not None and end_date < start_date:
        raise ValidationError({"end_date": "The term cannot end before it starts."})
    made, skipped = [], []
    for lease in leases:
        if lease.unit.property_id != prop.pk:
            continue
        try:
            with transaction.atomic():
                made.append(services.renew_lease(actor, lease, start_date=start_date, end_date=end_date, rent=rent,
                                                 request=request))
        except ValidationError as exc:
            skipped.append((lease, " ".join(exc.messages)))
    return made, skipped


def drafts(prop):
    """Renewal drafts waiting at the hostel, by start date then bed."""
    return (Lease.objects.filter(unit__property=prop, status=Lease.Status.DRAFT, previous_lease__isnull=False)
            .select_related("unit", "previous_lease").prefetch_related("lease_tenants__tenant")
            .order_by("start_date", "unit__code"))


def activate_drafts(actor: Membership, prop, *, leases, request=None):
    """Activates each renewal draft in its own atomic block. Returns (activated, [(lease, reason), ...])."""
    done, skipped = [], []
    for lease in leases:
        if lease.unit.property_id != prop.pk or lease.previous_lease_id is None:
            continue
        try:
            with transaction.atomic():
                done.append(services.activate_lease(actor, lease, request=request))
        except ValidationError as exc:
            skipped.append((lease, " ".join(exc.messages)))
    return done, skipped
