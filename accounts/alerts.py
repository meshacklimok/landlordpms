"""Security alerts (D-108 item 2).

Read from the audit trail, so a change is alerted on whichever view, API call or admin action made it:

- the account holder gets an SMS when two-step login is switched off or reset, or a recovery code is used
  (a changed password already sends one, from accounts.identity);
- those who manage the organization get an inbox message when someone changes roles, staff or what may be
  done, and also an SMS when money or data could be sent elsewhere: M-Pesa settings, a payment account, an API
  key, a webhook, or two-step login no longer being required. The SMS goes to the one who made the change too:
  if their account was taken over, that is how they find out.

Alerts are sent after the change is committed, and a failure to send never undoes the change.
"""

import logging

from django.contrib.auth.signals import user_login_failed
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from audit.models import AuditEvent
from core import watch
from notifications.providers.sms import send_sms

from .models import User

logger = logging.getLogger(__name__)

_IF_NOT_YOU = gettext_lazy("If this wasn't you, reset your password and contact support now.")

# Action → what the SMS to the account holder says.
ACCOUNT = {
    "mfa.disable": gettext_lazy("Two-step login was switched off on your NyumbaPMS account."),
    "mfa.admin_reset": gettext_lazy("Support switched off two-step login on your NyumbaPMS account."),
    "mfa.recovery_code_used": gettext_lazy("A recovery code was used to log in to your NyumbaPMS account."),
}

# Action → (what was done, as "{actor} ...", and whether it also goes by SMS). %(obj)s is the record's name.
ORGANIZATION = {
    "membership.change_role": (gettext_lazy("changed the role of %(obj)s"), False),
    "membership.override": (gettext_lazy("changed what %(obj)s may do"), False),
    "role.update": (gettext_lazy("changed the role %(obj)s"), False),
    "invitation.accept": (gettext_lazy("joined the organization"), False),
    "organization.require_mfa_off": (gettext_lazy("stopped requiring two-step login for staff"), True),
    "mpesa.credentials": (gettext_lazy("changed the M-Pesa settings of %(obj)s"), True),
    "payment_account.create": (gettext_lazy("added the payment account %(obj)s"), True),
    "api_key.create": (gettext_lazy("created the API key %(obj)s"), True),
    "webhook.create": (gettext_lazy("added a webhook to %(obj)s"), True),
    "webhook.update": (gettext_lazy("changed the webhook to %(obj)s"), True),
    "organization.export_data": (gettext_lazy("downloaded all the organization's data"), True),
}


@receiver(user_login_failed, dispatch_uid="accounts_alerts_login_failed")
def _login_failed(sender, credentials=None, request=None, **kwargs):
    watch.count("signin")


@receiver(post_save, sender=AuditEvent, dispatch_uid="accounts_alerts_audit")
def _audited(sender, instance: AuditEvent, created: bool, **kwargs):
    if not created:
        return
    if instance.action == "mfa.failed":
        watch.count("two_step")
    elif instance.action in ACCOUNT or instance.action in ORGANIZATION:
        pk = instance.pk
        transaction.on_commit(lambda: send(pk))


def send(event_pk: int) -> int:
    """Sends the alerts for one audit event. Returns how many messages went out."""
    try:
        event = AuditEvent.objects.select_related("actor", "organization").get(pk=event_pk)
        if event.action in ACCOUNT:
            return _account(event)
        return _organization(event)
    except Exception:
        logger.exception("Security alert for audit event %s was not sent", event_pk)
        return 0


def _subject_user(event: AuditEvent) -> User | None:
    if event.object_type != User._meta.label_lower:
        return event.actor
    key = "pk" if event.object_id.isdigit() else "public_id"  # audit stores the public id when there is one
    return User.objects.filter(**{key: event.object_id}).first()


def _account(event: AuditEvent) -> int:
    user = _subject_user(event)
    if user is None or not user.phone:
        return 0
    text = str(ACCOUNT[event.action])
    if event.action == "mfa.recovery_code_used":
        left = (event.changes.get("left") or [None, None])[1]
        if left is not None:
            text += " " + _("%(n)s recovery codes are left.") % {"n": left}
    send_sms(user.phone, f"{text} {_IF_NOT_YOU}")
    return 1


def _organization(event: AuditEvent) -> int:
    from notifications.delivery import notify, staff_with  # notifications imports accounts

    org = event.organization
    if org is None:
        return 0
    what, by_sms = ORGANIZATION[event.action]
    what = str(what) % {"obj": event.object_repr or "-"}
    actor = str(event.actor) if event.actor else _("Someone")
    when = timezone.localtime(event.created_at).strftime("%d %b %Y %H:%M")
    sent = 0
    for user in staff_with(org, "organization.manage"):
        is_actor = event.actor_id == user.pk
        if not is_actor and notify(org, "security_change", user=user, created_by=event.actor, urgent=True,
                                   context={"actor": actor, "what": what, "when": when},
                                   dedupe_key=f"security_change:{event.pk}:{user.pk}"):
            sent += 1
        if by_sms and user.phone:
            send_sms(user.phone, _("%(org)s on NyumbaPMS: %(actor)s %(what)s on %(when)s.")
                     % {"org": org.display_name, "actor": _("you") if is_actor else actor, "what": what, "when": when}
                     + f" {_IF_NOT_YOU}")
            sent += 1
    return sent
