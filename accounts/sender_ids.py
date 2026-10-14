"""An organization's own SMS sender name (D-120).

By default every SMS goes out under the platform's name (AT_SENDER_ID). An owner who wants tenants to see their own
name asks for one on the Subscription page; a Platform Admin registers it with the networks through Africa's
Talking (a one-off fee per network, 1 to 3 weeks) and approves it in Django admin. Only then do the
organization's tenant messages use it. Login codes and security alerts always speak for NyumbaPMS.
"""

import logging
import re

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from core.mail import SYSTEM, mailer_for
from django.utils import timezone
from django.utils.translation import gettext as _

from audit import services as audit

from .models import Membership, Organization
from .permissions import require

logger = logging.getLogger(__name__)

# Letters and digits, 3 to 11, at least one letter: what the Kenyan networks accept for an alphanumeric sender.
NAME_RE = re.compile(r"^(?=.*[A-Za-z])[A-Za-z0-9]{3,11}$")

Status = Organization.SenderStatus


def clean_name(name: str) -> str:
    name = (name or "").strip()
    if not NAME_RE.match(name):
        raise ValidationError(_("Use 3 to 11 letters or digits, with no spaces or symbols, e.g. KAMAUHOMES."))
    if name.lower() == (getattr(settings, "AT_SENDER_ID", "") or "").lower():
        raise ValidationError(_("That is already the name your messages go out under."))
    return name


def request_name(actor: Membership, name: str, request=None) -> None:
    """Asks for `name`. While it waits, messages keep going under the platform's name."""
    require(actor, "subscription.manage")
    org = actor.organization
    name = clean_name(name)
    if org.sms_sender_id == name and org.sms_sender_status in (Status.REQUESTED, Status.APPROVED):
        return
    before = [org.sms_sender_id, org.sms_sender_status]
    org.sms_sender_id = name
    org.sms_sender_status = Status.REQUESTED
    org.sms_sender_note = ""
    org.sms_sender_requested_at = timezone.now()
    org.sms_sender_decided_at = None
    org.save(update_fields=["sms_sender_id", "sms_sender_status", "sms_sender_note", "sms_sender_requested_at",
                            "sms_sender_decided_at", "updated_at"])
    audit.record("organization.sender_id_requested", actor=actor.user, organization=org, obj=org, request=request,
                 changes={"sms_sender": [before, [name, Status.REQUESTED]]})
    _tell_operators(org, actor)


def cancel(actor: Membership, request=None) -> None:
    """Goes back to the platform's name, whether the request was waiting or approved."""
    require(actor, "subscription.manage")
    org = actor.organization
    if not org.sms_sender_status:
        return
    before = [org.sms_sender_id, org.sms_sender_status]
    org.sms_sender_id = ""
    org.sms_sender_status = Status.NONE
    org.sms_sender_note = ""
    org.save(update_fields=["sms_sender_id", "sms_sender_status", "sms_sender_note", "updated_at"])
    audit.record("organization.sender_id_cancelled", actor=actor.user, organization=org, obj=org, request=request,
                 changes={"sms_sender": [before, ["", Status.NONE]]})


def decide(admin, org: Organization, approve: bool, note: str = "", request=None) -> None:
    """A Platform Admin approves the name once the networks have registered it, or rejects it with a note."""
    if not (admin and admin.is_staff):
        raise PermissionDenied("platform admin")
    if not org.sms_sender_id:
        raise ValidationError(_("%(org)s has not asked for a sender name.") % {"org": org.name})
    status = Status.APPROVED if approve else Status.REJECTED
    before = org.sms_sender_status
    org.sms_sender_status = status
    org.sms_sender_note = (note or "")[:200]
    org.sms_sender_decided_at = timezone.now()
    org.save(update_fields=["sms_sender_status", "sms_sender_note", "sms_sender_decided_at", "updated_at"])
    audit.record("organization.sender_id_approved" if approve else "organization.sender_id_rejected", actor=admin,
                 organization=org, obj=org, request=request,
                 changes={"sms_sender_status": [before, status], "sms_sender_id": [None, org.sms_sender_id]})


def _tell_operators(org: Organization, actor: Membership) -> None:
    from core.alerts import recipients

    to = recipients()
    if not to:
        return
    try:
        send_mail(f"SMS sender name requested: {org.sms_sender_id}",
                  f"{org.name} ({actor.user.full_name}, {actor.user.phone}) asked to send SMS as "
                  f"{org.sms_sender_id}.\n\nRegister it with Africa's Talking, then approve it in Django admin "
                  f"(Organizations, action \"Approve SMS sender names\").",
                  None, to, fail_silently=False, using=mailer_for(SYSTEM))
    except Exception:  # the request is saved; a mail outage must not undo it
        logger.exception("Could not email operators about the sender name for %s", org.pk)
