"""Hostel rooms and beds (D-188).

A hostel room either holds beds that are let one by one (each bed is a BED_SPACE unit with its own lease and its own
Paybill account number, PROP-R101-A), or is let whole (one HOSTEL_ROOM unit, one booking that may name several
students). The choice is made when the room is made. Everything else (leases, invoices, payments, reports) is the
ordinary unit machinery, so a hostel gets all of it for free.
"""

import secrets

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models.functions import Upper
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import require
from audit import services as audit

from .models import Property, Room, Unit, clean_code
from .services import _create_unit, _same_org, _validate, archive_unit

BED_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
MAX_ROOMS_AT_ONCE = 300
MAX_STUDENTS_WHOLE = 50


def bed_code(room_code: str, index: int) -> str:
    """R101 and 0 give R101-A: the account number a student types on Paybill follows it (PROP-R101-A)."""
    return f"{room_code}-{BED_LETTERS[index]}"


def _create_units(actor: Membership, room: Room, *, price, first_index: int = 0, count: int | None = None,
                  request=None) -> list[Unit]:
    from subscriptions import entitlements

    if room.let_whole:
        specs = [(room.code, Unit.Type.HOSTEL_ROOM, _("Room for %(n)s") % {"n": room.capacity})]
    else:
        count = room.capacity if count is None else count
        specs = [(bed_code(room.code, i), Unit.Type.BED_SPACE, _("Bed %(letter)s") % {"letter": BED_LETTERS[i]})
                 for i in range(first_index, first_index + count)]
    units = []
    for code, unit_type, label in specs:
        try:
            units.append(_create_unit(actor, room.property, code=code, room=room, building=room.building,
                                      unit_type=unit_type, type_label=label, list_rent=price, request=request))
        except ValidationError as exc:
            if hasattr(exc, "error_dict") and "code" in exc.error_dict:
                raise ValidationError({"code": _("The room code %(code)s is too long or not allowed. Use up to 8 "
                                                 "letters or digits, e.g. R101.") % {"code": room.code}}) from None
            raise
        entitlements.added("units")
    return units


def _create_room(actor: Membership, prop, *, code: str, capacity: int, let_whole: bool, gender: str, building,
                 price, request=None) -> Room:
    code = clean_code(code)
    if gender not in Room.Gender.values:
        raise ValidationError({"gender": _("Unknown choice.")})
    if building is not None and (building.property_id != prop.pk or building.is_archived):
        raise ValidationError({"building": _("Choose a building in this property.")})
    if not let_whole and capacity > len(BED_LETTERS):
        raise ValidationError({"capacity": _("A room with beds let one by one holds up to %(n)s beds.")
                               % {"n": len(BED_LETTERS)}})
    if price is not None and price < 0:
        raise ValidationError({"price": _("The price cannot be negative.")})
    room = Room(organization=prop.organization, property=prop, building=building, code=code, gender=gender,
                let_whole=bool(let_whole), capacity=capacity)
    _validate(room, exclude=["organization", "property"])
    clash = (Room.all_objects.filter(property=prop).annotate(ucode=Upper("code")).filter(ucode=room.code)
             .first())
    if clash:
        # An archived room keeps its code: its beds, bookings and payment references still carry it.
        raise ValidationError({"code": (_("Room %(code)s was archived. Its code stays with its old bookings; use another.")
                                        if clash.is_archived else _("This hostel already has a room %(code)s."))
                               % {"code": room.code}})
    try:
        with transaction.atomic():
            room.save()
    except IntegrityError:
        raise ValidationError({"code": _("This hostel already has a room %(code)s.") % {"code": room.code}}) from None
    audit.record("room.create", actor=actor.user, organization=actor.organization, obj=room, request=request,
                 changes={"code": [None, room.code], "capacity": [None, capacity], "let_whole": [None, room.let_whole],
                          **({"gender": [None, gender]} if gender else {})})
    _create_units(actor, room, price=price, request=request)
    return room


@transaction.atomic
def add_rooms(actor: Membership, prop, *, prefix: str = "", first: int = 1, count: int = 1, capacity: int = 1,
              let_whole: bool = False, gender: str = "", building=None, price=None, request=None) -> list[Room]:
    """Makes `count` rooms numbered from `first` (prefix R, first 101, count 3 gives R101, R102, R103).

    Each room takes `capacity` beds, let one by one at `price` each, or is let whole at `price` for the room.
    All or nothing: one clash or plan limit rolls everything back.
    """
    from subscriptions import entitlements

    _same_org(actor, prop, building)
    require(actor, "units.manage", prop)
    if prop.is_archived:
        raise ValidationError(_("Restore the property first."))
    if not 1 <= count <= MAX_ROOMS_AT_ONCE:
        raise ValidationError({"count": _("Add between 1 and %(n)s rooms at a time.") % {"n": MAX_ROOMS_AT_ONCE}})
    if not 1 <= capacity <= (MAX_STUDENTS_WHOLE if let_whole else len(BED_LETTERS)):
        raise ValidationError({"capacity": _("That is more than a room can hold.")})
    if first < 0:
        raise ValidationError({"first": _("The first room number cannot be negative.")})
    prefix = clean_code(prefix)
    rooms = []
    with entitlements.batch(actor.organization):
        for n in range(first, first + count):
            rooms.append(_create_room(actor, prop, code=f"{prefix}{n}", capacity=capacity, let_whole=let_whole,
                                      gender=gender, building=building, price=price, request=request))
    return rooms


