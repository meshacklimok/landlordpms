"""Access services: organizations, roles, memberships, overrides, scope, invitations.

Safety rules (doc 13, D-023, D-024):
- Every target must belong to the actor's organization.
- No privilege escalation: you can only grant capabilities you hold yourself,
  and only give property access you have yourself.
- Only Owners can touch the Owner role or Owner memberships.
- The Owner role keeps OWNER_CRITICAL; every organization keeps at least one active Owner.
- Every change is audited.
"""

import hashlib
import secrets
from collections.abc import Iterable
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from audit import services as audit
from core.phone import normalize_phone
from core.sms import send_sms

from .capabilities import CAPABILITIES, OWNER_CRITICAL, ROLE_TEMPLATES
from .models import (
    Capability,
    Invitation,
    Membership,
    MembershipCapability,
    Organization,
    PropertyAccess,
    Role,
    RoleCapability,
    RoleTemplate,
    User,
)
from .permissions import clear_cache, effective_capabilities, require

INVITATION_TTL = timedelta(days=7)


# ---------------------------------------------------------------------------
# Catalog sync
# ---------------------------------------------------------------------------


def sync_access_catalog() -> dict[str, int]:
    """Upsert capabilities from code; seed missing role templates. Idempotent."""
    stats = {"created": 0, "updated": 0, "deactivated": 0, "templates_created": 0}
    codenames = set()
    for cap in CAPABILITIES:
        codenames.add(cap.codename)
        fields = {
            "module": cap.module,
            "description": cap.description,
            "sensitive": cap.sensitive,
            "org_wide": cap.org_wide,
            "read_only_safe": cap.read_only_safe,
            "is_active": True,
        }
        obj, created = Capability.objects.get_or_create(codename=cap.codename, defaults=fields)
        if created:
            stats["created"] += 1
        elif any(getattr(obj, k) != v for k, v in fields.items()):
            Capability.objects.filter(pk=obj.pk).update(**fields)
            stats["updated"] += 1
    stats["deactivated"] = (
        Capability.objects.filter(is_active=True).exclude(codename__in=codenames).update(is_active=False)
    )

    by_codename = {c.codename: c for c in Capability.objects.filter(is_active=True)}
    for order, tpl in enumerate(ROLE_TEMPLATES):
        template, created = RoleTemplate.objects.get_or_create(
            key=tpl.key,
            defaults={
                "name": tpl.name,
                "description": tpl.description,
                "is_owner_template": tpl.is_owner,
                "sort_order": order,
            },
        )
        if created:
            template.capabilities.set([by_codename[c] for c in tpl.capabilities])
            stats["templates_created"] += 1
        elif tpl.is_owner:
            # The Owner template always carries every capability, including new ones.
            template.capabilities.add(*by_codename.values())
    return stats


# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------


def copy_role_templates(organization: Organization) -> dict[str, Role]:
    roles = {}
    templates = RoleTemplate.objects.filter(is_active=True).prefetch_related("capabilities")
    for template in templates:
        role = Role.objects.create(
            organization=organization,
            name=template.name,
            description=template.description,
            based_on_template=template,
            is_owner_role=template.is_owner_template,
        )
        caps = list(template.capabilities.filter(is_active=True))
        if template.is_owner_template:
            caps = list(Capability.objects.filter(is_active=True))
        RoleCapability.objects.bulk_create([RoleCapability(role=role, capability=c) for c in caps])
        roles[template.key] = role
    if not any(r.is_owner_role for r in roles.values()):
        raise ValidationError("No active Owner role template; run `manage.py sync_access_catalog`.")
    return roles


@transaction.atomic
def create_organization(*, user: User, name: str, org_type: str = Organization.Type.INDIVIDUAL,
                        request=None) -> Membership:
    """Organization + roles + Owner membership in one transaction (doc 11 §16)."""
    if not user.phone_verified:
        raise PermissionDenied(_("Verify your phone number first."))
    org = Organization.objects.create(name=name.strip(), org_type=org_type, created_by=user,
                                      billing_phone=user.phone, billing_email=user.email or "")
    roles = copy_role_templates(org)
    owner_role = next(r for r in roles.values() if r.is_owner_role)
    membership = Membership.objects.create(user=user, organization=org, role=owner_role, all_properties=True)
    audit.record("organization.create", actor=user, organization=org, obj=org, request=request,
                 changes={"name": org.name, "type": org.org_type})
    return membership


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def _same_org(actor: Membership, *objs) -> None:
    for obj in objs:
        if obj.organization_id != actor.organization_id:
            raise PermissionDenied(_("That belongs to another organization."))


