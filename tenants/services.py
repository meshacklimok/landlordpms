"""Tenant services (doc 11 §6, doc 13).

Rules:
- Phones are stored in E.164. A phone may repeat; callers warn with `tenants_with_phone`.
- ID number and KRA PIN are sensitive: only members with tenants.view_sensitive may set or change them.
- An ID document identifies one tenant per organization, archived tenants included.
- Status is never set by hand; leases drive it.
- Tenants are archived, never deleted. An active tenant cannot be archived.
- Every change is audited. Sensitive values are masked in the audit log.
"""

import re

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Q
from django.db.models.functions import Upper
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import accessible_property_ids, can, require
from audit import services as audit
from core.phone import InvalidPhoneNumber, normalize_phone

from .models import Tenant

TENANT_FIELDS = ("kind", "name", "contact_person", "phone", "alt_phone", "email",
                 "emergency_contact_name", "emergency_contact_phone", "notes")
SENSITIVE_FIELDS = Tenant.SENSITIVE_FIELDS
PHONE_FIELDS = ("phone", "alt_phone", "emergency_contact_phone")

KRA_PIN_RE = re.compile(r"^[AP]\d{9}[A-Z]$")


def visible_tenants(membership: Membership, queryset=None):
    """Tenants this membership may see.

    Members with every property see every tenant. Scoped members see the tenants
    they added and the tenants on leases (any status) in their properties.
    """
    from leases.models import LeaseTenant

    qs = (queryset if queryset is not None else Tenant.objects.all()).for_org(membership.organization)
    ids = accessible_property_ids(membership)
    if ids is None:
        return qs
    on_lease = LeaseTenant.objects.filter(tenant=OuterRef("pk"), lease__unit__property_id__in=ids)
    return qs.filter(Q(created_by_id=membership.user_id) | Exists(on_lease))


def tenants_with_phone(org, phone: str, exclude_pk=None):
    qs = Tenant.all_objects.for_org(org).filter(Q(phone=phone) | Q(alt_phone=phone))
    return qs.exclude(pk=exclude_pk) if exclude_pk else qs


def _same_org(actor: Membership, tenant: Tenant) -> None:
    if tenant.organization_id != actor.organization_id:
        raise PermissionDenied(_("That record belongs to another organization."))


def _require_visible(actor: Membership, tenant: Tenant, capability: str) -> None:
    _same_org(actor, tenant)
    require(actor, capability)
    if not visible_tenants(actor, Tenant.all_objects.all()).filter(pk=tenant.pk).exists():
        raise PermissionDenied(capability)


def _clean(tenant: Tenant) -> None:
    errors = {}
    tenant.name = tenant.name.strip()
    tenant.id_number = tenant.id_number.strip().upper().replace(" ", "")
    tenant.kra_pin = tenant.kra_pin.strip().upper().replace(" ", "")
    for field in PHONE_FIELDS:
        value = getattr(tenant, field)
        if not value:
            continue
        try:
            setattr(tenant, field, normalize_phone(value))
        except InvalidPhoneNumber as exc:
            errors[field] = str(exc)
    if tenant.alt_phone and tenant.alt_phone == tenant.phone:
        tenant.alt_phone = ""
    if tenant.id_number and not tenant.id_type:
        errors["id_type"] = _("Choose the ID type.")
    if tenant.kra_pin and not KRA_PIN_RE.match(tenant.kra_pin):
        errors["kra_pin"] = _("A KRA PIN looks like A123456789B.")
    if tenant.kind == Tenant.Kind.INDIVIDUAL and tenant.id_type == Tenant.IdType.COMPANY_REG:
        errors["id_type"] = _("Company registration is for company tenants.")
    try:
        tenant.full_clean(exclude=["organization", "created_by", *errors], validate_unique=False,
                          validate_constraints=False)
    except ValidationError as exc:
        errors.update(exc.message_dict)
    if errors:
        raise ValidationError(errors)
    if tenant.id_number and _id_taken(tenant):
        raise ValidationError({"id_number": _("Another tenant already has this ID number.")})


