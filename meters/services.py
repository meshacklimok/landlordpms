"""Metered water (D-057) and electricity (D-070). Views stay thin; the rules live here."""

import datetime
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import can, require, visible_properties
from audit import services as audit
from billing import invoicing
from core.money import ZERO
from inspections import photos
from leases.models import Lease
from properties.models import Property, Unit

from . import split as splitting
from .models import Meter, MeterCharge, MeterReading, MeterUnit, OfflineRound

Flag = MeterReading.Flag
Status = MeterReading.Status

LONG_GAP_DAYS = 45
# "Much higher than usual": over HIGH_TIMES the usual use for the days, and at least HIGH_MIN over it
# (5 m³ of water, 50 kWh of electricity; D-070 item 4).
HIGH_TIMES = 3
HIGH_MIN = {"WATER": Decimal("5"), "ELECTRICITY": Decimal("50")}
HISTORY = 3
M3 = Decimal("0.001")
MAX_VALUE = Decimal("999999999.999")


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def visible_meters(membership: Membership, queryset=None):
    qs = (queryset if queryset is not None else Meter.objects.all()).for_org(membership.organization)
    return qs.filter(property__in=visible_properties(membership, Property.all_objects.all()))


def visible_readings(membership: Membership, queryset=None):
    qs = (queryset if queryset is not None else MeterReading.objects.all()).for_org(membership.organization)
    return qs.filter(meter__property__in=visible_properties(membership, Property.all_objects.all()))


def to_approve(membership: Membership):
    """Submitted readings on the properties where the member may approve."""
    qs = visible_readings(membership).filter(status=Status.SUBMITTED)
    ids = [p.pk for p in visible_properties(membership, Property.all_objects.all())
           if can(membership, "meters.approve", p)]
    return qs.filter(meter__property_id__in=ids)


def _check(actor: Membership, prop: Property, capability: str) -> None:
    if prop.organization_id != actor.organization_id:
        raise PermissionDenied(_("That record belongs to another organization."))
    require(actor, capability, prop)


def _audit(action, actor, obj, request, changes=None):
    audit.record(action, actor=actor.user, organization=actor.organization, obj=obj, request=request,
                 changes=changes or {})


# ---------------------------------------------------------------------------
# Meters
# ---------------------------------------------------------------------------


def _clean_amount(value, field) -> Decimal:
    try:
        amount = Decimal(str(value if value not in (None, "") else "0").replace(",", "").strip())
    except InvalidOperation:
        raise ValidationError({field: _("Enter an amount, like 150 or 150.50.")}) from None
    if amount < 0:
        raise ValidationError({field: _("The amount cannot be negative.")})
    if amount != amount.quantize(Decimal("0.01")) or amount > Decimal("99999999"):
        raise ValidationError({field: _("Use at most 2 decimal places.")})
    return amount.quantize(Decimal("0.01"))


def _clean_meter(prop: Property, *, label, serial, kind, split, rate, minimum_charge, units,
                 utility=Meter.Utility.WATER, payer=Meter.Payer.TENANTS,
                 vacant_share=Meter.VacantShare.OCCUPIED) -> tuple[dict, list]:
    label = (label or "").strip()[:60]
    if not label:
        raise ValidationError({"label": _("Give the meter a label, e.g. A1 water.")})
    if utility not in Meter.Utility.values:
        raise ValidationError({"utility": _("Choose water or electricity.")})
    if kind not in Meter.Kind.values:
        raise ValidationError({"kind": _("Choose postpaid or prepaid.")})
    if split not in Meter.Split.values:
        raise ValidationError({"split": _("Choose how a shared meter is split.")})
    if payer not in Meter.Payer.values:
        raise ValidationError({"payer": _("Choose who pays.")})
    if vacant_share not in Meter.VacantShare.values:
        raise ValidationError({"vacant_share": _("Choose who pays the empty rooms' share.")})
    served = []
    for unit, weight in units:
        if unit.property_id != prop.pk:
            raise ValidationError({"units": _("Every unit must be on this property.")})
        try:
            weight = Decimal(str(weight or "1"))
        except InvalidOperation:
            weight = Decimal("0")
        if not Decimal("0") < weight <= Decimal("9999"):
            raise ValidationError({"units": _("Weights must be above zero.")})
        served.append((unit, weight.quantize(Decimal("0.01"))))
    if not served:
        raise ValidationError({"units": _("Choose the unit or units this meter serves.")})
    fields = {"utility": utility, "label": label, "serial": (serial or "").strip()[:40], "kind": kind, "split": split,
              "payer": payer, "vacant_share": vacant_share,
              "rate": _clean_amount(rate, "rate"), "minimum_charge": _clean_amount(minimum_charge, "minimum_charge")}
    if kind == Meter.Kind.POSTPAID and fields["rate"] <= 0:
        raise ValidationError({"rate": _("Enter the price per kWh.") if utility == Meter.Utility.ELECTRICITY
                               else _("Enter the price per m³.")})
    return fields, served