def _no_escalation(actor: Membership, codenames: Iterable[str]) -> None:
    missing = set(codenames) - effective_capabilities(actor)
    if missing:
        raise PermissionDenied(
            _("You can only grant capabilities you hold yourself: %(caps)s")
            % {"caps": ", ".join(sorted(missing))}
        )


def _require_owner_for(actor: Membership, *, role: Role | None = None, membership: Membership | None = None):
    touches_owner = (role is not None and role.is_owner_role) or (
        membership is not None and membership.role.is_owner_role
    )
    if touches_owner and not actor.role.is_owner_role:
        raise PermissionDenied(_("Only an Owner can change Owners or the Owner role."))


def _lock_org(org: Organization) -> Organization:
    return Organization.objects.select_for_update().get(pk=org.pk)


def active_owner_count(org: Organization) -> int:
    return Membership.objects.filter(
        organization=org, is_active=True, role__is_owner_role=True, user__is_active=True
    ).count()


def _ensure_not_last_owner(membership: Membership) -> None:
    if membership.role.is_owner_role and membership.is_active and active_owner_count(membership.organization) <= 1:
        raise ValidationError(_("Every organization needs at least one active Owner."))


def _capabilities_for(codenames: Iterable[str]) -> list[Capability]:
    codenames = set(codenames)
    caps = list(Capability.objects.filter(codename__in=codenames, is_active=True))
    unknown = codenames - {c.codename for c in caps}
    if unknown:
        raise ValidationError(_("Unknown capabilities: %(caps)s") % {"caps": ", ".join(sorted(unknown))})
    return caps


def role_codenames(role: Role) -> set[str]:
    return set(role.capabilities.filter(is_active=True).values_list("codename", flat=True))


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


@transaction.atomic
def create_role(actor: Membership, *, name: str, description: str = "", capabilities: Iterable[str] = (),
                request=None) -> Role:
    require(actor, "roles.manage")
    codenames = set(capabilities)
    _no_escalation(actor, codenames)
    caps = _capabilities_for(codenames)
    try:
        with transaction.atomic():
            role = Role.objects.create(organization=actor.organization, name=name.strip(), description=description)
    except IntegrityError:
        raise ValidationError(_("A role with this name already exists.")) from None
    RoleCapability.objects.bulk_create([RoleCapability(role=role, capability=c) for c in caps])
    audit.record("role.create", actor=actor.user, organization=actor.organization, obj=role, request=request,
                 changes={"capabilities": sorted(codenames)})
    return role


def clone_role(actor: Membership, role: Role, *, name: str, request=None) -> Role:
    _same_org(actor, role)
    return create_role(actor, name=name, description=role.description, capabilities=role_codenames(role),
                       request=request)


@transaction.atomic
def update_role(actor: Membership, role: Role, *, name: str | None = None, description: str | None = None,
                capabilities: Iterable[str] | None = None, request=None) -> Role:
    require(actor, "roles.manage")
    _same_org(actor, role)
    _require_owner_for(actor, role=role)
    changes = {}

    if name is not None and name.strip() != role.name:
        changes["name"] = [role.name, name.strip()]
        role.name = name.strip()
    if description is not None and description != role.description:
        changes["description"] = [role.description, description]
        role.description = description
    if changes:
        try:
            with transaction.atomic():
                role.save()
        except IntegrityError:
            raise ValidationError(_("A role with this name already exists.")) from None

    if capabilities is not None:
        new = set(capabilities)
        old = role_codenames(role)
        added, removed = new - old, old - new
        _no_escalation(actor, added)
        if role.is_owner_role and removed & OWNER_CRITICAL:
            raise ValidationError(
                _("The Owner role must keep: %(caps)s") % {"caps": ", ".join(sorted(OWNER_CRITICAL))}
            )
        if added:
            RoleCapability.objects.bulk_create(
                [RoleCapability(role=role, capability=c) for c in _capabilities_for(added)]
            )
        if removed:
            RoleCapability.objects.filter(role=role, capability__codename__in=removed).delete()
        if added or removed:
            changes["capabilities"] = {"added": sorted(added), "removed": sorted(removed)}

    if changes:
        audit.record("role.update", actor=actor.user, organization=actor.organization, obj=role,
                     request=request, changes=changes)
    return role


@transaction.atomic
def archive_role(actor: Membership, role: Role, request=None) -> None:
    require(actor, "roles.manage")
    _same_org(actor, role)
    if role.is_owner_role:
        raise ValidationError(_("The Owner role cannot be removed."))
    if Membership.all_objects.filter(role=role, archived_at__isnull=True).exists():
        raise ValidationError(_("Move this role's members to another role first."))
    role.archive(by=actor.user)
    audit.record("role.archive", actor=actor.user, organization=actor.organization, obj=role, request=request)


