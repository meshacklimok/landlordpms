"""Two-step login with an authenticator app (D-059).

TOTP per RFC 6238 (6 digits, 30-second steps, SHA-1), checked with the standard library. The
secret is encrypted at rest; recovery codes are stored hashed and work once. SMS is never a second
factor: password reset already goes by SMS, and a SIM swap would pass both.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

import segno
from django.conf import settings
from django.contrib.auth import update_session_auth_hash
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.translation import gettext as _

from audit import services as audit
from core import crypto, ratelimit

from .models import RecoveryCode, TOTPDevice, User

ISSUER = "landlordpms"
DIGITS = 6
STEP = 30
# Steps either side of now accepted for phone clock drift.
DRIFT = 1
RECOVERY_CODES = 10
# Wrong codes allowed per user in the window before they must wait.
FAIL_LIMIT = 5
FAIL_WINDOW = 900

# Session keys.
PENDING = "mfa_pending"  # {"user": pk, "backend": ..., "at": epoch seconds, "next": url}
PENDING_TTL = 300
VERIFIED = "mfa_verified"


class MFAError(Exception):
    """Too many wrong codes: the user must wait."""


# ---------------------------------------------------------------------------
# TOTP
# ---------------------------------------------------------------------------


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    return base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)


def code_at(secret: str, step: int) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 10 ** DIGITS:0{DIGITS}d}"


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // STEP)


def _match_step(secret: str, code: str, now: float | None = None) -> int | None:
    """The step the code belongs to, within the drift window, or None."""
    here = current_step(now)
    for step in range(here - DRIFT, here + DRIFT + 1):
        if hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def _clean(code: str) -> str:
    return "".join((code or "").split()).replace("-", "").lower()


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


def is_enabled(user) -> bool:
    if user is None or user.pk is None:
        return False
    return TOTPDevice.objects.filter(user=user, confirmed_at__isnull=False).exists()


def remaining_recovery_codes(user) -> int:
    return RecoveryCode.objects.filter(user=user, used_at__isnull=True).count()


def provisioning_uri(user, secret: str) -> str:
    label = quote(f"{ISSUER}:{user.phone}")
    return f"otpauth://totp/{label}?" + urlencode({"secret": secret, "issuer": ISSUER, "digits": DIGITS,
                                                  "period": STEP})


def qr_svg(uri: str) -> str:
    """An inline SVG QR code, drawn on the server so the secret never leaves it for a third party."""
    return segno.make(uri, error="m").svg_inline(scale=4, border=2)


def _end_other_sessions(user, request=None) -> None:
    User.objects.filter(pk=user.pk).update(session_epoch=F("session_epoch") + 1)
    user.refresh_from_db(fields=["session_epoch"])
    if request is not None and getattr(request, "user", None) is not None and request.user.pk == user.pk:
        update_session_auth_hash(request, user)


# ---------------------------------------------------------------------------
# Setting up and switching off
# ---------------------------------------------------------------------------


def start_setup(user) -> TOTPDevice:
    """A fresh secret waiting for its first code. Replaces an earlier unfinished setup."""
    if is_enabled(user):
        raise ValidationError(_("Two-step login is already on."))
    device, _created = TOTPDevice.objects.update_or_create(
        user=user, defaults={"secret": crypto.encrypt(new_secret()), "confirmed_at": None, "last_step": 0})
    return device


def pending_secret(user) -> str | None:
    device = TOTPDevice.objects.filter(user=user, confirmed_at__isnull=True).first()
    return crypto.decrypt(device.secret) if device else None


def _new_recovery_codes(user) -> list[str]:
    RecoveryCode.objects.filter(user=user).delete()
    codes = [f"{secrets.token_hex(3)}-{secrets.token_hex(3)}" for _ in range(RECOVERY_CODES)]
    RecoveryCode.objects.bulk_create([RecoveryCode(user=user, code_hash=_hash(user, c)) for c in codes])
    return codes


def _hash(user, code: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), f"recovery:{user.pk}:{_clean(code)}".encode(),
                    hashlib.sha256).hexdigest()


@transaction.atomic
def confirm_setup(user, code: str, request=None) -> list[str]:
    """Switches MFA on once the app's code matches. Returns the recovery codes to show once."""
    device = TOTPDevice.objects.select_for_update().filter(user=user, confirmed_at__isnull=True).first()
    if device is None:
        raise ValidationError(_("Start again: scan the new code."))
    step = _match_step(crypto.decrypt(device.secret), _clean(code))
    if step is None:
        raise ValidationError(_("That code is wrong. Check the time on your phone and try again."))
    device.confirmed_at = timezone.now()
    device.last_step = step
    device.save(update_fields=["confirmed_at", "last_step"])
    codes = _new_recovery_codes(user)
    audit.record("mfa.enable", actor=user, obj=user, request=request)
    _end_other_sessions(user, request)
    if request is not None:
        request.session[VERIFIED] = True
    return codes


