"""Property, building and unit services (doc 11 §5, §23; D-029).

Rules:
- Every target must belong to the actor's organization and be inside the actor's property scope.
- Property codes are fixed after creation. A unit's payment reference is `{PROPERTY_CODE}-{UNIT_CODE}`.
- Codes and payment references stay unique across archived rows, so old ones are never reused.
- Nothing is deleted: properties, buildings and units are archived and can be restored.
- Every change is audited.
"""

import secrets
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models.functions import Upper
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership, PropertyAccess
from accounts.permissions import can, clear_cache, require
from audit import services as audit

from .models import (
    Building,
    Property,
    PropertyOwner,
    PropertyShare,
    Unit,
    clean_code,
    format_percent,
    payment_reference_for,
)

PROPERTY_FIELDS = ("name", "category", "county", "sub_county", "area", "street", "latitude", "longitude", "owner",
                   "management_fee_percent", "management_fee_fixed", "branch", "service_days", "market_value")
OWNER_FIELDS = ("name", "phone", "email", "note")
UNIT_FIELDS = ("code", "building", "room", "unit_type", "type_label", "list_rent")

# Case C: a single house gets one unit automatically.
MAIN_HOUSE_CODE = "MAIN"


def _same_org(actor: Membership, *objs) -> None:
    for obj in objs:
        if obj is not None and obj.organization_id != actor.organization_id:
            raise PermissionDenied(_("That record belongs to another organization."))


def _check_branch(actor: Membership, branch, current=None) -> None:
    """A branch decides who sees the property (D-127): only those who run the organization move it."""
    if branch == current:
        return
    if not can(actor, "organization.manage"):
        raise PermissionDenied(_("Only staff who manage the organization can change a property's branch."))
    if branch is not None:
        _same_org(actor, branch)
        if branch.is_archived:
            raise ValidationError({"branch": _("That branch is archived.")})


def _validate(obj, exclude=()) -> None:
    """Field validation only; uniqueness is checked against all rows, archived included."""
    obj.full_clean(exclude=list(exclude), validate_unique=False, validate_constraints=False)


def _snapshot(obj, fields) -> dict:
    out = {}
    for f in fields:
        value = getattr(obj, f)
        if isinstance(value, Decimal):
            value = str(value)
        elif hasattr(value, "pk"):
            value = str(value)
        out[f] = value
    return out


# ---------------------------------------------------------------------------
# Owners (D-053)
# ---------------------------------------------------------------------------


def _save_owner(actor: Membership, owner: PropertyOwner, fields: dict, request=None) -> PropertyOwner:
    require(actor, "properties.manage")
    _same_org(actor, owner)
    creating = owner.pk is None
    before = {} if creating else _snapshot(owner, OWNER_FIELDS)
    for k in OWNER_FIELDS:
        if k in fields:
            setattr(owner, k, (fields[k] or "").strip())
    if not owner.name:
        raise ValidationError({"name": _("Enter the owner's name.")})
    _validate(owner, exclude=["organization"])
    owner.save()
    after = _snapshot(owner, OWNER_FIELDS)
    changes = {k: [None, v] for k, v in after.items() if v} if creating else audit.diff(before, after)
    if changes:
        audit.record("property_owner.create" if creating else "property_owner.update", actor=actor.user,
                     organization=actor.organization, obj=owner, request=request, changes=changes)
    return owner


@transaction.atomic
def create_owner(actor: Membership, *, request=None, **fields) -> PropertyOwner:
    return _save_owner(actor, PropertyOwner(organization=actor.organization), fields, request)


@transaction.atomic
def update_owner(actor: Membership, owner: PropertyOwner, *, request=None, **fields) -> PropertyOwner:
    return _save_owner(actor, owner, fields, request)


# ---------------------------------------------------------------------------
# Joint ownership (D-078)
# ---------------------------------------------------------------------------

HUNDRED = Decimal(100)


def shares_of(prop: Property) -> list[tuple[PropertyOwner, Decimal]]:
    """Every owner of the property and their percentage; the sole owner holds 100."""
    rows = [(s.owner, s.percent) for s in prop.shares.select_related("owner")]
    if rows:
        return rows
    return [(prop.owner, HUNDRED)] if prop.owner_id else []