def _set_units(meter: Meter, served: list) -> None:
    MeterUnit.objects.filter(meter=meter).exclude(unit__in=[u for u, _w in served]).delete()
    for unit, weight in served:
        MeterUnit.objects.update_or_create(meter=meter, unit=unit, defaults={"weight": weight})


def _describe_units(served) -> str:
    return ", ".join(f"{u.code}×{w}" for u, w in served)


@transaction.atomic
def create_meter(actor: Membership, prop: Property, *, units, request=None, **fields) -> Meter:
    _check(actor, prop, "meters.manage")
    if prop.is_archived:
        raise ValidationError(_("This property is archived."))
    fields, served = _clean_meter(prop, units=units, **fields)
    meter = Meter.objects.create(organization=prop.organization, property=prop, created_by=actor.user, **fields)
    _set_units(meter, served)
    _audit("meters.create", actor, meter, request, {**{k: [None, str(v)] for k, v in fields.items()},
                                                    "units": [None, _describe_units(served)]})
    return meter


@transaction.atomic
def update_meter(actor: Membership, meter: Meter, *, units, request=None, **fields) -> Meter:
    _check(actor, meter.property, "meters.manage")
    fields, served = _clean_meter(meter.property, units=units, **fields)
    before_units = _describe_units([(mu.unit, mu.weight) for mu in meter.served.select_related("unit")])
    changes = {k: [str(getattr(meter, k)), str(v)] for k, v in fields.items() if getattr(meter, k) != v}
    for k, v in fields.items():
        setattr(meter, k, v)
    meter.save()
    _set_units(meter, served)
    after_units = _describe_units(served)
    if after_units != before_units:
        changes["units"] = [before_units, after_units]
    if changes:
        _audit("meters.edit", actor, meter, request, changes)
    return meter


@transaction.atomic
def archive_meter(actor: Membership, meter: Meter, *, request=None) -> None:
    _check(actor, meter.property, "meters.manage")
    if meter.readings.filter(status=Status.SUBMITTED).exists():
        raise ValidationError(_("Approve or reject its waiting readings first."))
    meter.archive(actor.user)
    _audit("meters.archive", actor, meter, request, {"archived": [False, True]})


@transaction.atomic
def restore_meter(actor: Membership, meter: Meter, *, request=None) -> None:
    _check(actor, meter.property, "meters.manage")
    meter.restore()
    _audit("meters.restore", actor, meter, request, {"archived": [True, False]})


# ---------------------------------------------------------------------------
# Readings
# ---------------------------------------------------------------------------


def latest_reading(meter: Meter, *, before: MeterReading | None = None) -> MeterReading | None:
    """The newest live reading (waiting or approved) of the meter."""
    qs = meter.readings.filter(status__in=MeterReading.LIVE)
    if before is not None:
        qs = qs.exclude(pk=before.pk).filter(read_on__lt=before.read_on)
    return qs.order_by("-read_on", "-pk").first()


def parse_value(value) -> Decimal:
    text = str(value if value is not None else "").replace(",", "").strip()
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise ValidationError({"value": _("Enter the number on the meter, like 1234.5.")}) from None
    if not number.is_finite() or number < 0:
        raise ValidationError({"value": _("Enter the number on the meter, like 1234.5.")})
    if number != number.quantize(M3) or number > MAX_VALUE:
        raise ValidationError({"value": _("Use at most 3 decimal places.")})
    return number.quantize(M3)


