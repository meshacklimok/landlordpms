"""SMS one-time codes (doc 14 A11). Codes are stored hashed, expire quickly,
allow few attempts, and requests are rate-limited per phone (doc 14 D14)."""

import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db.models import F
from django.utils import timezone
from django.utils.translation import gettext as _

from core import ratelimit
from notifications.email_html import send_branded
from notifications.providers.mail import SYSTEM, mailer_for
from notifications.providers.sms import send_sms
from notifications.suppression import is_suppressed

from .models import OTPCode

logger = logging.getLogger(__name__)

OTP_TTL = timedelta(minutes=10)
OTP_LENGTH = 6
MAX_ATTEMPTS = 5
RESEND_COOLDOWN = timedelta(seconds=60)
MAX_PER_HOUR = 5
MAX_PER_PHONE_PER_DAY = 10  # across every purpose, so changing the purpose does not reset the count


class OTPError(Exception):
    pass


def _hash(phone: str, purpose: str, code: str) -> str:
    msg = f"{phone}:{purpose}:{code}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()


def _throttle(phone: str, purpose: str, last_sent_at) -> None:
    if last_sent_at and timezone.now() - last_sent_at < RESEND_COOLDOWN:
        raise OTPError(_("Please wait a minute before asking for another code."))
    if not ratelimit.hit(f"otp:{purpose}:{phone}", MAX_PER_HOUR, 3600):
        raise OTPError(_("Too many codes requested. Try again in an hour."))
    if not ratelimit.hit(f"otp-day:{phone}", MAX_PER_PHONE_PER_DAY, 86400):
        raise OTPError(_("Too many codes requested for this number today. Try again tomorrow."))


def issue_otp(phone: str, purpose: str, email: str = "", link: str = "") -> None:
    """Sends a code to the phone by SMS, or to `email` when given (with `link` under it, if any). The code stays
    keyed by the phone."""
    now = timezone.now()
    last = OTPCode.objects.filter(phone=phone, purpose=purpose).order_by("-created_at").first()
    _throttle(phone, purpose, last.created_at if last else None)
    if not email and not ratelimit.hit("otp-sms-all", getattr(settings, "OTP_SMS_DAILY_LIMIT", 3000), 86400):
        # Many different numbers in one day looks like someone running up our SMS bill, not like sign-ins.
        logger.error("Daily limit of %s login-code SMS reached", getattr(settings, "OTP_SMS_DAILY_LIMIT", 3000))
        raise OTPError(_("Codes by SMS are paused for a while. Try again later, or ask for the code by email."))

    code = f"{secrets.randbelow(10 ** OTP_LENGTH):0{OTP_LENGTH}d}"
    # Only the newest code is valid.
    OTPCode.objects.filter(phone=phone, purpose=purpose, consumed_at__isnull=True).update(consumed_at=now)
    OTPCode.objects.create(phone=phone, purpose=purpose, code_hash=_hash(phone, purpose, code),
                           expires_at=now + OTP_TTL)
    text = _("Your NyumbaPMS code is %(code)s. It expires in 10 minutes. Never share it.") % {"code": code}
    if email:
        if is_suppressed(email):
            raise OTPError(_("We cannot email that address: mail to it bounced or was reported as spam. "
                             "Use a different address."))
        try:
            body = _("Your NyumbaPMS code:\n\n%(code)s\n\nIt expires in 10 minutes. Never share it.") % {"code": code}
            if link:
                body += "\n\n" + _("Or confirm on the page this button opens. It stays valid for 24 hours.") + "\n" + link
            send_branded(_("Your NyumbaPMS code"), body, [email], using=mailer_for(SYSTEM),
                         button_label=_("Confirm my email"))
        except Exception as exc:  # noqa: BLE001 - any mail failure reads the same to the user
            raise OTPError(_("We could not send the email. Check the address and try again.")) from exc
        return
    send_sms(phone, text)


def pretend_issue_otp(phone: str, purpose: str) -> None:
    """Applies the same limits as issue_otp without sending anything, for phones with no account,
    so the errors a caller sees don't reveal whether the account exists."""
    key = f"otp-none:{purpose}:{phone}"
    _throttle(phone, purpose, cache.get(key))
    cache.set(key, timezone.now(), timeout=int(RESEND_COOLDOWN.total_seconds()))


def verify_otp(phone: str, purpose: str, code: str) -> bool:
    now = timezone.now()
    otp = (
        OTPCode.objects.filter(phone=phone, purpose=purpose, consumed_at__isnull=True, expires_at__gt=now)
        .order_by("-created_at")
        .first()
    )
    if otp is None:
        return False
    # Spend an attempt before comparing, in one statement, so parallel guesses can't share one.
    if not OTPCode.objects.filter(pk=otp.pk, attempts__lt=MAX_ATTEMPTS).update(attempts=F("attempts") + 1):
        return False
    code = (code or "").strip()
    if not hmac.compare_digest(otp.code_hash, _hash(phone, purpose, code)):
        return False
    updated = OTPCode.objects.filter(pk=otp.pk, consumed_at__isnull=True).update(consumed_at=now)
    return updated == 1


def purge_expired() -> int:
    """Hard delete old codes (allowed: temporary records, doc 11 §23)."""
    cutoff = timezone.now() - timedelta(days=1)
    deleted, _counts = OTPCode.objects.filter(created_at__lt=cutoff).delete()
    return deleted
