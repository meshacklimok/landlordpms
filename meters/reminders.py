"""A nudge near the end of the month to whoever reads the meters, with the rooms not read yet.

Readings taken in a month are billed on the next month's invoices (D-057), so an unread meter delays a tenant's
water or power charge. From REMIND_FROM_DAY to the last day of the month, each person who may record readings on a
property is told once a day how many of its meters have no reading dated this month. Meters the landlord pays,
prepaid meters, archived meters and meters whose rooms are empty are never counted (as in D-146).
"""

import calendar
import datetime

from accounts.models import Organization
from notifications.delivery import notify, staff_with
from properties.models import Property

from . import services
from .models import Meter, MeterReading

REMIND_FROM_DAY = 25


def in_window(today: datetime.date) -> bool:
    return today.day >= REMIND_FROM_DAY


def unread_this_month(prop: Property, today: datetime.date) -> list[Meter]:
    """The property's billed meters, serving a room let this month, with no live reading dated this month."""
    first = today.replace(day=1)
    last = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    meters = list(Meter.objects.filter(property=prop, kind=Meter.Kind.POSTPAID).prefetch_related("served__unit"))
    read = set(MeterReading.objects.filter(meter__in=meters, status__in=MeterReading.LIVE, read_on__gte=first)
               .values_list("meter_id", flat=True))
    return [m for m in meters
            if m.pk not in read and not m.landlord_pays
            and services.covering_leases([mu.unit for mu in m.served.all()], first, last).exists()]


def run_daily(today: datetime.date) -> dict[str, int]:
    """In-app, to each `meters.record` holder, for each of their properties with meters left to read."""
    if not in_window(today):
        return {"meter_reminders": 0}
    sent = 0
    for org in Organization.objects.all():
        for prop in Property.objects.filter(organization=org):
            unread = unread_this_month(prop, today)
            if not unread:
                continue
            for user in staff_with(org, "meters.record", prop):
                if notify(org, "meters_unread", user=user, context={"count": len(unread), "property": prop.name},
                          dedupe_key=f"meters_unread:{today.isoformat()}:{prop.pk}:{user.pk}"):
                    sent += 1
    return {"meter_reminders": sent}
