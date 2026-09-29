"""Inviting tenants to the portal, claiming the invitation and revoking access (D-055)."""

import hashlib
import secrets
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership, User
from accounts.permissions import require
from audit import services as audit
from core.sms import send_sms
from leases.models import Lease, LeaseTenant
from notifications import services as notifications
from notifications.catalog import SMS
from tenants.models import Tenant
from tenants.services import visible_tenants

from .models import PortalInvitation, TenantAccount

INVITATION_TTL = timedelta(days=7)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _check_tenant(actor: Membership, tenant: Tenant) -> None:
    require(actor, "tenants.invite_portal")
    if tenant.organization_id != actor.organization_id:
        raise PermissionDenied(_("That record belongs to another organization."))
    if not visible_tenants(actor, Tenant.all_objects.filter(pk=tenant.pk)).exists():
        raise PermissionDenied(_("You cannot see this tenant."))


def live_account(tenant: Tenant) -> TenantAccount | None:
    return TenantAccount.objects.filter(tenant=tenant, revoked_at__isnull=True).select_related("user").first()


def pending_invitation(tenant: Tenant) -> PortalInvitation | None:
    return (PortalInvitation.objects.filter(tenant=tenant, accepted_at__isnull=True, revoked_at__isnull=True,
                                            expires_at__gt=timezone.now()).order_by("-created_at").first())


def has_leases(tenant: Tenant) -> bool:
    """Whether the tenant is on any lease past draft: otherwise the portal would be empty."""
    return LeaseTenant.objects.filter(tenant=tenant, lease__archived_at__isnull=True).exclude(
        lease__status=Lease.Status.DRAFT).exists()


@transaction.atomic
def invite_tenant(actor: Membership, tenant: Tenant, *, request=None) -> tuple[PortalInvitation, str]:
    """A new invitation, replacing any pending one. Returns it with the raw token for the link."""
    _check_tenant(actor, tenant)
    tenant = Tenant.all_objects.select_for_update().get(pk=tenant.pk)
    if tenant.is_archived:
        raise ValidationError(_("This tenant is archived."))
    if live_account(tenant):
        raise ValidationError(_("This tenant can already use the portal."))
    if not has_leases(tenant):
        raise ValidationError(_("Add this tenant to a lease first: the portal shows their leases."))
    if not notifications.channel_allowed(tenant, SMS):
        raise ValidationError(_("This tenant has turned off SMS, so the invitation cannot be sent."))
    now = timezone.now()
    PortalInvitation.objects.filter(tenant=tenant, accepted_at__isnull=True, revoked_at__isnull=True).update(
        revoked_at=now, updated_at=now)
    token = secrets.token_urlsafe(32)
    invitation = PortalInvitation.objects.create(
        organization=actor.organization, tenant=tenant, phone=tenant.phone, token_hash=_hash_token(token),
        invited_by=actor.user, expires_at=now + INVITATION_TTL)
    audit.record("portal.invite", actor=actor.user, organization=actor.organization, obj=tenant, request=request,
                 changes={"phone": tenant.phone})
    return invitation, token


def send_invitation(invitation: PortalInvitation, accept_url: str) -> bool:
    """Whether the SMS went out."""
    return send_sms(invitation.phone,
             _("%(org)s invites you to see your rent, statements and receipts online. Open: %(url)s")
             % {"org": invitation.organization.name, "url": accept_url}).ok


def get_invitation(token: str) -> PortalInvitation | None:
    if not token:
        return None
    return (PortalInvitation.objects.select_related("organization", "tenant")
            .filter(token_hash=_hash_token(token)).first())


@transaction.atomic
def accept_invitation(token: str, user: User, *, request=None) -> TenantAccount:
    invitation = get_invitation(token)
    if invitation is None or not invitation.is_pending:
        raise ValidationError(_("This invitation is invalid or has expired. Ask your landlord for a new one."))
    invitation = PortalInvitation.objects.select_for_update().select_related("tenant", "organization").get(
        pk=invitation.pk)
    if not invitation.is_pending:
        raise ValidationError(_("This invitation is invalid or has expired. Ask your landlord for a new one."))
    tenant = Tenant.all_objects.select_for_update().get(pk=invitation.tenant_id)
    if not user.phone_verified or user.phone != invitation.phone or tenant.phone != invitation.phone:
        raise PermissionDenied(_("Log in with the verified phone number this invitation was sent to."))
    if tenant.is_archived or invitation.organization.archived_at is not None:
        raise ValidationError(_("This invitation is invalid or has expired. Ask your landlord for a new one."))
    account = live_account(tenant)
    if account is not None and account.user_id != user.pk:
        raise ValidationError(_("Someone else already uses the portal for this tenant."))
    if account is None:
        account = TenantAccount.objects.create(organization=invitation.organization, tenant=tenant, user=user,
                                               invited_by=invitation.invited_by)
    invitation.accepted_at = timezone.now()
    invitation.accepted_by = user
    invitation.save(update_fields=["accepted_at", "accepted_by", "updated_at"])
    audit.record("portal.accept", actor=user, organization=invitation.organization, obj=tenant, request=request)
    return account


@transaction.atomic
def revoke_invitation(actor: Membership, invitation: PortalInvitation, *, request=None) -> None:
    _check_tenant(actor, invitation.tenant)
    if invitation.accepted_at or invitation.revoked_at:
        return
    invitation.revoked_at = timezone.now()
    invitation.save(update_fields=["revoked_at", "updated_at"])
    audit.record("portal.invite_revoke", actor=actor.user, organization=actor.organization, obj=invitation.tenant,
                 request=request)


@transaction.atomic
def revoke_account(actor: Membership, account: TenantAccount, *, request=None) -> None:
    _check_tenant(actor, account.tenant)
    if account.revoked_at:
        return
    account.revoked_at = timezone.now()
    account.revoked_by = actor.user
    account.save(update_fields=["revoked_at", "revoked_by", "updated_at"])
    audit.record("portal.revoke", actor=actor.user, organization=actor.organization, obj=account.tenant,
                 request=request)
