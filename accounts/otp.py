"""SMS one-time codes (doc 14 A11). Codes are stored hashed, expire quickly,
allow few attempts, and requests are rate-limited per phone (doc 14 D14)."""

import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext as _

from core import ratelimit
from core.sms import send_sms

from .models import OTPCode

OTP_TTL = timedelta(minutes=10)
OTP_LENGTH = 6
MAX_ATTEMPTS = 5
RESEND_COOLDOWN = timedelta(seconds=60)
MAX_PER_HOUR = 5


class OTPError(Exception):
    pass


def _hash(phone: str, purpose: str, code: str) -> str:
    msg = f"{phone}:{purpose}:{code}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()


def issue_otp(phone: str, purpose: str) -> None:
    now = timezone.now()
    last = OTPCode.objects.filter(phone=phone, purpose=purpose).order_by("-created_at").first()
    if last and now - last.created_at < RESEND_COOLDOWN:
        raise OTPError(_("Please wait a minute before asking for another code."))
    if not ratelimit.hit(f"otp:{purpose}:{phone}", MAX_PER_HOUR, 3600):
        raise OTPError(_("Too many codes requested. Try again in an hour."))

    code = f"{secrets.randbelow(10 ** OTP_LENGTH):0{OTP_LENGTH}d}"
    # Only the newest code is valid.
    OTPCode.objects.filter(phone=phone, purpose=purpose, consumed_at__isnull=True).update(consumed_at=now)
    OTPCode.objects.create(phone=phone, purpose=purpose, code_hash=_hash(phone, purpose, code),
                           expires_at=now + OTP_TTL)
    send_sms(phone, _("Your landlordpms code is %(code)s. It expires in 10 minutes. Never share it.")
             % {"code": code})


def verify_otp(phone: str, purpose: str, code: str) -> bool:
    now = timezone.now()
    otp = (
        OTPCode.objects.filter(phone=phone, purpose=purpose, consumed_at__isnull=True, expires_at__gt=now)
        .order_by("-created_at")
        .first()
    )
    if otp is None or otp.attempts >= MAX_ATTEMPTS:
        return False
    code = (code or "").strip()
    if not hmac.compare_digest(otp.code_hash, _hash(phone, purpose, code)):
        OTPCode.objects.filter(pk=otp.pk).update(attempts=otp.attempts + 1)
        return False
    updated = OTPCode.objects.filter(pk=otp.pk, consumed_at__isnull=True).update(consumed_at=now)
    return updated == 1


def purge_expired() -> int:
    """Hard delete old codes (allowed: temporary records, doc 11 §23)."""
    cutoff = timezone.now() - timedelta(days=1)
    deleted, _counts = OTPCode.objects.filter(created_at__lt=cutoff).delete()
    return deleted
