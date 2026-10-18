"""Sign-up, phone verification and password reset (doc 14 A11)."""

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from audit import services as audit
from core.phone import normalize_phone
from notifications.providers.sms import send_sms
from support import legal

from .models import OTPCode, User
from .otp import issue_otp, pretend_issue_otp, verify_otp


def _claim_email(email: str, *, exclude_phone: str) -> None:
    """Refuses an email another account holds. An unverified email on an account whose phone was never verified is
    an abandoned sign-up, so it is released; otherwise someone could squat on an address they do not own."""
    for other in User.objects.filter(email__iexact=email).exclude(phone=exclude_phone):
        if other.phone_verified or other.email_verified_at:
            raise ValidationError({"email": _("An account with this email already exists.")})
        other.email = None
        other.save(update_fields=["email"])


@transaction.atomic
def register_user(*, phone: str, full_name: str, password: str, email: str = "", request=None) -> User:
    phone = normalize_phone(phone)
    email = (email or "").strip().lower() or None
    existing = User.objects.filter(phone=phone).first()
    if existing is not None and existing.phone_verified:
        raise ValidationError({"phone": _("An account with this phone number already exists. Log in instead.")})
    if email:
        _claim_email(email, exclude_phone=phone)

    # An unverified account proves nobody owns the phone yet, so it can be taken over by
    # whoever verifies it first.
    user = existing or User(phone=phone)
    user.full_name = full_name.strip()
    user.email = email
    validate_password(password, user)
    user.set_password(password)
    user.terms_version, user.terms_accepted_at = legal.VERSION, timezone.now()
    user.save()
    audit.record("user.register", actor=user, obj=user, request=request,
                 changes={"terms_version": [None, legal.VERSION]})
    return user


def accept_terms(user: User, *, request=None) -> None:
    """Records that the user accepted the current Terms and Privacy Policy (D-095 item 5)."""
    if user.terms_version == legal.VERSION:
        return
    before = user.terms_version or None
    user.terms_version, user.terms_accepted_at = legal.VERSION, timezone.now()
    user.save(update_fields=["terms_version", "terms_accepted_at"])
    audit.record("user.accept_terms", actor=user, obj=user, request=request,
                 changes={"terms_version": [before, legal.VERSION]})


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


def send_email_verification(user: User) -> None:
    issue_otp(user.phone, OTPCode.Purpose.VERIFY_EMAIL, email=user.email)


def verify_email(user: User, code: str, request=None) -> bool:
    if not user.email:
        return False
    if user.email_verified_at:
        return True
    if not verify_otp(user.phone, OTPCode.Purpose.VERIFY_EMAIL, code):
        return False
    user.email_verified_at = timezone.now()
    user.save(update_fields=["email_verified_at"])
    audit.record("user.verify_email", actor=user, obj=user, request=request)
    return True


@transaction.atomic
def set_email(user: User, email: str, request=None) -> None:
    """Adds or changes the email. It counts as unverified until the code sent to it is entered."""
    email = (email or "").strip().lower()
    if email == (user.email or "") and user.email_verified_at:
        return
    _claim_email(email, exclude_phone=user.phone)
    before = user.email
    user.email, user.email_verified_at = email, None
    user.save(update_fields=["email", "email_verified_at"])
    audit.record("user.set_email", actor=user, obj=user, request=request, changes={"email": [before, email]})


def _reset_user(identifier: str) -> tuple[User | None, str]:
    """The active account an email or phone number points to, and a normalized key for the throttle."""
    identifier = (identifier or "").strip()
    if "@" in identifier:
        key = identifier.lower()
        return User.objects.filter(email__iexact=key, is_active=True).first(), key
    key = normalize_phone(identifier)
    return User.objects.filter(phone=key, is_active=True).first(), key


def request_password_reset(identifier: str) -> None:
    """Sends the reset code to the email on the account. Only an account with no email (a caretaker) gets it by SMS.
    Behaves the same for unknown accounts, limits included, so the form doesn't reveal who has one."""
    user, key = _reset_user(identifier)
    if user is None:
        pretend_issue_otp(key, OTPCode.Purpose.RESET_PASSWORD)
    elif user.email:
        issue_otp(user.phone, OTPCode.Purpose.RESET_PASSWORD, email=user.email)
    else:
        issue_otp(user.phone, OTPCode.Purpose.RESET_PASSWORD)


@transaction.atomic
def reset_password(*, identifier: str, code: str, new_password: str, request=None) -> User | None:
    # Checked before looking the user up, so a weak password is refused the same way for every account.
    validate_password(new_password)
    user, _key = _reset_user(identifier)
    if user is None:
        return None
    validate_password(new_password, user)
    if not verify_otp(user.phone, OTPCode.Purpose.RESET_PASSWORD, code):
        return None
    user.set_password(new_password)
    user.password_reset_at = timezone.now()
    # Receiving the code proves control of the place it went to.
    if user.email and not user.email_verified_at:
        user.email_verified_at = user.password_reset_at
    elif not user.email and not user.phone_verified:
        user.phone_verified_at = user.password_reset_at
    user.save(update_fields=["password", "password_reset_at", "phone_verified_at", "email_verified_at"])
    audit.record("user.password_reset", actor=user, obj=user, request=request)
    # SIM-swap warning: the owner of the account sees this even if someone else reset it.
    send_sms(user.phone, _("Your NyumbaPMS password was just changed. If this wasn't you, contact support now."))
    if user.email:
        user.email_user(
            _("Your NyumbaPMS password was changed"),
            _("Your password was changed. If this wasn't you, contact support immediately."),
        )
    return user