def _occupied_between(units, start: datetime.date, end: datetime.date) -> bool:
    return covering_leases(units, start, end).exists()


def covering_leases(units, start: datetime.date, end: datetime.date):
    """Issued, live leases on these units that cover at least one day of start..end (as billing does)."""
    return (Lease.objects.filter(unit__in=units).exclude(status=Lease.Status.DRAFT).filter(start_date__lte=end)
            .annotate(last_day=Coalesce("ended_on", "end_date"))
            .filter(Q(last_day__isnull=True) | Q(last_day__gte=start)))


def usual_daily_use(meter: Meter, up_to: datetime.date) -> Decimal | None:
    """Average use a day (m³ or kWh) over the last approved periods ending by `up_to`, or None without history."""
    periods = list(meter.readings.filter(status=Status.APPROVED, is_baseline=False, read_on__lte=up_to)
                   .select_related("previous").order_by("-read_on")[:HISTORY])
    used = sum((r.usage for r in periods), Decimal(0))
    days = sum(r.days for r in periods)
    if not periods or days <= 0:
        return None
    return used / days


def flags_for(meter: Meter, previous: MeterReading | None, read_on: datetime.date, value: Decimal,
              baseline: bool) -> list[str]:
    if baseline or previous is None:
        return []
    flags = []
    usage = value - previous.value
    days = (read_on - previous.read_on).days
    if usage < 0:
        flags.append(Flag.LOWER)
    elif usage == 0 and _occupied_between(meter.units.all(), previous.read_on + datetime.timedelta(days=1), read_on):
        flags.append(Flag.ZERO)
    elif usage > 0:
        daily = usual_daily_use(meter, previous.read_on)
        if daily is not None:
            expected = daily * days
            if usage > expected * HIGH_TIMES and usage - expected >= HIGH_MIN[meter.utility]:
                flags.append(Flag.HIGH)
    if days > LONG_GAP_DAYS:
        flags.append(Flag.LONG_GAP)
    return [str(f) for f in flags]


@transaction.atomic
def record_reading(actor: Membership, meter: Meter, *, read_on: datetime.date, value, photo=None, note="",
                   replaced: bool = False, units_used=None, today: datetime.date | None = None,
                   request=None) -> MeterReading:
    """Records a reading for approval. The photo is optional (D-057).

    `units_used` stands in for `value` when only the use was written down: the reading is the last one plus
    the units, worked out here under the meter's lock so a reading recorded meanwhile cannot throw it off."""
    _check(actor, meter.property, "meters.record")
    today = today or timezone.localdate()
    meter = Meter.all_objects.select_for_update().get(pk=meter.pk)
    if meter.is_archived:
        raise ValidationError(_("This meter is archived."))
    if read_on is None:
        raise ValidationError({"read_on": _("Enter the reading date.")})
    if read_on > today:
        raise ValidationError({"read_on": _("The reading date cannot be in the future.")})
    note = (note or "").strip()[:300]
    previous = latest_reading(meter)
    if units_used is not None:
        if previous is None or replaced:
            raise ValidationError({"value": _("There is no earlier reading to add the units to. "
                                              "Type the number on the meter.")})
        value = parse_value(previous.value + parse_value(units_used))
        note = f"{note[:260]} · {_('Typed as units used.')}" if note else _("Typed as units used.")
    else:
        value = parse_value(value)
    if previous is not None and read_on <= previous.read_on:
        raise ValidationError({"read_on": _("The last reading was on %(day)s. Date this one after it.")
                               % {"day": previous.read_on.strftime("%d %b %Y")}})
    if replaced and not note:
        raise ValidationError({"note": _("Say what happened to the old meter, e.g. its last reading.")})
    baseline = previous is None or replaced
    fields = {}
    if photo:
        image, _w, _h = photos.process(photo)
        fields["photo"] = image
    try:
        reading = MeterReading.objects.create(
            organization=meter.organization, meter=meter, read_on=read_on, value=value, is_baseline=baseline,
            previous=previous, flags=flags_for(meter, previous, read_on, value, baseline), note=note,
            recorded_by=actor.user, **fields)
    except IntegrityError:
        raise ValidationError({"read_on": _("This meter already has a reading on that day.")}) from None
    _audit("meters.reading_record", actor, reading, request, {
        "meter": [None, meter.label], "read_on": [None, str(read_on)], "value": [None, str(value)],
        **({"baseline": [None, True]} if baseline else {}),
        **({"flags": [None, reading.flags]} if reading.flags else {})})
    return reading


