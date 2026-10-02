"""M-Pesa settings: payment accounts, Daraja credentials and callback URL registration (D-045 items 2, 3, 9).

Secrets go in encrypted and never come back out to a page. A blank secret on save keeps the
stored one. The audit log names the fields that changed, never their values.
"""

import re

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import require
from audit import services as audit
from payments.models import PaymentAccount

from .daraja import DarajaError, get_client
from .models import FORBIDDEN_URL_WORDS, DarajaCredentials, new_callback_token

MPESA_TYPES = (PaymentAccount.Type.PAYBILL, PaymentAccount.Type.TILL)
_SHORTCODE = re.compile(r"^\d{5,7}$")


def _same_org(actor: Membership, obj) -> None:
    if obj.organization_id != actor.organization_id:
        raise PermissionDenied(_("That record belongs to another organization."))


def mpesa_accounts(org):
    return PaymentAccount.objects.for_org(org).filter(type__in=MPESA_TYPES).select_related("daraja")


def _shortcode(value: str, field: str = "shortcode") -> str:
    value = (value or "").strip()
    if not _SHORTCODE.match(value):
        raise ValidationError({field: _("Enter the 5 to 7 digit number.")})
    return value


@transaction.atomic
def add_account(actor: Membership, *, type: str, number: str, display_name: str, request=None) -> PaymentAccount:
    """Adds a Paybill or Till the organization collects rent on."""
    require(actor, "payment_accounts.manage")
    if type not in MPESA_TYPES:
        raise ValidationError({"type": _("Choose Paybill or Till.")})
    number = _shortcode(number, "number")
    display_name = (display_name or "").strip()[:80] or f"{PaymentAccount.Type(type).label} {number}"
    if PaymentAccount.all_objects.filter(organization=actor.organization, type=type, number=number).exists():
        raise ValidationError({"number": _("This account is already added.")})
    account = PaymentAccount.objects.create(organization=actor.organization, type=type, number=number,
                                            display_name=display_name)
    audit.record("payment_account.create", actor=actor.user, organization=actor.organization, obj=account,
                 request=request, changes={"type": [None, type], "number": [None, number]})
    return account


def _account(actor: Membership, account: PaymentAccount) -> PaymentAccount:
    _same_org(actor, account)
    if account.type not in MPESA_TYPES:
        raise ValidationError(_("Only Paybill and Till accounts connect to M-Pesa."))
    if account.archived_at is not None:
        raise ValidationError(_("That payment account is archived."))
    return account


@transaction.atomic
def save_credentials(actor: Membership, account: PaymentAccount, *, environment: str, shortcode: str = "",
                     consumer_key: str = "", consumer_secret: str = "", passkey: str = "",
                     request=None) -> DarajaCredentials:
    """Creates or updates an account's Daraja settings. Blank secrets keep the stored ones."""
    require(actor, "mpesa.settings")
    _account(actor, account)
    if environment not in DarajaCredentials.Environment.values:
        raise ValidationError({"environment": _("Choose sandbox or live.")})
    shortcode = _shortcode(shortcode or account.number)
    creds = DarajaCredentials.objects.select_for_update().filter(payment_account=account).first() \
        or DarajaCredentials(organization=account.organization, payment_account=account)
    new = {"consumer_key": consumer_key, "consumer_secret": consumer_secret, "passkey": passkey}
    missing = [n for n in ("consumer_key", "consumer_secret") if not creds.has(n) and not (new[n] or "").strip()]
    if missing:
        raise ValidationError({n: _("Enter this from your Daraja app.") for n in missing})

    changed = []
    if creds.environment != environment or not creds.pk:
        changed.append("environment")
    if creds.shortcode != shortcode:
        changed.append("shortcode")
    creds.environment, creds.shortcode = environment, shortcode
    for name, value in new.items():
        value = (value or "").strip()
        if value and value != (creds.secret(name) if creds.has(name) else ""):
            creds.set_secret(name, value)
            changed.append(name)
    if not changed:
        return creds
    # Where Safaricom sends payments depends on the shortcode, environment and keys.
    if {"environment", "shortcode", "consumer_key", "consumer_secret"} & set(changed):
        creds.urls_registered_at = None
    creds.updated_by = actor.user
    creds.save()
    audit.record("mpesa.credentials", actor=actor.user, organization=account.organization, obj=account,
                 request=request, changes={"changed": [None, sorted(changed)],
                                           "environment": [None, environment], "shortcode": [None, shortcode]})
    return creds


@transaction.atomic
def new_token(actor: Membership, creds: DarajaCredentials, *, request=None) -> DarajaCredentials:
    """Replaces the secret in the callback URLs, e.g. after a leak. The URLs must be registered again."""
    require(actor, "mpesa.settings")
    _same_org(actor, creds)
    for _attempt in range(3):
        creds.callback_token = new_callback_token()
        try:
            with transaction.atomic():
                creds.urls_registered_at = None
                creds.updated_by = actor.user
                creds.save(update_fields=["callback_token", "urls_registered_at", "updated_by", "updated_at"])
            break
        except IntegrityError:  # pragma: no cover - 256-bit tokens do not collide
            continue
    audit.record("mpesa.new_token", actor=actor.user, organization=creds.organization,
                 obj=creds.payment_account, request=request)
    return creds


def callback_urls(creds: DarajaCredentials) -> dict[str, str]:
    base = settings.SITE_URL
    return {kind: base + reverse("hook_daraja", args=[creds.callback_token, kind])
            for kind in ("confirm", "validate", "stk")}


def _check_urls(creds: DarajaCredentials, urls: dict[str, str]) -> None:
    for url in urls.values():
        if creds.environment == DarajaCredentials.Environment.PRODUCTION and not url.startswith("https://"):
            raise ValidationError(_("Live M-Pesa needs the site on https. Set SITE_URL to the https address."))
        lowered = url.lower()
        # Tokens never contain one, so a forbidden word comes from SITE_URL.
        word = next((w for w in FORBIDDEN_URL_WORDS if w in lowered), None)
        if word:
            raise ValidationError(_("Safaricom refuses callback addresses containing “%(word)s”. "
                                    "Change SITE_URL.") % {"word": word})


def register_urls(actor: Membership, creds: DarajaCredentials, *, request=None) -> DarajaCredentials:
    """Tells Safaricom to send this account's payments to our callback URLs."""
    require(actor, "mpesa.settings")
    _same_org(actor, creds)
    _account(actor, creds.payment_account)
    if not creds.can_connect:
        raise ValidationError(_("Save the consumer key and secret first."))
    urls = callback_urls(creds)
    _check_urls(creds, urls)
    try:
        get_client(creds).register_urls(urls["confirm"], urls["validate"])
    except DarajaError as e:
        creds.registration_error = str(e)[:300]
        creds.save(update_fields=["registration_error", "updated_at"])
        raise ValidationError(_("Safaricom did not accept the addresses: %(error)s") % {"error": e}) from None
    creds.urls_registered_at = timezone.now()
    creds.registration_error = ""
    creds.save(update_fields=["urls_registered_at", "registration_error", "updated_at"])
    audit.record("mpesa.register_urls", actor=actor.user, organization=creds.organization,
                 obj=creds.payment_account, request=request, changes={"shortcode": [None, creds.shortcode]})
    return creds
