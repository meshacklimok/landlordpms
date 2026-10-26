"""What a tenant pays for themselves: their own electricity or water notes, and the water the landlord bills (D-219).

Own entries belong to the lease and are read only through the tenant's portal. Nothing here touches invoices.
"""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext as _

from meters.models import MeterCharge
from reports.metrics import month_start

from .models import OwnUse

MONTHS = 12
MAX_AMOUNT = Decimal("10000000")
MAX_VALUE = Decimal("999999999")


def _decimal(raw, label: str, *, ceiling: Decimal) -> Decimal | None:
    text = str(raw or "").strip().replace(",", "")
    if not text:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise ValidationError(_("%(label)s must be a number.") % {"label": label}) from None
    if not value.is_finite() or value < 0 or value > ceiling:
        raise ValidationError(_("%(label)s is not a sensible figure.") % {"label": label})
    return value


def add_entry(user, lease, *, utility, kind, on, value="", amount="", note="") -> OwnUse:
    """Saves one note. A reading needs the meter number; a top-up needs units or an amount."""
    if utility not in OwnUse.Utility.values or kind not in OwnUse.Kind.values:
        raise ValidationError(_("Choose electricity or water, and what you are adding."))
    today = timezone.localdate()
    if not on or on > today or on < today - datetime.timedelta(days=366 * 3):
        raise ValidationError(_("Enter a date in the past three years, not in the future."))
    number = _decimal(value, _("The reading") if kind == OwnUse.Kind.READING else _("The units"), ceiling=MAX_VALUE)
    paid = _decimal(amount, _("The amount"), ceiling=MAX_AMOUNT)
    if kind == OwnUse.Kind.READING and number is None:
        raise ValidationError(_("Enter what the meter shows."))
    if kind == OwnUse.Kind.TOPUP and number is None and paid is None:
        raise ValidationError(_("Enter the amount you paid, the units you got, or both."))
    return OwnUse.objects.create(organization=lease.organization, lease=lease, utility=utility, kind=kind, on=on,
                                 value=number, amount=paid, note=(note or "").strip()[:200], created_by=user)


def delete_entry(entry: OwnUse) -> None:
    entry.delete()


@dataclass
class OwnMonth:
    month: datetime.date
    units: Decimal | None = None
    cost: Decimal | None = None
    entries: int = 0


@dataclass
class OwnSummary:
    utility: str
    label: str
    unit_label: str
    months: list[OwnMonth]
    entries: list[OwnUse]
    this_month: OwnMonth | None = None
    last_month: OwnMonth | None = None
    change_pct: int | None = None
    price_per_unit: Decimal | None = None
    days_left: int | None = None
    has_data: bool = False
    notes: list[str] = field(default_factory=list)


def _month_list(today: datetime.date) -> list[datetime.date]:
    first = month_start(today)
    out = []
    for back in range(MONTHS - 1, -1, -1):
        year, month = divmod(first.year * 12 + first.month - 1 - back, 12)
        out.append(datetime.date(year, month + 1, 1))
    return out


def summarize(lease, utility: str, today: datetime.date | None = None) -> OwnSummary:
    """Twelve months of what the tenant used and paid for one utility.

    Units come from the differences between readings when there are readings, else from units bought. Each
    difference counts in the month of the later reading. Money is whatever amounts the tenant entered."""
    today = today or timezone.localdate()
    entries = list(OwnUse.objects.filter(lease=lease, utility=utility).order_by("on", "pk"))
    labels = dict(OwnUse.Utility.choices)
    unit_label = "kWh" if utility == OwnUse.Utility.ELECTRICITY else "m³"
    buckets = {m: OwnMonth(month=m) for m in _month_list(today)}

    readings = [e for e in entries if e.kind == OwnUse.Kind.READING]
    by_reading = len(readings) >= 2
    if by_reading:
        for before, after in zip(readings, readings[1:], strict=False):
            used = after.value - before.value
            bucket = buckets.get(month_start(after.on))
            if bucket is not None and used >= 0:
                bucket.units = (bucket.units or Decimal("0")) + used
    for entry in entries:
        bucket = buckets.get(month_start(entry.on))
        if bucket is None:
            continue
        bucket.entries += 1
        if entry.amount is not None:
            bucket.cost = (bucket.cost or Decimal("0")) + entry.amount
        if not by_reading and entry.kind == OwnUse.Kind.TOPUP and entry.value is not None:
            bucket.units = (bucket.units or Decimal("0")) + entry.value

    months = list(buckets.values())
    this_month, last_month = months[-1], months[-2]
    change = None
    if last_month.units and this_month.units is not None and last_month.units > 0:
        change = int(round((this_month.units - last_month.units) / last_month.units * 100))
    total_units = sum((m.units for m in months if m.units), Decimal("0"))
    total_cost = sum((m.cost for m in months if m.cost), Decimal("0"))
    price = (total_cost / total_units).quantize(Decimal("0.01")) if total_units > 0 and total_cost > 0 else None
    return OwnSummary(utility=utility, label=str(labels[utility]), unit_label=unit_label, months=months,
                      entries=list(reversed(entries))[:20], this_month=this_month, last_month=last_month,
                      change_pct=change, price_per_unit=price, has_data=bool(entries),
                      days_left=_days_left(entries, today))


def _days_left(entries, today: datetime.date) -> int | None:
    """For a tenant who buys tokens: days until the last top-up runs out at their usual daily use, else None.

    Needs at least two top-ups with units. Usual use is the units bought before the latest one over the days
    between the first and the latest top-up."""
    buys = [e for e in entries if e.kind == OwnUse.Kind.TOPUP and e.value]
    if len(buys) < 2:
        return None
    span = (buys[-1].on - buys[0].on).days
    spent = sum((b.value for b in buys[:-1]), Decimal("0"))
    if span <= 0 or spent <= 0:
        return None
    daily = spent / span
    runs_out = buys[-1].on + datetime.timedelta(days=int(buys[-1].value / daily))
    return (runs_out - today).days


@dataclass
class BilledMonth:
    month: datetime.date
    amount: Decimal
    lines: list[MeterCharge]


def billed_water(lease, today: datetime.date | None = None) -> list[BilledMonth]:
    """Water (and any other metered charge) the landlord bills this lease, a row for each of the last twelve
    months, newest first, with the lines behind it."""
    today = today or timezone.localdate()
    months = _month_list(today)
    charges = (MeterCharge.objects.filter(lease=lease, cancelled_at__isnull=True, billing_month__gte=months[0],
                                          billing_month__lte=months[-1], reading__status="APPROVED")
               .select_related("reading", "reading__meter", "reading__previous").order_by("billing_month", "pk"))
    grouped: dict[datetime.date, list[MeterCharge]] = {}
    for charge in charges:
        grouped.setdefault(charge.billing_month, []).append(charge)
    return [BilledMonth(month=m, amount=sum((c.amount for c in grouped[m]), Decimal("0")), lines=grouped[m])
            for m in reversed(months) if m in grouped]