@transaction.atomic
def set_shares(actor: Membership, prop: Property, shares: list[tuple[PropertyOwner, Decimal]], *,
               request=None) -> Property:
    """Replaces the property's owners. Two or more rows adding up to 100 make it jointly owned; the
    largest share becomes the main owner. One row makes that owner the sole owner; none clears it."""
    _same_org(actor, prop)
    require(actor, "properties.manage", prop)
    _same_org(actor, *(owner for owner, _pct in shares))
    owners = [owner for owner, _pct in shares]
    if len({o.pk for o in owners}) != len(owners):
        raise ValidationError(_("Each owner can appear only once."))
    if any(pct is None or pct <= 0 or pct > HUNDRED for _o, pct in shares):
        raise ValidationError(_("Each share must be more than 0 and at most 100%."))
    if len(shares) > 1 and sum(pct for _o, pct in shares) != HUNDRED:
        raise ValidationError(_("The shares add up to %(total)s%%; they must add up to 100%%.")
                              % {"total": format_percent(sum(pct for _o, pct in shares))})
    if len(shares) == 1 and shares[0][1] != HUNDRED:
        raise ValidationError(_("A sole owner holds 100%. Add the other owners and their shares."))

    before = {str(o): format_percent(p) for o, p in shares_of(prop)}
    prop.shares.all().delete()
    if len(shares) > 1:
        PropertyShare.objects.bulk_create([PropertyShare(organization=prop.organization, property=prop, owner=o,
                                                         percent=p) for o, p in shares])
        main = max(shares, key=lambda row: row[1])[0]  # the first of the largest, as entered
    else:
        main = shares[0][0] if shares else None
    prop.owner = main
    prop.save(update_fields=["owner", "updated_at"])
    after = {str(o): format_percent(p) for o, p in shares_of(prop)}
    if before != after:
        audit.record("property.shares", actor=actor.user, organization=actor.organization, obj=prop,
                     request=request, changes={"owners": [before, after]})
    return prop


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


def _property_code_taken(org, code: str, exclude_pk=None) -> bool:
    qs = Property.all_objects.for_org(org).annotate(ucode=Upper("code")).filter(ucode=clean_code(code))
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.exists()


@transaction.atomic
def create_property(actor: Membership, *, name: str, code: str, category: str = Property.Category.APARTMENT_BLOCK,
                    request=None, **address) -> Property:
    require(actor, "properties.manage")
    org = actor.organization
    prop = Property(organization=org, name=name.strip(), code=clean_code(code), category=category,
                    created_by=actor.user, **{k: v for k, v in address.items() if k in PROPERTY_FIELDS})
    _same_org(actor, prop.owner)
    _check_branch(actor, prop.branch)
    _validate(prop, exclude=["organization", "created_by"])
    if _property_code_taken(org, prop.code):
        raise ValidationError({"code": _("Another property already uses the code %(code)s.") % {"code": prop.code}})
    try:
        with transaction.atomic():
            prop.save()
    except IntegrityError:
        raise ValidationError({"code": _("Another property already uses that code.")}) from None

    # A scoped member who adds a property must be able to see it.
    if not actor.all_properties:
        PropertyAccess.objects.get_or_create(membership=actor, property=prop)
        clear_cache(actor)

    audit.record("property.create", actor=actor.user, organization=org, obj=prop, request=request,
                 changes={k: [None, v] for k, v in _snapshot(prop, ("code", *PROPERTY_FIELDS)).items() if v})

    if prop.category == Property.Category.SINGLE_HOUSE:
        _create_unit(actor, prop, code=MAIN_HOUSE_CODE, unit_type=Unit.Type.HOUSE, type_label=_("Main house"),
                     request=request)
    return prop