# ---------------------------------------------------------------------------
# Memberships
# ---------------------------------------------------------------------------


@transaction.atomic
def change_member_role(actor: Membership, membership: Membership, role: Role, request=None) -> Membership:
    require(actor, "staff.manage")
    _same_org(actor, membership, role)
    if role.archived_at is not None:
        raise ValidationError(_("That role has been removed."))
    _require_owner_for(actor, membership=membership)
    _require_owner_for(actor, role=role)
    _no_escalation(actor, role_codenames(role))
    _lock_org(actor.organization)
    if membership.role.is_owner_role and not role.is_owner_role:
        _ensure_not_last_owner(membership)
    old = membership.role
    membership.role = role
    membership.save(update_fields=["role", "updated_at"])
    clear_cache(membership)
    audit.record("membership.change_role", actor=actor.user, organization=actor.organization, obj=membership,
                 request=request, changes={"role": [old.name, role.name]})
    return membership


@transaction.atomic
def set_member_active(actor: Membership, membership: Membership, active: bool, request=None) -> Membership:
    """Suspend or reactivate a member."""
    require(actor, "staff.manage")
    _same_org(actor, membership)
    _require_owner_for(actor, membership=membership)
    _lock_org(actor.organization)
    if not active:
        _ensure_not_last_owner(membership)
    if membership.is_active != active:
        membership.is_active = active
        membership.save(update_fields=["is_active", "updated_at"])
        audit.record("membership.activate" if active else "membership.suspend", actor=actor.user,
                     organization=actor.organization, obj=membership, request=request)
    return membership


@transaction.atomic
def remove_member(actor: Membership, membership: Membership, request=None) -> None:
    require(actor, "staff.manage")
    _same_org(actor, membership)
    _require_owner_for(actor, membership=membership)
    _lock_org(actor.organization)
    _ensure_not_last_owner(membership)
    membership.is_active = False
    membership.save(update_fields=["is_active", "updated_at"])
    membership.archive(by=actor.user)
    audit.record("membership.remove", actor=actor.user, organization=actor.organization, obj=membership,
                 request=request)


@transaction.atomic
def set_override(actor: Membership, membership: Membership, codename: str, granted: bool | None,
                 request=None) -> None:
    """granted=True grants, False withholds, None clears the override."""
    require(actor, "staff.manage")
    _same_org(actor, membership)
    _require_owner_for(actor, membership=membership)
    if granted:
        _no_escalation(actor, [codename])
    if membership.role.is_owner_role and granted is False and codename in OWNER_CRITICAL:
        raise ValidationError(_("An Owner cannot be denied %(cap)s.") % {"cap": codename})
    (capability,) = _capabilities_for([codename])
    existing = MembershipCapability.objects.filter(membership=membership, capability=capability).first()
    old = None if existing is None else existing.granted
    if old == granted:
        return
    if granted is None:
        existing.delete()
    elif existing is None:
        MembershipCapability.objects.create(membership=membership, capability=capability, granted=granted,
                                            created_by=actor.user)
    else:
        existing.granted = granted
        existing.save(update_fields=["granted"])
    clear_cache(membership)
    audit.record("membership.override", actor=actor.user, organization=actor.organization, obj=membership,
                 request=request, changes={codename: [old, granted]})


def _check_scope_escalation(actor: Membership, all_properties: bool, property_ids: set[int]) -> None:
    if actor.all_properties:
        return
    if all_properties:
        raise PermissionDenied(_("You cannot give access to all properties."))
    own = set(actor.property_access.values_list("property_id", flat=True))
    if property_ids - own:
        raise PermissionDenied(_("You can only give access to properties you can access."))


def _org_property_ids(org: Organization, properties: Iterable) -> set[int]:
    from properties.models import Property

    ids = {getattr(p, "pk", p) for p in properties}
    found = set(Property.objects.for_org(org).filter(pk__in=ids).values_list("pk", flat=True))
    if found != ids:
        raise PermissionDenied(_("That property belongs to another organization."))
    return ids


