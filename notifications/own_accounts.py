"""An organization's own outgoing email server and own Africa's Talking SMS account (Messages > Email, SMS).

Both are optional. Without them, mail and SMS go through the platform's accounts, mail under the organization's
name and SMS under its approved sender name (D-120). Login codes and security alerts always speak for NyumbaPMS.
"""

import smtplib

from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.core.mail.backends.smtp import EmailBackend
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import require
from audit import services as audit
from core.sms import AfricasTalkingSmsSender

from .models import EmailAccount, SmsAccount

TIMEOUT = 10


def email_account(org) -> EmailAccount | None:
    return EmailAccount.objects.filter(organization=org).first()


def usable_email(org) -> EmailAccount | None:
    account = email_account(org)
    return account if account is not None and account.usable else None


def sms_account(org) -> SmsAccount | None:
    return SmsAccount.objects.filter(organization=org).first()


def usable_sms(org) -> SmsAccount | None:
    account = sms_account(org)
    return account if account is not None and account.usable else None


def connection(account: EmailAccount):
    """A mail sender for the organization's own server (built the way Django builds a MAILERS entry)."""
    return EmailBackend(alias=f"org-{account.organization_id}", host=account.host, port=account.port,
                        username=account.username, password=account.secret("password"),
                        use_tls=account.use_tls, timeout=TIMEOUT)


def sms_sender(account: SmsAccount) -> AfricasTalkingSmsSender:
    return AfricasTalkingSmsSender(account.username, account.secret("api_key"), account.sender_id)


def _audit(action, actor, obj, request, changes=None):
    audit.record(action, actor=actor.user, organization=actor.organization, obj=obj, request=request,
                 changes=changes or {})


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


def save_email(actor: Membership, *, host: str, port: int, username: str = "", password: str = "",
               use_tls: bool = True, from_email: str, from_name: str = "", enabled: bool = True,
               request=None) -> EmailAccount:
    """Creates or updates the organization's mail server. A blank password keeps the saved one."""
    require(actor, "organization.manage")
    org = actor.organization
    host = (host or "").strip().lower()
    if not host or " " in host or "/" in host:
        raise ValidationError({"host": _("Enter the server name, such as smtp.yourcompany.co.ke.")})
    account = email_account(org)
    created = account is None
    account = account or EmailAccount(organization=org)
    changes = {}
    for name, value in (("host", host), ("port", port), ("username", (username or "").strip()),
                        ("use_tls", use_tls), ("from_email", from_email.strip().lower()),
                        ("from_name", (from_name or "").strip()), ("enabled", enabled)):
        if created or getattr(account, name) != value:
            changes[name] = [None if created else getattr(account, name), value]
            setattr(account, name, value)
    if password.strip():
        account.set_secret("password", password.strip())
        changes["password"] = [None, "changed"]  # never the value itself
    if changes:
        account.checked_at, account.check_error = None, ""
    account.updated_by = actor.user
    account.save()
    if changes:
        _audit("email_account.saved", actor, account, request, changes)
    return account


def test_email(actor: Membership, *, request=None) -> EmailAccount:
    """Sends a short test to the member's own address through the organization's server, and keeps the outcome."""
    require(actor, "organization.manage")
    account = email_account(actor.organization)
    if account is None:
        raise ValidationError(_("Save your mail server first."))
    to = actor.user.email
    if not to:
        raise ValidationError(_("Add an email address to your own profile first: the test goes there."))
    from .emails import from_address

    try:
        connection(account).send_messages([EmailMessage(
            subject=_("Test from NyumbaPMS"),
            body=_("This test went through your own mail server. Tenants will get mail from this address."),
            from_email=from_address(actor.organization, account), to=[to])])
    except (smtplib.SMTPException, OSError) as e:
        account.check_error = (str(e) or e.__class__.__name__)[:300]
    else:
        account.check_error = ""
    account.checked_at = timezone.now()
    account.save(update_fields=["check_error", "checked_at", "updated_at"])
    _audit("email_account.tested", actor, account, request, {"ok": [None, not account.check_error]})
    return account


def remove_email(actor: Membership, *, request=None) -> None:
    require(actor, "organization.manage")
    account = email_account(actor.organization)
    if account is not None:
        _audit("email_account.removed", actor, account, request, {"host": [account.host, None]})
        account.delete()


# ---------------------------------------------------------------------------
# SMS
# ---------------------------------------------------------------------------


def save_sms(actor: Membership, *, username: str, api_key: str = "", sender_id: str = "", enabled: bool = True,
             request=None) -> SmsAccount:
    """Creates or updates the organization's Africa's Talking account. A blank key keeps the saved one."""
    require(actor, "organization.manage")
    org = actor.organization
    username = (username or "").strip()
    if not username:
        raise ValidationError({"username": _("Enter your Africa's Talking username.")})
    sender_id = (sender_id or "").strip()
    if sender_id:
        from accounts.sender_ids import NAME_RE

        if not NAME_RE.match(sender_id):
            raise ValidationError({"sender_id": _("Use 3 to 11 letters or digits, with no spaces or symbols.")})
    account = sms_account(org)
    if account is None and not api_key.strip():
        raise ValidationError({"api_key": _("Paste your Africa's Talking API key.")})
    created = account is None
    account = account or SmsAccount(organization=org)
    changes = {}
    for name, value in (("username", username), ("sender_id", sender_id), ("enabled", enabled)):
        if created or getattr(account, name) != value:
            changes[name] = [None if created else getattr(account, name), value]
            setattr(account, name, value)
    if api_key.strip():
        account.set_secret("api_key", api_key.strip())
        changes["api_key"] = [None, "changed"]
    if changes:
        account.checked_at, account.check_error = None, ""
    account.updated_by = actor.user
    account.save()
    if changes:
        _audit("sms_account.saved", actor, account, request, changes)
    return account


def test_sms(actor: Membership, *, request=None) -> SmsAccount:
    """Sends one short SMS to the member's own phone through the organization's account."""
    require(actor, "organization.manage")
    account = sms_account(actor.organization)
    if account is None:
        raise ValidationError(_("Save your SMS account first."))
    result = sms_sender(account).send(actor.user.phone, _("Test from NyumbaPMS: your own SMS account works."))
    account.check_error = "" if result.ok else (result.error or "Not accepted")[:300]
    account.checked_at = timezone.now()
    account.save(update_fields=["check_error", "checked_at", "updated_at"])
    _audit("sms_account.tested", actor, account, request, {"ok": [None, result.ok]})
    return account


def remove_sms(actor: Membership, *, request=None) -> None:
    require(actor, "organization.manage")
    account = sms_account(actor.organization)
    if account is not None:
        _audit("sms_account.removed", actor, account, request, {"username": [account.username, None]})
        account.delete()