@transaction.atomic
def update_property(actor: Membership, prop: Property, *, request=None, **fields) -> Property:
    _same_org(actor, prop)
    require(actor, "properties.manage", prop)
    if "code" in fields and clean_code(fields["code"]) != prop.code:
        raise ValidationError({"code": _("A property code cannot be changed; unit payment references use it.")})
    _same_org(actor, fields.get("owner"))
    if "branch" in fields:
        _check_branch(actor, fields["branch"], prop.branch)
    if "owner" in fields and prop.shares.exists():
        new = fields["owner"]
        if new is None or not prop.shares.filter(owner=new).exists():
            raise ValidationError({"owner": _("This property has joint owners. Change them under Owners and shares "
                                              "on the property page.")})
    before = _snapshot(prop, PROPERTY_FIELDS)
    for k in PROPERTY_FIELDS:
        if k in fields:
            setattr(prop, k, fields[k].strip() if isinstance(fields[k], str) else fields[k])
    _validate(prop, exclude=["organization", "created_by"])
    prop.save()
    changes = audit.diff(before, _snapshot(prop, PROPERTY_FIELDS))
    if "branch" in changes:
        clear_cache(actor)
    if changes:
        audit.record("property.update", actor=actor.user, organization=actor.organization, obj=prop,
                     request=request, changes=changes)
    return prop


def _open_leases():
    """Drafts, active leases and closed ones whose tenant has not moved out yet."""
    from django.db.models import Q

    from leases.models import Lease

    return Lease.objects.filter(Q(status__in=(Lease.Status.DRAFT, Lease.Status.ACTIVE))
                                | Q(ended_on__gte=timezone.localdate()))


def _issued_leases():
    from leases.models import Lease

    return Lease.all_objects.exclude(status=Lease.Status.DRAFT)


@transaction.atomic
def archive_property(actor: Membership, prop: Property, request=None) -> None:
    """Archives the property with its buildings and units. Blocked while any unit has an open lease."""
    _same_org(actor, prop)
    require(actor, "properties.manage", prop)
    if prop.is_archived:
        return
    if _open_leases().filter(unit__property=prop).exists():
        raise ValidationError(_("End or delete the leases on this property before archiving it."))
    now = timezone.now()
    units = Unit.objects.filter(property=prop).update(archived_at=now, archived_by=actor.user, updated_at=now)
    Building.objects.filter(property=prop).update(archived_at=now, archived_by=actor.user, updated_at=now)
    prop.archived_at, prop.archived_by = now, actor.user
    prop.save(update_fields=["archived_at", "archived_by", "updated_at"])
    audit.record("property.archive", actor=actor.user, organization=actor.organization, obj=prop, request=request,
                 changes={"units_archived": [0, units]})


@transaction.atomic
def restore_property(actor: Membership, prop: Property, request=None) -> None:
    """Restores the property and the buildings and units archived together with it."""
    _same_org(actor, prop)
    require(actor, "properties.manage", prop)
    if not prop.is_archived:
        return
    when = prop.archived_at
    Unit.all_objects.filter(property=prop, archived_at=when).update(archived_at=None, archived_by=None)
    Building.all_objects.filter(property=prop, archived_at=when).update(archived_at=None, archived_by=None)
    prop.restore()
    audit.record("property.restore", actor=actor.user, organization=actor.organization, obj=prop, request=request)


# ---------------------------------------------------------------------------
# Buildings
# ---------------------------------------------------------------------------


def _building_name_taken(prop: Property, name: str, exclude_pk=None) -> bool:
    qs = Building.all_objects.filter(property=prop).annotate(uname=Upper("name")).filter(uname=name.strip().upper())
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.exists()


@transaction.atomic
def create_building(actor: Membership, prop: Property, *, name: str, request=None) -> Building:
    _same_org(actor, prop)
    require(actor, "properties.manage", prop)
    if prop.is_archived:
        raise ValidationError(_("Restore the property first."))
    building = Building(organization=prop.organization, property=prop, name=name.strip())
    _validate(building, exclude=["organization", "property"])
    if _building_name_taken(prop, building.name):
        raise ValidationError({"name": _("This property already has a building with that name.")})
    building.save()
    audit.record("building.create", actor=actor.user, organization=actor.organization, obj=building,
                 request=request, changes={"property": [None, str(prop)], "name": [None, building.name]})
    return building


@transaction.atomic
def rename_building(actor: Membership, building: Building, *, name: str, request=None) -> Building:
    _same_org(actor, building)
    require(actor, "properties.manage", building.property)
    old = building.name
    building.name = name.strip()
    _validate(building, exclude=["organization", "property"])
    if _building_name_taken(building.property, building.name, exclude_pk=building.pk):
        raise ValidationError({"name": _("This property already has a building with that name.")})
    building.save()
    if old != building.name:
        audit.record("building.update", actor=actor.user, organization=actor.organization, obj=building,
                     request=request, changes={"name": [old, building.name]})
    return building


