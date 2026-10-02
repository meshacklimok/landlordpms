"""Sign-up, phone verification and password reset (doc 14 A11)."""

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from audit import services as audit
from core.phone import normalize_phone
from core.sms import send_sms

from .models import OTPCode, User
from .otp import issue_otp, pretend_issue_otp, verify_otp


@transaction.atomic
def register_user(*, phone: str, full_name: str, password: str, email: str = "", request=None) -> User:
    phone = normalize_phone(phone)
    email = (email or "").strip().lower() or None
    existing = User.objects.filter(phone=phone).first()
    if existing is not None and existing.phone_verified:
        raise ValidationError({"phone": _("An account with this phone number already exists. Log in instead.")})
    if email and User.objects.filter(email__iexact=email).exclude(phone=phone).exists():
        raise ValidationError({"email": _("An account with this email already exists.")})

    # An unverified account proves nobody owns the phone yet, so it can be taken over by
    # whoever verifies it first.
    user = existing or User(phone=phone)
    user.full_name = full_name.strip()
    user.email = email
    validate_password(password, user)
    user.set_password(password)
    user.save()
    audit.record("user.register", actor=user, obj=user, request=request)
    return user


def send_phone_verification(user: User) -> None:
    issue_otp(user.phone, OTPCode.Purpose.VERIFY_PHONE)


def verify_phone(user: User, code: str, request=None) -> bool:
    if user.phone_verified:
        return True
    if not verify_otp(user.phone, OTPCode.Purpose.VERIFY_PHONE, code):
        return False
    user.phone_verified_at = timezone.now()
    user.save(update_fields=["phone_verified_at"])
    audit.record("user.verify_phone", actor=user, obj=user, request=request)
    return True


def request_password_reset(phone: str) -> None:
    """Behaves the same for unknown phones, limits included, so the form doesn't reveal who has an account."""
    phone = normalize_phone(phone)
    if User.objects.filter(phone=phone, is_active=True).exists():
        issue_otp(phone, OTPCode.Purpose.RESET_PASSWORD)
    else:
        pretend_issue_otp(phone, OTPCode.Purpose.RESET_PASSWORD)


@transaction.atomic
def reset_password(*, phone: str, code: str, new_password: str, request=None) -> User | None:
    phone = normalize_phone(phone)
    # Checked before looking the user up, so a weak password is refused the same way for every phone.
    validate_password(new_password)
    user = User.objects.filter(phone=phone, is_active=True).first()
    if user is None:
        return None
    validate_password(new_password, user)
    if not verify_otp(phone, OTPCode.Purpose.RESET_PASSWORD, code):
        return None
    user.set_password(new_password)
    user.password_reset_at = timezone.now()
    # Receiving the code proves control of the phone.
    if not user.phone_verified:
        user.phone_verified_at = user.password_reset_at
    user.save(update_fields=["password", "password_reset_at", "phone_verified_at"])
    audit.record("user.password_reset", actor=user, obj=user, request=request)
    # SIM-swap warning: the owner of the account sees this even if someone else reset it.
    send_sms(phone, _("Your landlordpms password was just changed. If this wasn't you, contact support now."))
    if user.email:
        user.email_user(
            _("Your landlordpms password was changed"),
            _("Your password was changed. If this wasn't you, contact support immediately."),
        )
    return user