def check(user, code: str, request=None) -> str | None:
    """"totp" or "recovery" when the code is right, else None. Raises MFAError after too many wrong codes."""
    key = f"mfa:user:{user.pk}"
    if ratelimit.is_limited(key, FAIL_LIMIT):
        raise MFAError(_("Too many wrong codes. Wait 15 minutes and try again."))
    code = _clean(code)
    with transaction.atomic():
        device = TOTPDevice.objects.select_for_update().filter(user=user, confirmed_at__isnull=False).first()
        if device is None:
            return None
        if code.isdigit() and len(code) == DIGITS:
            step = _match_step(crypto.decrypt(device.secret), code)
            if step is not None and step > device.last_step:
                device.last_step = step
                device.save(update_fields=["last_step"])
                ratelimit.reset(key)
                return "totp"
        elif code:
            used = RecoveryCode.objects.filter(user=user, code_hash=_hash(user, code), used_at__isnull=True).update(
                used_at=timezone.now())
            if used:
                ratelimit.reset(key)
                audit.record("mfa.recovery_code_used", actor=user, obj=user, request=request,
                             changes={"left": [None, remaining_recovery_codes(user)]})
                return "recovery"
    ratelimit.hit(key, FAIL_LIMIT, FAIL_WINDOW)
    audit.record("mfa.failed", actor=user, obj=user, request=request)
    return None


def disable(user, *, password: str, code: str, request=None) -> None:
    """Needs the password and a code. May raise MFAError. The failed-code audit is kept, so no outer atomic."""
    if not user.check_password(password or ""):
        raise ValidationError({"password": _("The password is wrong.")})
    if check(user, code, request) is None:
        raise ValidationError({"code": _("That code is wrong.")})
    with transaction.atomic():
        _remove(user)
        audit.record("mfa.disable", actor=user, obj=user, request=request)
    _end_other_sessions(user, request)


def _remove(user) -> None:
    TOTPDevice.objects.filter(user=user).delete()
    RecoveryCode.objects.filter(user=user).delete()


def regenerate_recovery_codes(user, code: str, request=None) -> list[str]:
    if check(user, code, request) is None:
        raise ValidationError({"code": _("That code is wrong.")})
    with transaction.atomic():
        codes = _new_recovery_codes(user)
        audit.record("mfa.recovery_codes_renewed", actor=user, obj=user, request=request)
    return codes


@transaction.atomic
def admin_reset(admin, user, request=None) -> None:
    """A Platform Admin removes a user's MFA after checking who they are (lost phone and codes)."""
    if not (admin and admin.is_staff):
        raise ValidationError(_("Only a Platform Admin can reset two-step login."))
    _remove(user)
    audit.record("mfa.admin_reset", actor=admin, obj=user, request=request)
    User.objects.filter(pk=user.pk).update(session_epoch=F("session_epoch") + 1)


# ---------------------------------------------------------------------------
# Login in two steps
# ---------------------------------------------------------------------------


def begin_login(request, user, next_url: str = "") -> None:
    """The password was right; hold the user until the code is given. Not logged in yet."""
    request.session[PENDING] = {"user": user.pk, "backend": getattr(user, "backend", ""), "at": int(time.time()),
                                "next": next_url}


def pending_user(request):
    data = request.session.get(PENDING)
    if not data or time.time() - data.get("at", 0) > PENDING_TTL:
        request.session.pop(PENDING, None)
        return None, None
    user = User.objects.filter(pk=data["user"], is_active=True).first()
    return user, data


def session_verified(request) -> bool:
    return bool(request.session.get(VERIFIED))


def suggest(membership) -> bool:
    """Whether to suggest MFA to this member: their role holds a sensitive capability and MFA is off."""
    from .capabilities import CAPABILITY_MAP
    from .permissions import effective_capabilities

    if membership is None or is_enabled(membership.user):
        return False
    return any(CAPABILITY_MAP[c].sensitive for c in effective_capabilities(membership) if c in CAPABILITY_MAP)