@transaction.atomic
def archive_building(actor: Membership, building: Building, request=None) -> None:
    _same_org(actor, building)
    require(actor, "properties.manage", building.property)
    if Unit.objects.filter(building=building).exists():
        raise ValidationError(_("Move or archive this building's units first."))
    building.archive(actor.user)
    audit.record("building.archive", actor=actor.user, organization=actor.organization, obj=building,
                 request=request)


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


def _check_unit(prop: Property, unit: Unit, exclude_pk=None) -> None:
    if unit.building is not None and (unit.building.property_id != prop.pk or unit.building.is_archived):
        raise ValidationError({"building": _("Choose a building in this property.")})
    if unit.room is not None and (unit.room.property_id != prop.pk or unit.room.is_archived):
        raise ValidationError({"room": _("Choose a room in this hostel.")})
    code_taken = (
        Unit.all_objects.filter(property=prop).annotate(ucode=Upper("code")).filter(ucode=unit.code)
        .exclude(pk=exclude_pk).exists()
    )
    if code_taken:
        raise ValidationError({"code": _("This property already has a unit %(code)s.") % {"code": unit.code}})
    ref_taken = (
        Unit.all_objects.for_org(prop.organization).annotate(uref=Upper("payment_reference"))
        .filter(uref=unit.payment_reference).exclude(pk=exclude_pk).exists()
    )
    if ref_taken:
        raise ValidationError({"code": _("The payment reference %(ref)s is already in use.")
                               % {"ref": unit.payment_reference}})


def _save_unit(unit: Unit) -> None:
    try:
        with transaction.atomic():
            unit.save()
    except IntegrityError:
        raise ValidationError({"code": _("That unit code or payment reference is already in use.")}) from None


def _create_unit(actor: Membership, prop: Property, *, code: str, request=None, **fields) -> Unit:
    from subscriptions import entitlements

    entitlements.check(prop.organization, "units")
    unit = Unit(organization=prop.organization, property=prop, code=clean_code(code),
                **{k: v for k, v in fields.items() if k in UNIT_FIELDS})
    unit.payment_reference = payment_reference_for(prop.code, unit.code)
    _validate(unit, exclude=["organization", "property", "payment_reference"])
    _check_unit(prop, unit)
    _save_unit(unit)
    audit.record("unit.create", actor=actor.user, organization=actor.organization, obj=unit, request=request,
                 changes={k: [None, v] for k, v in _snapshot(unit, (*UNIT_FIELDS, "payment_reference")).items()
                          if v not in (None, "")})
    return unit


@transaction.atomic
def create_unit(actor: Membership, prop: Property, *, code: str, request=None, **fields) -> Unit:
    _same_org(actor, prop, fields.get("building"))
    require(actor, "units.manage", prop)
    if prop.is_archived:
        raise ValidationError(_("Restore the property first."))
    return _create_unit(actor, prop, code=code, request=request, **fields)


@transaction.atomic
def update_unit(actor: Membership, unit: Unit, *, request=None, **fields) -> Unit:
    """Changing the code changes the payment reference, so it is blocked once a lease has been issued."""
    _same_org(actor, unit, fields.get("building"))
    require(actor, "units.manage", unit.property)
    before = _snapshot(unit, (*UNIT_FIELDS, "payment_reference"))
    for k in UNIT_FIELDS:
        if k in fields:
            setattr(unit, k, clean_code(fields[k]) if k == "code" else fields[k])
    if unit.code != before["code"] and _issued_leases().filter(unit=unit).exists():
        raise ValidationError({"code": _("Tenants pay to this code. It cannot change once a lease has been issued.")})
    unit.payment_reference = payment_reference_for(unit.property.code, unit.code)
    _validate(unit, exclude=["organization", "property", "payment_reference"])
    _check_unit(unit.property, unit, exclude_pk=unit.pk)
    _save_unit(unit)
    changes = audit.diff(before, _snapshot(unit, (*UNIT_FIELDS, "payment_reference")))
    if changes:
        audit.record("unit.update", actor=actor.user, organization=actor.organization, obj=unit, request=request,
                     changes=changes)
    return unit