@dataclass
class RoundEntry:
    meter: Meter
    value: str
    photo: object = None
    note: str = ""
    replaced: bool = False
    units_used: str | None = None


def record_round(actor: Membership, read_on: datetime.date, entries: list[RoundEntry], *, today=None,
                 request=None) -> tuple[list[MeterReading], dict[int, list[str]]]:
    """Records a reading for each meter given a value, all or none. Returns (readings, errors by meter pk)."""
    errors: dict[int, list[str]] = {}
    saved: list[MeterReading] = []
    with transaction.atomic():
        for entry in entries:
            try:
                with transaction.atomic():
                    saved.append(record_reading(actor, entry.meter, read_on=read_on, value=entry.value,
                                                photo=entry.photo, note=entry.note, replaced=entry.replaced,
                                                units_used=entry.units_used, today=today, request=request))
            except ValidationError as exc:
                errors[entry.meter.pk] = exc.messages
        if errors:
            transaction.set_rollback(True)
            return [], errors
    return saved, {}


# ---------------------------------------------------------------------------
# What each lease pays
# ---------------------------------------------------------------------------


def fmt_value(value: Decimal) -> str:
    return f"{value:,.3f}"


def fmt_weight(value: Decimal) -> str:
    """2.00 -> "2", 1.50 -> "1.5"."""
    return f"{Decimal(value).normalize():f}"


def billing_month_for(lease: Lease, read_on: datetime.date) -> datetime.date:
    """The month after the reading, or the lease's last month if it ends before then (D-057 item 6)."""
    month = invoicing.add_months(invoicing.month_start(read_on), 1)
    end = lease.effective_end
    if end is not None and end < month:
        return invoicing.month_start(end)
    return month


def billable(reading: MeterReading) -> bool:
    """Whether the reading measured any use to divide: not a baseline, not prepaid, and more than zero."""
    return (not reading.is_baseline and not reading.meter.is_prepaid and reading.previous is not None
            and (reading.usage or 0) > 0)


def charges_for(reading: MeterReading, *, rate: Decimal | None = None, minimum: Decimal | None = None,
                split: str | None = None, vacant_share: str | None = None, payer: str | None = None,
                bill_total: Decimal | None = None) -> list[MeterCharge]:
    """The charges approving the reading makes, unsaved. None for a baseline, a prepaid meter, or a meter the
    landlord pays. The rest default to the meter's settings (D-143)."""
    meter = reading.meter
    if not billable(reading) or (payer or meter.payer) == Meter.Payer.LANDLORD:
        return []
    rate = meter.rate if rate is None else rate
    division = splitting.divide(reading, split=split, vacant_share=vacant_share, rate=rate, minimum=minimum,
                                bill_total=bill_total)
    if not division.available:
        raise ValidationError(division.reason)
    charges = []
    for room in division.rooms:
        for part in room.parts:
            if part.amount <= 0:
                continue
            charges.append(MeterCharge(
                organization=reading.organization, reading=reading, lease=part.lease, unit=room.unit,
                service_start=part.start, service_end=part.end,
                billing_month=billing_month_for(part.lease, reading.read_on), amount=part.amount,
                description=splitting.describe(reading, division, room, part, rate=rate, bill_total=bill_total)))
    return charges


def estimate(reading: MeterReading) -> Decimal:
    """What approving the reading would bill in total, at today's rate and the meter's split."""
    try:
        return sum((c.amount for c in charges_for(reading)), ZERO)
    except ValidationError:
        return ZERO


def clean_bill_total(value) -> Decimal | None:
    """The utility company's bill typed on approval, or None when left empty."""
    if value in (None, ""):
        return None
    amount = _clean_amount(value, "bill_total")
    if amount <= 0:
        raise ValidationError({"bill_total": _("Enter the amount on the bill, or leave it empty.")})
    return amount


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


