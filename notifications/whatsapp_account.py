"""An organization's own WhatsApp number (Messages > Settings).

Tenants then get WhatsApp messages from the landlord's own business number, and replies come back to the
organization's own webhook in Meta. Without one, the platform number (if any) is used.
"""

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import require
from audit import services as audit
from notifications.providers.whatsapp import CloudApiWhatsAppSender

from .models import PlatformWhatsApp, WhatsAppAccount, new_verify_token, new_webhook_token


def account_for(org) -> WhatsAppAccount | None:
    return WhatsAppAccount.objects.filter(organization=org).first()


def usable_account(org) -> WhatsAppAccount | None:
    account = account_for(org)
    return account if account is not None and account.usable else None


def platform_number() -> PlatformWhatsApp | None:
    """The platform owner's number from Django admin, when it is switched on and complete."""
    try:
        row = PlatformWhatsApp.objects.filter(pk=1).first()
    except Exception:  # noqa: BLE001 - sending must still work if the table is missing
        return None
    return row if row is not None and row.usable else None


def platform_available() -> bool:
    """The platform has a number for organizations that have none of their own."""
    from django.conf import settings

    return platform_number() is not None or bool(getattr(settings, "WHATSAPP_BACKEND", ""))


def webhook_path(account: WhatsAppAccount) -> str:
    return f"/hooks/whatsapp/{account.webhook_token}/"


def _clean_id(value: str) -> str:
    value = (value or "").strip()
    if not value.isdigit() or not 5 <= len(value) <= 30:
        raise ValidationError({"phone_number_id": _("The phone number ID is a number from your Meta WhatsApp setup.")})
    return value


def save(actor: Membership, *, phone_number_id: str, access_token: str = "", app_secret: str = "",
         enabled: bool = True, request=None) -> WhatsAppAccount:
    """Creates or updates the organization's number. A blank token or secret keeps the one saved."""
    require(actor, "organization.manage")
    org = actor.organization
    phone_number_id = _clean_id(phone_number_id)
    account = account_for(org)
    if account is None and not access_token.strip():
        raise ValidationError({"access_token": _("Paste the access token from Meta.")})
    if WhatsAppAccount.objects.filter(phone_number_id=phone_number_id).exclude(organization=org).exists():
        raise ValidationError({"phone_number_id": _("That number is already connected to another account.")})
    created = account is None
    account = account or WhatsAppAccount(organization=org)
    changes = {}
    if account.phone_number_id != phone_number_id:
        changes["phone_number_id"] = [account.phone_number_id or None, phone_number_id]
        account.phone_number_id = phone_number_id
        account.checked_at, account.check_error, account.display_number, account.verified_name = None, "", "", ""
    for name, value in (("access_token", access_token.strip()), ("app_secret", app_secret.strip())):
        if value:
            account.set_secret(name, value)
            changes[name] = [None, "changed"]  # the value itself is never logged
            if name == "access_token":
                account.checked_at, account.check_error = None, ""
    if created or account.enabled != enabled:
        changes["enabled"] = [None if created else account.enabled, enabled]
    account.enabled = enabled
    account.updated_by = actor.user
    account.save()
    if changes:
        audit.record("whatsapp.saved", actor=actor.user, organization=org, obj=account, request=request,
                     changes=changes)
    return account


def check(actor: Membership, *, request=None) -> WhatsAppAccount:
    """Asks Meta about the number with the saved token, and keeps what it says."""
    require(actor, "organization.manage")
    account = account_for(actor.organization)
    if account is None or not account.has("access_token"):
        raise ValidationError(_("Save your WhatsApp details first."))
    try:
        details = CloudApiWhatsAppSender(account.phone_number_id, account.secret("access_token")).describe()
    except ValueError as e:
        account.check_error = (str(e) or "Meta did not accept the details")[:300]
    else:
        account.check_error = ""
        account.display_number = str(details.get("display_phone_number") or "")[:40]
        account.verified_name = str(details.get("verified_name") or "")[:120]
    account.checked_at = timezone.now()
    account.save(update_fields=["check_error", "display_number", "verified_name", "checked_at", "updated_at"])
    audit.record("whatsapp.checked", actor=actor.user, organization=actor.organization, obj=account, request=request,
                 changes={"ok": [None, not account.check_error]})
    return account


def new_webhook(actor: Membership, *, request=None) -> WhatsAppAccount:
    """Fresh webhook URL and verify token, if the old ones leaked. Meta then needs the new ones."""
    require(actor, "organization.manage")
    account = account_for(actor.organization)
    if account is None:
        raise ValidationError(_("Save your WhatsApp details first."))
    account.webhook_token, account.verify_token = new_webhook_token(), new_verify_token()
    account.save(update_fields=["webhook_token", "verify_token", "updated_at"])
    audit.record("whatsapp.webhook_renewed", actor=actor.user, organization=actor.organization, obj=account,
                 request=request)
    return account


def remove(actor: Membership, *, request=None) -> None:
    """Back to the platform number. The log keeps what was sent."""
    require(actor, "organization.manage")
    account = account_for(actor.organization)
    if account is None:
        return
    audit.record("whatsapp.removed", actor=actor.user, organization=actor.organization, obj=account,
                 request=request, changes={"phone_number_id": [account.phone_number_id, None]})
    account.delete()
