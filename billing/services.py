"""Charge type services (doc 11 §8). Needs `charges.manage`; every change is audited."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models.functions import Upper
from django.utils.translation import gettext as _

from accounts.models import Membership, Organization
from accounts.permissions import require
from audit import services as audit

from .models import ChargeType

# Seeded for every organization on first use.
DEFAULT_CHARGE_TYPES = (
    ("Rent", ChargeType.Category.RENT),
    ("Deposit", ChargeType.Category.DEPOSIT),
    ("Water", ChargeType.Category.WATER),
    ("Garbage", ChargeType.Category.GARBAGE),
    ("Service charge", ChargeType.Category.SERVICE_CHARGE),
)


def ensure_default_charge_types(org: Organization) -> None:
    existing = set(ChargeType.all_objects.for_org(org).filter(is_system=True).values_list("category", flat=True))
    for name, category in DEFAULT_CHARGE_TYPES:
        if category in existing:
            continue
        taken = ChargeType.all_objects.for_org(org).annotate(u=Upper("name")).filter(u=name.upper()).exists()
        try:
            with transaction.atomic():
                ChargeType.objects.create(organization=org, name=name if not taken else f"{name} (default)",
                                          category=category, is_system=True)
        except IntegrityError:
            pass  # created concurrently


def recurring_charge_types(org: Organization):
    ensure_default_charge_types(org)
    return ChargeType.objects.for_org(org).exclude(category__in=ChargeType.NOT_RECURRING)


def _same_org(actor: Membership, obj) -> None:
    if obj.organization_id != actor.organization_id:
        raise PermissionDenied(_("That record belongs to another organization."))


def _name_taken(org, name, exclude_pk=None) -> bool:
    qs = ChargeType.all_objects.for_org(org).annotate(u=Upper("name")).filter(u=name.upper())
    return qs.exclude(pk=exclude_pk).exists() if exclude_pk else qs.exists()


def _clean_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise ValidationError({"name": _("Enter a name.")})
    if len(name) > 60:
        raise ValidationError({"name": _("Use 60 characters or fewer.")})
    return name


@transaction.atomic
def create_charge_type(actor: Membership, *, name: str, category: str, request=None) -> ChargeType:
    require(actor, "charges.manage")
    name = _clean_name(name)
    if category not in ChargeType.Category.values or category in ChargeType.NOT_RECURRING:
        raise ValidationError({"category": _("Choose a category.")})
    if _name_taken(actor.organization, name):
        raise ValidationError({"name": _("There is already a charge type called %(name)s.") % {"name": name}})
    ct = ChargeType.objects.create(organization=actor.organization, name=name, category=category)
    audit.record("charge_type.create", actor=actor.user, organization=actor.organization, obj=ct, request=request,
                 changes={"name": [None, name], "category": [None, category]})
    return ct


@transaction.atomic
def rename_charge_type(actor: Membership, ct: ChargeType, *, name: str, request=None) -> ChargeType:
    _same_org(actor, ct)
    require(actor, "charges.manage")
    name = _clean_name(name)
    if name == ct.name:
        return ct
    if _name_taken(actor.organization, name, exclude_pk=ct.pk):
        raise ValidationError({"name": _("There is already a charge type called %(name)s.") % {"name": name}})
    old, ct.name = ct.name, name
    ct.save(update_fields=["name", "updated_at"])
    audit.record("charge_type.update", actor=actor.user, organization=actor.organization, obj=ct, request=request,
                 changes={"name": [old, name]})
    return ct


@transaction.atomic
def archive_charge_type(actor: Membership, ct: ChargeType, request=None) -> None:
    """Existing lease charges keep running; the type just can't be picked for new ones."""
    _same_org(actor, ct)
    require(actor, "charges.manage")
    if ct.is_system:
        raise ValidationError(_("Default charge types cannot be archived."))
    if not ct.is_archived:
        ct.archive(actor.user)
        audit.record("charge_type.archive", actor=actor.user, organization=actor.organization, obj=ct,
                     request=request)


@transaction.atomic
def restore_charge_type(actor: Membership, ct: ChargeType, request=None) -> None:
    _same_org(actor, ct)
    require(actor, "charges.manage")
    if ct.is_archived:
        ct.restore()
        audit.record("charge_type.restore", actor=actor.user, organization=actor.organization, obj=ct,
                     request=request)
