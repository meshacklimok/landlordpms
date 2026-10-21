"""Garbage pickups (D-161): the crew records, route by route and day by day, which contracts were collected and which
were missed. Collected pickups are billed on per-pickup charges in arrears (billing.invoicing); missed ones are not
charged and are counted so the office can see who has been let down.
"""
import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import require
from audit import services as audit
from properties.models import Property

from .models import Lease, Pickup

LOOK_BACK_DAYS = 62


def route_leases(prop: Property, day: datetime.date) -> list[Lease]:
    """The contracts on this route that were running on the day."""
    leases = (Lease.objects.filter(unit__property=prop, start_date__lte=day).exclude(status=Lease.Status.DRAFT)
              .select_related("unit").prefetch_related("lease_tenants__tenant").order_by("unit__code"))
    return [lease for lease in leases if lease.effective_end is None or lease.effective_end >= day]


def outcomes(prop: Property, day: datetime.date) -> dict:
    """{lease id: Pickup} already recorded for the route on the day."""
    return {p.lease_id: p for p in Pickup.objects.filter(lease__unit__property=prop, day=day)}


@transaction.atomic
def record(actor: Membership, prop: Property, day: datetime.date, marks: dict, *, request=None) -> dict:
    """Save the day sheet. `marks` is {lease id: "DONE" | "MISSED" | anything else to clear the mark}."""
    require(actor, "meters.record", prop)
    if prop.organization_id != actor.organization_id or prop.organization.org_type != "WASTE":
        raise ValidationError(_("Pickups are recorded by garbage collectors."))
    today = timezone.localdate()
    if day > today:
        raise ValidationError(_("You cannot record a pickup for a day that has not come."))
    if day < today - datetime.timedelta(days=LOOK_BACK_DAYS):
        raise ValidationError(_("That day is too long ago to change."))
    valid = {lease.pk: lease for lease in route_leases(prop, day)}
    counts = {"DONE": 0, "MISSED": 0, "cleared": 0}
    for lease_id, mark in marks.items():
        lease = valid.get(lease_id)
        if lease is None:
            continue
        if mark in (Pickup.Outcome.DONE, Pickup.Outcome.MISSED):
            Pickup.objects.update_or_create(
                lease=lease, day=day,
                defaults={"organization": prop.organization, "outcome": mark, "recorded_by": actor.user})
            counts[mark] += 1
        elif Pickup.objects.filter(lease=lease, day=day).delete()[0]:
            counts["cleared"] += 1
    audit.record("pickups.record", actor=actor.user, organization=actor.organization, obj=prop, request=request,
                 changes={"day": [None, str(day)], **{k: [None, v] for k, v in counts.items() if v}})
    return counts


def missed_by_contract(prop: Property, first: datetime.date, last: datetime.date) -> list[tuple]:
    """[(lease, missed, done)] for contracts with at least one missed pickup in the range, worst first."""
    rows = (Pickup.objects.filter(lease__unit__property=prop, day__range=(first, last))
            .values("lease_id")
            .annotate(missed=Count("pk", filter=Q(outcome=Pickup.Outcome.MISSED)),
                      done=Count("pk", filter=Q(outcome=Pickup.Outcome.DONE)))
            .filter(missed__gt=0).order_by("-missed"))
    leases = Lease.objects.select_related("unit").in_bulk([r["lease_id"] for r in rows])
    return [(leases[r["lease_id"]], r["missed"], r["done"]) for r in rows]
