"""Inviting property owners to the owner portal, claiming the invitation and stopping access (D-128).

Staff need `owners.invite_portal` and must see every one of the owner's properties: the portal shows
the owner all of them, so nobody can hand out more than they can see themselves.
"""

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
from notifications.email_html import send_branded
from notifications.providers.mail import MESSAGES, mailer_for
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
    return (OwnerAccount.objects.filter(owner=owner, revoked_at__isnull=True, is_delegate=False)
            .select_related("user").first())


def pending_invitation(owner: PropertyOwner) -> OwnerInvitation | None:
    return (OwnerInvitation.objects.filter(owner=owner, is_delegate=False, accepted_at__isnull=True,
                                           revoked_at__isnull=True, expires_at__gt=timezone.now())
            .order_by("-created_at").first())


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
    OwnerInvitation.objects.filter(owner=owner, is_delegate=False, accepted_at__isnull=True,
                                   revoked_at__isnull=True).update(
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
    from notifications.delivery import send_sms_for

    invitation.sent_by_sms = send_sms_for(org, invitation.phone, text, what="Owner portal invitation").ok
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
    if not user.phone_verified or user.phone != invitation.phone or (
            not invitation.is_delegate and owner.phone != invitation.phone):
        raise PermissionDenied(_("Log in with the verified phone number this invitation was sent to."))
    if invitation.organization.archived_at is not None:
        raise ValidationError(INVALID)
    if invitation.is_delegate:
        return _accept_delegate(invitation, owner, user, request)
    account = live_account(owner)
    if OwnerAccount.objects.filter(owner=owner, user=user, revoked_at__isnull=True, is_delegate=True).exists():
        raise ValidationError(_("You already see this portal as a guest. Ask the owner to remove you first."))
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
    now = timezone.now()
    account.revoked_at = now
    account.revoked_by = actor.user
    account.save(update_fields=["revoked_at", "revoked_by", "updated_at"])
    OwnerAccount.objects.filter(owner=account.owner, is_delegate=True, revoked_at__isnull=True).update(
        revoked_at=now, revoked_by=actor.user, updated_at=now)
    OwnerInvitation.objects.filter(owner=account.owner, is_delegate=True, accepted_at__isnull=True,
                                   revoked_at__isnull=True).update(revoked_at=now, updated_at=now)
    audit.record("owner_portal.revoke", actor=actor.user, organization=actor.organization, obj=account.owner,
                 request=request)


# People the owner chooses (D-217) ----------------------------------------------------------------------------

MAX_GUESTS = 3


def guests(owner: PropertyOwner):
    """The owner's live guests and invitations still open, for the page where they share the portal."""
    return {
        "accounts": list(OwnerAccount.objects.filter(owner=owner, is_delegate=True, revoked_at__isnull=True)
                         .select_related("user").order_by("created_at")),
        "invitations": list(OwnerInvitation.objects.filter(owner=owner, is_delegate=True, accepted_at__isnull=True,
                                                           revoked_at__isnull=True, expires_at__gt=timezone.now())
                            .order_by("created_at")),
    }


@transaction.atomic
def invite_guest(account: OwnerAccount, name: str, phone: str, *, request=None) -> tuple[OwnerInvitation, str]:
    """The owner shares the portal, read-only, with a person they choose. The link goes to that person's phone."""
    from core.phone import InvalidPhoneNumber, normalize_phone

    if account.revoked_at is not None or account.is_delegate:
        raise PermissionDenied("portal")
    name = (name or "").strip()[:120]
    if not name:
        raise ValidationError(_("Enter the person's name."))
    try:
        phone = normalize_phone(phone)
    except InvalidPhoneNumber as exc:
        raise ValidationError(str(exc)) from None
    owner = PropertyOwner.objects.select_for_update().get(pk=account.owner_id)
    if phone == owner.phone:
        raise ValidationError(_("That is your own number: you already have access."))
    found = guests(owner)
    if len(found["accounts"]) + len(found["invitations"]) >= MAX_GUESTS:
        raise ValidationError(_("You can share with up to %(n)s people. Remove one first.") % {"n": MAX_GUESTS})
    if any(a.user.phone == phone for a in found["accounts"]) or any(i.phone == phone for i in found["invitations"]):
        raise ValidationError(_("This person already has access or an invitation."))
    token = secrets.token_urlsafe(32)
    invitation = OwnerInvitation.objects.create(
        organization=account.organization, owner=owner, phone=phone, is_delegate=True, delegate_name=name,
        token_hash=_hash_token(token), invited_by=account.user, expires_at=timezone.now() + INVITATION_TTL)
    audit.record("owner_portal.guest_invite", actor=account.user, organization=account.organization, obj=owner,
                 request=request, changes={"name": name, "phone": phone})
    return invitation, token


def send_guest_invitation(invitation: OwnerInvitation, accept_url: str) -> bool:
    from notifications.delivery import send_sms_for

    org = invitation.organization
    text = (_("%(owner)s shared their property statements with you on %(org)s. Open: %(url)s")
            % {"owner": invitation.owner.name, "org": org.name, "url": accept_url})
    invitation.sent_by_sms = send_sms_for(org, invitation.phone, text, what="Owner portal guest invitation").ok
    invitation.save(update_fields=["sent_by_sms", "updated_at"])
    return invitation.sent_by_sms


def _accept_delegate(invitation, owner, user, request) -> OwnerAccount:
    if OwnerAccount.objects.filter(owner=owner, user=user, revoked_at__isnull=True).exists():
        raise ValidationError(_("You already have access to this portal."))
    account = OwnerAccount.objects.create(
        organization=invitation.organization, owner=owner, user=user, invited_by=invitation.invited_by,
        is_delegate=True, delegate_name=invitation.delegate_name)
    invitation.accepted_at = timezone.now()
    invitation.accepted_by = user
    invitation.save(update_fields=["accepted_at", "accepted_by", "updated_at"])
    audit.record("owner_portal.guest_accept", actor=user, organization=invitation.organization, obj=owner,
                 request=request)
    return account


@transaction.atomic
def remove_guest(account: OwnerAccount, *, account_id=None, invitation_id=None, request=None) -> None:
    """The owner removes a guest or cancels an open invitation."""
    if account.revoked_at is not None or account.is_delegate:
        raise PermissionDenied("portal")
    now = timezone.now()
    done = 0
    if account_id:
        done += OwnerAccount.objects.filter(owner=account.owner, is_delegate=True, revoked_at__isnull=True,
                                            public_id=account_id).update(revoked_at=now, revoked_by=account.user,
                                                                         updated_at=now)
    if invitation_id:
        done += OwnerInvitation.objects.filter(owner=account.owner, is_delegate=True, accepted_at__isnull=True,
                                               revoked_at__isnull=True, public_id=invitation_id
                                               ).update(revoked_at=now, updated_at=now)
    if done:
        audit.record("owner_portal.guest_remove", actor=account.user, organization=account.organization,
                     obj=account.owner, request=request)
