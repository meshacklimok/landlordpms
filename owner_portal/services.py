"""Inviting property owners to the owner portal, claiming the invitation and stopping access (D-128).

Staff need `owners.invite_portal` and must see every one of the owner's properties: the portal shows
the owner all of them, so nobody can hand out more than they can see themselves.
"""

import hashlib
import secrets
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from notifications.email_html import send_branded
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership, User
from accounts.permissions import require
from audit import services as audit
from notifications.providers.mail import MESSAGES, mailer_for
from notifications.providers.sms import send_sms
from properties.models import Property, PropertyOwner, owned_by
from reports.statements import fully_sees

from .models import OwnerAccount, OwnerInvitation

INVITATION_TTL = timedelta(days=7)
INVALID = _("This invitation is invalid or has expired. Ask your property manager for a new one.")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _check(actor: Membership, owner: PropertyOwner) -> None:
    require(actor, "owners.invite_portal")
    if owner.organization_id != actor.organization_id or not fully_sees(actor, owner):
        raise PermissionDenied(_("You can only invite owners whose properties you all see."))


def live_account(owner: PropertyOwner) -> OwnerAccount | None:
    return OwnerAccount.objects.filter(owner=owner, revoked_at__isnull=True).select_related("user").first()


def pending_invitation(owner: PropertyOwner) -> OwnerInvitation | None:
    return (OwnerInvitation.objects.filter(owner=owner, accepted_at__isnull=True, revoked_at__isnull=True,
                                           expires_at__gt=timezone.now()).order_by("-created_at").first())


@transaction.atomic
def invite_owner(actor: Membership, owner: PropertyOwner, *, request=None) -> tuple[OwnerInvitation, str]:
    """A new invitation, replacing any pending one. Returns it with the raw token for the link."""
    _check(actor, owner)
    owner = PropertyOwner.objects.select_for_update().get(pk=owner.pk)
    if not owner.phone:
        raise ValidationError(_("Add the owner's phone number first: they log in with it."))
    if live_account(owner):
        raise ValidationError(_("This owner can already use the owner portal."))
    if not Property.all_objects.filter(owned_by(owner)).exists():
        raise ValidationError(_("This owner has no properties yet, so the portal would be empty."))
    now = timezone.now()
    OwnerInvitation.objects.filter(owner=owner, accepted_at__isnull=True, revoked_at__isnull=True).update(
        revoked_at=now, updated_at=now)
    token = secrets.token_urlsafe(32)
    invitation = OwnerInvitation.objects.create(
        organization=actor.organization, owner=owner, phone=owner.phone, email=owner.email,
        token_hash=_hash_token(token), invited_by=actor.user, expires_at=now + INVITATION_TTL)
    audit.record("owner_portal.invite", actor=actor.user, organization=actor.organization, obj=owner,
                 request=request, changes={"phone": owner.phone, "email": owner.email})
    return invitation, token


def send_invitation(invitation: OwnerInvitation, accept_url: str) -> bool:
    """SMS to the owner's phone and, when they have one, an email. Whether at least one went out."""
    org = invitation.organization
    text = (_("%(org)s invites you to see your property statements and payments online. Open: %(url)s")
            % {"org": org.name, "url": accept_url})
    invitation.sent_by_sms = send_sms(invitation.phone, text, org=org).ok
    if invitation.email:
        body = "\n".join([
            _("Dear %(owner)s,") % {"owner": invitation.owner.name},
            "",
            _("%(org)s invites you to the owner portal, where you can see your monthly statements, payments made "
              "to you and your properties at any time.") % {"org": org.display_name},
            "",
            _("Open this link within 7 days and log in with your phone number %(phone)s:")
            % {"phone": invitation.phone},
            accept_url,
            "",
            org.display_name,
        ])
        try:
            send_branded(_("Your owner portal at %(org)s") % {"org": org.display_name}, body, [invitation.email],
                         using=mailer_for(MESSAGES), org_name=org.display_name, org=org,
                         button_label=_("Open your owner portal"))
        except Exception:  # noqa: BLE001 - a failed email must not lose the invitation
            invitation.sent_by_email = False
        else:
            invitation.sent_by_email = True
    invitation.save(update_fields=["sent_by_sms", "sent_by_email", "updated_at"])
    return invitation.sent_by_sms or invitation.sent_by_email


def get_invitation(token: str) -> OwnerInvitation | None:
    if not token:
        return None
    return (OwnerInvitation.objects.select_related("organization", "owner")
            .filter(token_hash=_hash_token(token)).first())


@transaction.atomic
def accept_invitation(token: str, user: User, *, request=None) -> OwnerAccount:
    invitation = get_invitation(token)
    if invitation is None or not invitation.is_pending:
        raise ValidationError(INVALID)
    invitation = (OwnerInvitation.objects.select_for_update(of=("self",)).select_related("owner", "organization")
                  .get(pk=invitation.pk))
    if not invitation.is_pending:
        raise ValidationError(INVALID)
    owner = PropertyOwner.objects.select_for_update().get(pk=invitation.owner_id)
    if not user.phone_verified or user.phone != invitation.phone or owner.phone != invitation.phone:
        raise PermissionDenied(_("Log in with the verified phone number this invitation was sent to."))
    if invitation.organization.archived_at is not None:
        raise ValidationError(INVALID)
    account = live_account(owner)
    if account is not None and account.user_id != user.pk:
        raise ValidationError(_("Someone else already uses the owner portal for this owner."))
    if account is None:
        account = OwnerAccount.objects.create(organization=invitation.organization, owner=owner, user=user,
                                              invited_by=invitation.invited_by)
    invitation.accepted_at = timezone.now()
    invitation.accepted_by = user
    invitation.save(update_fields=["accepted_at", "accepted_by", "updated_at"])
    audit.record("owner_portal.accept", actor=user, organization=invitation.organization, obj=owner,
                 request=request)
    return account


@transaction.atomic
def revoke_invitation(actor: Membership, invitation: OwnerInvitation, *, request=None) -> None:
    _check(actor, invitation.owner)
    if invitation.accepted_at or invitation.revoked_at:
        return
    invitation.revoked_at = timezone.now()
    invitation.save(update_fields=["revoked_at", "updated_at"])
    audit.record("owner_portal.invite_revoke", actor=actor.user, organization=actor.organization,
                 obj=invitation.owner, request=request)


@transaction.atomic
def revoke_account(actor: Membership, account: OwnerAccount, *, request=None) -> None:
    _check(actor, account.owner)
    if account.revoked_at:
        return
    account.revoked_at = timezone.now()
    account.revoked_by = actor.user
    account.save(update_fields=["revoked_at", "revoked_by", "updated_at"])
    audit.record("owner_portal.revoke", actor=actor.user, organization=actor.organization, obj=account.owner,
                 request=request)