def _id_taken(tenant: Tenant) -> bool:
    qs = (Tenant.all_objects.for_org(tenant.organization).annotate(uid=Upper("id_number"))
          .filter(id_type=tenant.id_type, uid=tenant.id_number))
    if tenant.pk:
        qs = qs.exclude(pk=tenant.pk)
    return qs.exists()


def _snapshot(tenant: Tenant) -> dict:
    return {f: getattr(tenant, f) for f in (*TENANT_FIELDS, *SENSITIVE_FIELDS)}


def _masked(changes: dict) -> dict:
    """Keep the fact that an ID number or KRA PIN changed, not the value."""
    def mask(value):
        return f"…{value[-3:]}" if value else ""
    return {k: ([mask(v) for v in pair] if k in ("id_number", "kra_pin") else pair) for k, pair in changes.items()}


def _save(tenant: Tenant) -> None:
    try:
        with transaction.atomic():
            tenant.save()
    except IntegrityError:
        raise ValidationError({"id_number": _("Another tenant already has this ID number.")}) from None


def _check_sensitive(actor: Membership, fields: dict) -> None:
    if any(fields.get(f) for f in SENSITIVE_FIELDS) and not can(actor, "tenants.view_sensitive"):
        raise PermissionDenied("tenants.view_sensitive")


@transaction.atomic
def create_tenant(actor: Membership, *, name: str, phone: str, request=None, **fields) -> Tenant:
    require(actor, "tenants.manage")
    _check_sensitive(actor, fields)
    allowed = {k: v for k, v in fields.items() if k in TENANT_FIELDS or k in SENSITIVE_FIELDS}
    tenant = Tenant(organization=actor.organization, name=name, phone=phone, created_by=actor.user,
                    **{k: (v if v is not None else "") for k, v in allowed.items()})
    _clean(tenant)
    _save(tenant)
    audit.record("tenant.create", actor=actor.user, organization=actor.organization, obj=tenant, request=request,
                 changes=_masked({k: [None, v] for k, v in _snapshot(tenant).items() if v}))
    return tenant


@transaction.atomic
def update_tenant(actor: Membership, tenant: Tenant, *, request=None, **fields) -> Tenant:
    _require_visible(actor, tenant, "tenants.manage")
    if tenant.is_archived:
        raise ValidationError(_("Restore this tenant before editing."))
    sensitive_changes = {f: fields[f] for f in SENSITIVE_FIELDS
                         if f in fields and (fields[f] or "") != getattr(tenant, f)}
    if sensitive_changes and not can(actor, "tenants.view_sensitive"):
        raise PermissionDenied("tenants.view_sensitive")
    before = _snapshot(tenant)
    for k, v in fields.items():
        if k in TENANT_FIELDS or k in SENSITIVE_FIELDS:
            setattr(tenant, k, v if v is not None else "")
    _clean(tenant)
    _save(tenant)
    changes = audit.diff(before, _snapshot(tenant))
    if changes:
        audit.record("tenant.update", actor=actor.user, organization=actor.organization, obj=tenant,
                     request=request, changes=_masked(changes))
    return tenant


@transaction.atomic
def archive_tenant(actor: Membership, tenant: Tenant, request=None) -> None:
    _require_visible(actor, tenant, "tenants.manage")
    if tenant.is_archived:
        return
    if tenant.status == Tenant.Status.ACTIVE:
        raise ValidationError(_("End this tenant's lease before archiving them."))
    tenant.archived_at, tenant.archived_by = timezone.now(), actor.user
    tenant.save(update_fields=["archived_at", "archived_by", "updated_at"])
    audit.record("tenant.archive", actor=actor.user, organization=actor.organization, obj=tenant, request=request)


@transaction.atomic
def restore_tenant(actor: Membership, tenant: Tenant, request=None) -> None:
    _require_visible(actor, tenant, "tenants.manage")
    if not tenant.is_archived:
        return
    tenant.archived_at = tenant.archived_by = None
    tenant.save(update_fields=["archived_at", "archived_by", "updated_at"])
    audit.record("tenant.restore", actor=actor.user, organization=actor.organization, obj=tenant, request=request)