@transaction.atomic
def set_property_scope(actor: Membership, membership: Membership, *, all_properties: bool,
                       properties: Iterable = (), request=None) -> None:
    require(actor, "staff.manage")
    _same_org(actor, membership)
    _require_owner_for(actor, membership=membership)
    ids = set() if all_properties else _org_property_ids(actor.organization, properties)
    _check_scope_escalation(actor, all_properties, ids)
    old_ids = set(membership.property_access.values_list("property_id", flat=True))
    old_all = membership.all_properties
    if membership.role.is_owner_role and not all_properties:
        raise ValidationError(_("Owners always have access to all properties."))
    membership.all_properties = all_properties
    membership.save(update_fields=["all_properties", "updated_at"])
    PropertyAccess.objects.filter(membership=membership).exclude(property_id__in=ids).delete()
    PropertyAccess.objects.bulk_create(
        [PropertyAccess(membership=membership, property_id=pid) for pid in ids - old_ids]
    )
    clear_cache(membership)
    if old_all != all_properties or old_ids != ids:
        audit.record("membership.scope", actor=actor.user, organization=actor.organization, obj=membership,
                     request=request, changes={"all_properties": [old_all, all_properties],
                                               "properties": [sorted(old_ids), sorted(ids)]})


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@transaction.atomic
def invite_staff(actor: Membership, *, phone: str, role: Role, full_name: str = "", email: str = "",
                 all_properties: bool = False, properties: Iterable = (), request=None) -> tuple[Invitation, str]:
    require(actor, "staff.manage")
    _same_org(actor, role)
    if role.archived_at is not None:
        raise ValidationError(_("That role has been removed."))
    _require_owner_for(actor, role=role)
    _no_escalation(actor, role_codenames(role))
    all_properties = all_properties or role.is_owner_role
    ids = set() if all_properties else _org_property_ids(actor.organization, properties)
    _check_scope_escalation(actor, all_properties, ids)
    phone = normalize_phone(phone)
    if Membership.objects.filter(organization=actor.organization, user__phone=phone).exists():
        raise ValidationError(_("This person is already a member."))

    token = secrets.token_urlsafe(32)
    invitation = Invitation.objects.create(
        organization=actor.organization, phone=phone, email=email, full_name=full_name, role=role,
        all_properties=all_properties, token_hash=_hash_token(token), invited_by=actor.user,
        expires_at=timezone.now() + INVITATION_TTL,
    )
    if ids:
        invitation.properties.set(ids)
    audit.record("invitation.create", actor=actor.user, organization=actor.organization, obj=invitation,
                 request=request, changes={"phone": phone, "role": role.name})
    return invitation, token


def send_invitation(invitation: Invitation, accept_url: str) -> None:
    send_sms(
        invitation.phone,
        _("%(org)s invited you to landlordpms as %(role)s. Accept: %(url)s")
        % {"org": invitation.organization.name, "role": invitation.role.name, "url": accept_url},
    )


def get_invitation(token: str) -> Invitation | None:
    return (
        Invitation.objects.select_related("organization", "role")
        .filter(token_hash=_hash_token(token))
        .first()
    )


@transaction.atomic
def accept_invitation(token: str, user: User, request=None) -> Membership:
    invitation = get_invitation(token)
    if invitation is None or not invitation.is_pending:
        raise ValidationError(_("This invitation is invalid or has expired."))
    invitation = Invitation.objects.select_for_update().get(pk=invitation.pk)
    if not invitation.is_pending:
        raise ValidationError(_("This invitation is invalid or has expired."))
    if not user.phone_verified or user.phone != invitation.phone:
        raise PermissionDenied(_("Log in with the verified phone number this invitation was sent to."))
    if invitation.role.archived_at is not None:
        raise ValidationError(_("The role on this invitation has been removed. Ask for a new invitation."))

    membership = Membership.all_objects.filter(user=user, organization=invitation.organization).first()
    if membership is not None and membership.archived_at is None:
        raise ValidationError(_("You are already a member of this organization."))
    if membership is None:
        membership = Membership(user=user, organization=invitation.organization)
    membership.role = invitation.role
    membership.all_properties = invitation.all_properties
    membership.is_active = True
    membership.archived_at = None
    membership.archived_by = None
    membership.invited_by = invitation.invited_by
    membership.save()
    PropertyAccess.objects.filter(membership=membership).delete()
    PropertyAccess.objects.bulk_create(
        [PropertyAccess(membership=membership, property=p) for p in invitation.properties.all()]
    )
    invitation.accepted_at = timezone.now()
    invitation.accepted_by = user
    invitation.save(update_fields=["accepted_at", "accepted_by", "updated_at"])
    audit.record("invitation.accept", actor=user, organization=invitation.organization, obj=membership,
                 request=request, changes={"role": invitation.role.name})
    return membership


def revoke_invitation(actor: Membership, invitation: Invitation, request=None) -> None:
    require(actor, "staff.manage")
    _same_org(actor, invitation)
    if invitation.accepted_at or invitation.revoked_at:
        return
    invitation.revoked_at = timezone.now()
    invitation.save(update_fields=["revoked_at", "updated_at"])
    audit.record("invitation.revoke", actor=actor.user, organization=actor.organization, obj=invitation,
                 request=request)