def _lock(reading: MeterReading) -> MeterReading:
    # Lock only the reading: FOR UPDATE cannot reach the nullable side of the `previous` join.
    return (MeterReading.objects.select_for_update(of=("self",)).select_related("meter__property", "previous")
            .get(pk=reading.pk))


def can_approve(membership: Membership, reading: MeterReading) -> bool:
    return (reading.organization_id == membership.organization_id and reading.status == Status.SUBMITTED
            and can(membership, "meters.approve", reading.meter.property))


@transaction.atomic
def approve(actor: Membership, reading: MeterReading, *, note="", split: str | None = None,
            vacant_share: str | None = None, bill_total=None, today=None, request=None) -> list[MeterCharge]:
    """Approves the reading and makes its charges. A shared meter is divided the meter's way unless another
    `split` or `vacant_share` was picked; `bill_total` divides the utility company's bill instead of use × rate.
    The choice is kept on the reading (D-143)."""
    _check(actor, reading.meter.property, "meters.approve")
    if split not in (None, "") and split not in Meter.Split.values:
        raise ValidationError(_("Choose how the meter is divided."))
    if vacant_share not in (None, "") and vacant_share not in Meter.VacantShare.values:
        raise ValidationError(_("Choose who pays the empty rooms' share."))
    bill_total = clean_bill_total(bill_total)
    reading = _lock(reading)
    if reading.status != Status.SUBMITTED:
        raise ValidationError(_("This reading is not waiting for approval."))
    if reading.previous is not None and reading.previous.status != Status.APPROVED:
        raise ValidationError(_("Approve the reading before it (%(day)s) first.")
                              % {"day": reading.previous.read_on.strftime("%d %b %Y")})
    if Flag.LOWER in reading.flags:
        raise ValidationError(_("The reading is lower than the last one. Reject it, or record it again as a "
                                "replaced meter."))
    note = (note or "").strip()[:300]
    if reading.flags and not note:
        raise ValidationError({"note": _("This reading is flagged. Say why it is right before approving it.")})
    meter = reading.meter
    if bill_total is not None and not billable(reading):
        raise ValidationError({"bill_total": _("This reading has no use to divide a bill over.")})
    reading.rate, reading.minimum_charge = meter.rate, meter.minimum_charge
    shared = meter.served.count() > 1
    reading.split = (split or meter.split) if shared else ""
    reading.vacant_share = (vacant_share or meter.vacant_share) if shared else ""
    reading.payer = meter.payer
    reading.bill_total = bill_total
    reading.status = Status.APPROVED
    reading.approved_at = timezone.now()
    reading.approved_by = actor.user
    reading.approval_note = note
    reading.save()
    charges = MeterCharge.objects.bulk_create(charges_for(
        reading, rate=reading.rate, minimum=reading.minimum_charge, split=reading.split or None,
        vacant_share=reading.vacant_share or None, payer=reading.payer, bill_total=bill_total))
    _audit("meters.reading_approve", actor, reading, request, {
        "status": [Status.SUBMITTED, Status.APPROVED], "rate": [None, str(reading.rate)],
        "charged": [None, str(sum((c.amount for c in charges), ZERO))],
        **({"split": [None, reading.split], "vacant_share": [None, reading.vacant_share]} if shared else {}),
        **({"payer": [None, reading.payer]} if meter.landlord_pays else {}),
        **({"bill_total": [None, str(bill_total)]} if bill_total is not None else {}),
        **({"note": [None, note]} if note else {})})
    for lease_id in sorted({c.lease_id for c in charges}):
        invoicing.bill_meter_charges(reading.organization, today=today, lease=Lease.all_objects.get(pk=lease_id),
                                     actor=actor, request=request)
    return charges


def approve_many(actor: Membership, readings, *, today=None, request=None) -> tuple[int, int]:
    """Approves each unflagged reading, oldest first. Returns (approved, skipped)."""
    done = skipped = 0
    for reading in sorted(readings, key=lambda r: (r.read_on, r.pk)):
        if reading.flags or not can_approve(actor, reading):
            skipped += 1
            continue
        try:
            approve(actor, reading, today=today, request=request)
        except (ValidationError, PermissionDenied):
            skipped += 1
        else:
            done += 1
    return done, skipped