@transaction.atomic
def set_unit_status(actor: Membership, unit: Unit, status: str, request=None) -> Unit:
    _same_org(actor, unit)
    require(actor, "units.set_status", unit.property)
    if status not in Unit.ManualStatus.values:
        raise ValidationError(_("Unknown status."))
    old = unit.manual_status
    if old != status:
        unit.manual_status = status
        unit.save(update_fields=["manual_status", "updated_at"])
        audit.record("unit.status", actor=actor.user, organization=actor.organization, obj=unit, request=request,
                     changes={"manual_status": [old, status]})
        if old == Unit.ManualStatus.RESERVED:
            from prospects import services as prospects  # a hold for a prospect ends with the Reserved status

            prospects.unit_status_changed(actor, unit, request=request)
    return unit


@transaction.atomic
def bulk_update_units(actor: Membership, units, *, rent=None, percent=None, status: str = "", request=None) -> int:
    """Change the asking rent (to an amount, or by a percent) and/or the manual status of many units at once.

    Each unit goes through the single-unit services, so permissions, audit and rules are the same.
    All or nothing: one blocked unit rolls the whole change back.
    """
    if rent is not None and percent is not None:
        raise ValidationError(_("Choose a new rent or a percentage change, not both."))
    if rent is not None and rent < 0:
        raise ValidationError(_("Rent cannot be negative."))
    count = 0
    for unit in units:
        if rent is not None:
            update_unit(actor, unit, request=request, list_rent=Decimal(rent))
        elif percent is not None:
            base = unit.list_rent
            if base is None:
                continue
            new = (base * (Decimal(100) + Decimal(percent)) / Decimal(100)).quantize(Decimal("1"))
            update_unit(actor, unit, request=request, list_rent=max(new, Decimal(0)))
        if status:
            set_unit_status(actor, unit, status, request=request)
        count += 1
    return count


@transaction.atomic
def archive_unit(actor: Membership, unit: Unit, request=None) -> None:
    """Blocked while the unit has a draft or active lease. Open balances join in Phase 3 (doc 11 §23)."""
    _same_org(actor, unit)
    require(actor, "units.manage", unit.property)
    if unit.is_archived:
        return
    if _open_leases().filter(unit=unit).exists():
        raise ValidationError(_("End or delete this unit's lease before archiving it."))
    unit.archive(actor.user)
    audit.record("unit.archive", actor=actor.user, organization=actor.organization, obj=unit, request=request)


@transaction.atomic
def restore_unit(actor: Membership, unit: Unit, request=None) -> None:
    _same_org(actor, unit)
    require(actor, "units.manage", unit.property)
    if unit.property.is_archived:
        raise ValidationError(_("Restore the property first."))
    if unit.building is not None and unit.building.is_archived:
        raise ValidationError(_("Restore the building first, or move the unit."))
    unit.restore()
    audit.record("unit.restore", actor=actor.user, organization=actor.organization, obj=unit, request=request)


# ---------------------------------------------------------------------------
# Public vacancy link (D-040)
# ---------------------------------------------------------------------------


def can_share_unit(actor: Membership, unit: Unit) -> bool:
    """Unit managers and letting agents (who draft leases) may advertise a unit."""
    return can(actor, "units.manage", unit.property) or can(actor, "leases.draft", unit.property)


@transaction.atomic
def share_unit(actor: Membership, unit: Unit, request=None) -> Unit:
    """Issues a new public link, replacing any old one. The sharer's name and phone are the contact."""
    _same_org(actor, unit)
    if not can_share_unit(actor, unit):
        raise PermissionDenied("units.manage")
    if unit.is_archived:
        raise ValidationError(_("Restore the unit first."))
    unit.share_token, unit.shared_by = secrets.token_urlsafe(16), actor.user
    unit.save(update_fields=["share_token", "shared_by", "updated_at"])
    audit.record("unit.share", actor=actor.user, organization=actor.organization, obj=unit, request=request)
    return unit


@transaction.atomic
def unshare_unit(actor: Membership, unit: Unit, request=None) -> Unit:
    _same_org(actor, unit)
    if not can_share_unit(actor, unit):
        raise PermissionDenied("units.manage")
    if unit.share_token:
        unit.share_token, unit.shared_by = None, None
        unit.save(update_fields=["share_token", "shared_by", "updated_at"])
        audit.record("unit.unshare", actor=actor.user, organization=actor.organization, obj=unit, request=request)
    return unit