@transaction.atomic
def update_room(actor: Membership, room: Room, *, gender: str | None = None, capacity: int | None = None,
                request=None) -> Room:
    """Change who a room is for, or take another bed (or student) in it.

    Beds are never removed here: archive the bed, once its lease has ended.
    """
    from subscriptions import entitlements

    _same_org(actor, room)
    require(actor, "units.manage", room.property)
    if room.is_archived:
        raise ValidationError(_("Restore the room first."))
    changes = {}
    if gender is not None and gender != room.gender:
        if gender not in Room.Gender.values:
            raise ValidationError({"gender": _("Unknown choice.")})
        changes["gender"] = [room.gender, gender]
        room.gender = gender
    if capacity is not None and capacity != room.capacity:
        if capacity < room.capacity:
            raise ValidationError({"capacity": _("To take a bed out, archive it once its lease has ended.")})
        if capacity > (MAX_STUDENTS_WHOLE if room.let_whole else len(BED_LETTERS)):
            raise ValidationError({"capacity": _("That is more than a room can hold.")})
        added = capacity - room.capacity
        if not room.let_whole:
            first = room.units.model.all_objects.filter(room=room).count()  # archived beds keep their letter
            template = room.units.order_by("pk").first()
            with entitlements.batch(room.organization):
                _create_units(actor, room, price=template.list_rent if template else None, first_index=first,
                              count=added, request=request)
        elif added:
            room.units.update(type_label=_("Room for %(n)s") % {"n": capacity})
        changes["capacity"] = [room.capacity, capacity]
        room.capacity = capacity
    if changes:
        room.save()
        audit.record("room.update", actor=actor.user, organization=actor.organization, obj=room, request=request,
                     changes=changes)
    return room


def free_beds(prop: Property, gender: str = "", whole: bool | None = None, today=None):
    """Lettable units in the hostel's rooms that nobody holds, for a student of `gender` (F, M or "" for any).

    A room marked for the other gender is left out; a room for anyone suits everyone. `whole` narrows to rooms let
    whole (True) or beds let one by one (False).
    """
    from leases.services import with_occupancy

    units = with_occupancy(Unit.objects.filter(property=prop, room__isnull=False, room__archived_at__isnull=True,
                                               manual_status=Unit.ManualStatus.NORMAL), today).filter(
        is_occupied=False)
    if gender:
        units = units.filter(room__gender__in=["", gender])
    if whole is not None:
        units = units.filter(room__let_whole=whole)
    return units


def _may_run_booking_link(actor: Membership, prop: Property) -> None:
    from accounts.permissions import can

    if not (can(actor, "units.manage", prop) or can(actor, "prospects.manage", prop)):
        raise PermissionDenied("units.manage")


@transaction.atomic
def enable_booking_link(actor: Membership, prop: Property, request=None) -> Property:
    """Turns the public booking page on (or issues a fresh link). Enquiries go to whoever did it."""
    _same_org(actor, prop)
    _may_run_booking_link(actor, prop)
    if prop.is_archived:
        raise ValidationError(_("Restore the property first."))
    if actor.organization.org_type != "HOSTEL":
        raise ValidationError(_("Only a hostel has a booking page."))
    prop.booking_token, prop.booking_shared_by = secrets.token_urlsafe(16), actor.user
    prop.save(update_fields=["booking_token", "booking_shared_by", "updated_at"])
    audit.record("property.booking_link_on", actor=actor.user, organization=actor.organization, obj=prop,
                 request=request)
    return prop


@transaction.atomic
def disable_booking_link(actor: Membership, prop: Property, request=None) -> Property:
    _same_org(actor, prop)
    _may_run_booking_link(actor, prop)
    if prop.booking_token:
        prop.booking_token, prop.booking_shared_by = None, None
        prop.save(update_fields=["booking_token", "booking_shared_by", "updated_at"])
        audit.record("property.booking_link_off", actor=actor.user, organization=actor.organization, obj=prop,
                     request=request)
    return prop


def shared_hostel(token: str):
    """The live hostel behind a public booking link, or None. Lapsed or archived organizations show nothing."""
    from accounts.models import Organization

    return (Property.objects.filter(booking_token=token, organization__status=Organization.Status.ACTIVE,
                                    organization__archived_at__isnull=True, organization__org_type="HOSTEL")
            .select_related("organization", "booking_shared_by").first())


@transaction.atomic
def archive_room(actor: Membership, room: Room, request=None) -> None:
    """Archives the room and its beds. Blocked while any of them has a draft or active lease."""
    _same_org(actor, room)
    require(actor, "units.manage", room.property)
    if room.is_archived:
        return
    for unit in room.units.all():
        archive_unit(actor, unit, request=request)
    room.archive(actor.user)
    audit.record("room.archive", actor=actor.user, organization=actor.organization, obj=room, request=request)
