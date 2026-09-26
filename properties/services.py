"""Property, building and unit services (doc 11 §5, §23; D-029).

Rules:
- Every target must belong to the actor's organization and be inside the actor's property scope.
- Property codes are fixed after creation. A unit's payment reference is `{PROPERTY_CODE}-{UNIT_CODE}`.
- Codes and payment references stay unique across archived rows, so old ones are never reused.
- Nothing is deleted: properties, buildings and units are archived and can be restored.
- Every change is audited.
"""

from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models.functions import Upper
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership, PropertyAccess
from accounts.permissions import clear_cache, require
from audit import services as audit

from .models import Building, Property, Unit, clean_code, payment_reference_for

PROPERTY_FIELDS = ("name", "category", "county", "sub_county", "area", "street", "latitude", "longitude")
UNIT_FIELDS = ("code", "building", "unit_type", "type_label", "list_rent")

# Case C: a single house gets one unit automatically.
MAIN_HOUSE_CODE = "MAIN"


def _same_org(actor: Membership, *objs) -> None:
    for obj in objs:
        if obj is not None and obj.organization_id != actor.organization_id:
            raise PermissionDenied(_("That record belongs to another organization."))


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
    before = _snapshot(prop, PROPERTY_FIELDS)
    for k in PROPERTY_FIELDS:
        if k in fields:
            setattr(prop, k, fields[k].strip() if isinstance(fields[k], str) else fields[k])
    _validate(prop, exclude=["organization", "created_by"])
    prop.save()
    changes = audit.diff(before, _snapshot(prop, PROPERTY_FIELDS))
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
    return unit


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