@transaction.atomic
def reject(actor: Membership, reading: MeterReading, *, reason, request=None) -> MeterReading:
    _check(actor, reading.meter.property, "meters.approve")
    reading = _lock(reading)
    if reading.status != Status.SUBMITTED:
        raise ValidationError(_("Only a reading waiting for approval can be rejected."))
    reason = (reason or "").strip()[:300]
    if not reason:
        raise ValidationError({"reason": _("Say why the reading is rejected.")})
    if MeterReading.objects.filter(previous=reading, status__in=MeterReading.LIVE).exists():
        raise ValidationError(_("A later reading is measured from this one. Reject that one first."))
    reading.status = Status.REJECTED
    reading.rejected_at = timezone.now()
    reading.rejected_by = actor.user
    reading.reject_reason = reason
    reading.save()
    _audit("meters.reading_reject", actor, reading, request,
           {"status": [Status.SUBMITTED, Status.REJECTED], "reason": [None, reason]})
    return reading


def can_undo(membership: Membership, reading: MeterReading) -> bool:
    return (reading.status == Status.APPROVED and can(membership, "meters.approve", reading.meter.property)
            and not MeterReading.objects.filter(previous=reading, status=Status.APPROVED).exists()
            and not reading.charges.filter(cancelled_at__isnull=True, lines__is_void=False).exists())


@transaction.atomic
def undo_approval(actor: Membership, reading: MeterReading, *, request=None) -> MeterReading:
    """Back to waiting, while nothing it charged is on a live invoice (D-057 item 7)."""
    _check(actor, reading.meter.property, "meters.approve")
    reading = _lock(reading)
    if reading.status != Status.APPROVED:
        raise ValidationError(_("This reading is not approved."))
    if MeterReading.objects.filter(previous=reading, status=Status.APPROVED).exists():
        raise ValidationError(_("A later reading was approved after this one. Undo that one first."))
    live = reading.charges.filter(cancelled_at__isnull=True, lines__is_void=False)
    if live.exists():
        raise ValidationError(_("Its charge is on an invoice. Void the invoice first."))
    cancelled = reading.charges.filter(cancelled_at__isnull=True).update(cancelled_at=timezone.now())
    reading.status = Status.SUBMITTED
    reading.approved_at = reading.approved_by = reading.rate = reading.minimum_charge = reading.bill_total = None
    reading.approval_note = reading.split = reading.vacant_share = reading.payer = ""
    reading.save()
    _audit("meters.reading_unapprove", actor, reading, request,
           {"status": [Status.APPROVED, Status.SUBMITTED], "charges_cancelled": [None, cancelled]})
    return reading


def units_for(prop: Property):
    return Unit.objects.filter(property=prop).order_by("code")


# ---------------------------------------------------------------------------
# Rounds typed offline (D-088)
# ---------------------------------------------------------------------------


def record_offline_round(actor: Membership, prop: Property, *, key: str, read_on: datetime.date,
                         entries: list[RoundEntry], typed_at=None, today=None,
                         request=None) -> tuple[list[MeterReading] | None, dict[int, list[str]]]:
    """Records a round kept on a phone. Returns (readings, errors); readings is None when the round was
    already recorded, so a second send does nothing (item 3)."""
    with transaction.atomic():
        try:
            with transaction.atomic():
                OfflineRound.objects.create(organization=actor.organization, user=actor.user, key=key,
                                            property=prop, typed_at=typed_at, readings=len(entries))
        except IntegrityError:
            return None, {}
        saved, errors = record_round(actor, read_on, entries, today=today, request=request)
        if errors:
            transaction.set_rollback(True)
            return [], errors
        audit.record("meters.offline_round", actor=actor.user, organization=actor.organization, obj=prop,
                     request=request, changes={"round": [None, key], "readings": [None, len(saved)],
                                               "typed_at": [None, typed_at.isoformat() if typed_at else None]})
    return saved, {}
