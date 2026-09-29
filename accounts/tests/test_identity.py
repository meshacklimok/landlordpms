import re
from datetime import timedelta

import pytest
from django.contrib.auth import authenticate
from django.core import mail
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from accounts import identity, otp
from accounts.models import OTPCode, User

from .factories import PASSWORD, make_user

pytestmark = pytest.mark.django_db

VERIFY = OTPCode.Purpose.VERIFY_PHONE
RESET = OTPCode.Purpose.RESET_PASSWORD


def last_code(outbox) -> str:
    return re.search(r"\b(\d{6})\b", outbox[-1][1]).group(1)


def age_codes(phone, seconds=61):
    """Move codes into the past so the resend cooldown has passed."""
    for c in OTPCode.objects.filter(phone=phone):
        OTPCode.objects.filter(pk=c.pk).update(created_at=c.created_at - timedelta(seconds=seconds))


# --- OTP --------------------------------------------------------------------------------------


def test_otp_is_stored_hashed_and_verifies_once(outbox):
    otp.issue_otp("+254712345678", VERIFY)
    code = last_code(outbox)
    row = OTPCode.objects.get()
    assert code not in row.code_hash
    assert otp.verify_otp("+254712345678", VERIFY, code)
    assert not otp.verify_otp("+254712345678", VERIFY, code)


def test_otp_is_bound_to_purpose(outbox):
    otp.issue_otp("+254712345678", VERIFY)
    assert not otp.verify_otp("+254712345678", RESET, last_code(outbox))


def test_otp_expires(outbox):
    otp.issue_otp("+254712345678", VERIFY)
    OTPCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert not otp.verify_otp("+254712345678", VERIFY, last_code(outbox))


def test_otp_locks_after_max_attempts(outbox):
    otp.issue_otp("+254712345678", VERIFY)
    code = last_code(outbox)
    for _ in range(otp.MAX_ATTEMPTS):
        assert not otp.verify_otp("+254712345678", VERIFY, "000000" if code != "000000" else "111111")
    assert not otp.verify_otp("+254712345678", VERIFY, code)


def test_otp_resend_cooldown():
    otp.issue_otp("+254712345678", VERIFY)
    with pytest.raises(otp.OTPError):
        otp.issue_otp("+254712345678", VERIFY)


def test_otp_hourly_limit():
    for _ in range(otp.MAX_PER_HOUR):
        otp.issue_otp("+254712345678", VERIFY)
        age_codes("+254712345678")
    with pytest.raises(otp.OTPError):
        otp.issue_otp("+254712345678", VERIFY)


def test_new_otp_invalidates_older_one(outbox):
    otp.issue_otp("+254712345678", VERIFY)
    first = last_code(outbox)
    age_codes("+254712345678")
    otp.issue_otp("+254712345678", VERIFY)
    second = last_code(outbox)
    if first != second:
        assert not otp.verify_otp("+254712345678", VERIFY, first)
    assert otp.verify_otp("+254712345678", VERIFY, second)


def test_purge_expired_removes_old_codes():
    otp.issue_otp("+254712345678", VERIFY)
    age_codes("+254712345678", seconds=2 * 86400)
    assert otp.purge_expired() == 1


# --- registration and verification ------------------------------------------------------------


def test_register_normalizes_phone_and_leaves_unverified():
    user = identity.register_user(phone="0712 345 678", full_name="Wanjiku", password=PASSWORD)
    assert user.phone == "+254712345678"
    assert not user.phone_verified


def test_register_rejects_weak_password():
    with pytest.raises(ValidationError):
        identity.register_user(phone="0712345678", full_name="W", password="123")


def test_register_rejects_taken_verified_phone():
    make_user(phone="0712345678")
    with pytest.raises(ValidationError):
        identity.register_user(phone="+254712345678", full_name="W", password=PASSWORD)


def test_unverified_phone_can_be_taken_over():
    squatter = make_user(phone="0712345678", verified=False)
    user = identity.register_user(phone="0712345678", full_name="Real Owner", password="An0ther-Pass!")
    assert user.pk == squatter.pk
    assert user.full_name == "Real Owner"
    assert user.check_password("An0ther-Pass!")


def test_register_rejects_email_used_by_someone_else():
    make_user(email="a@example.com")
    with pytest.raises(ValidationError):
        identity.register_user(phone="0712345678", full_name="W", password=PASSWORD, email="A@Example.com")


def test_verify_phone(outbox):
    user = make_user(verified=False)
    identity.send_phone_verification(user)
    assert not identity.verify_phone(user, "not-it")
    assert identity.verify_phone(user, last_code(outbox))
    assert User.objects.get(pk=user.pk).phone_verified


# --- password reset ---------------------------------------------------------------------------


def test_password_reset_for_unknown_phone_is_silent(outbox):
    identity.request_password_reset("0799999999")
    assert outbox == []


def test_password_reset_flow_warns_owner(outbox):
    user = make_user(phone="0712345678", email="w@example.com")
    identity.request_password_reset("0712 345 678")
    code = last_code(outbox)
    assert identity.reset_password(phone="0712345678", code=code, new_password="Brand-New-Pass-1") == user
    user.refresh_from_db()
    assert user.check_password("Brand-New-Pass-1")
    assert user.password_reset_at is not None
    assert "password was just changed" in outbox[-1][1]
    assert len(mail.outbox) == 1


def test_password_reset_wrong_code():
    make_user(phone="0712345678")
    identity.request_password_reset("0712345678")
    assert identity.reset_password(phone="0712345678", code="xxxxxx", new_password="Brand-New-Pass-1") is None


# --- login ------------------------------------------------------------------------------------


@pytest.mark.parametrize("identifier", ["0712345678", "+254712345678", "254 712 345 678", "712345678",
                                        "W@Example.com"])
def test_login_by_any_phone_format_or_email(identifier):
    user = make_user(phone="0712345678", email="w@example.com")
    assert authenticate(None, username=identifier, password=PASSWORD) == user


def test_login_rejects_wrong_password_and_inactive_user():
    user = make_user(phone="0712345678")
    assert authenticate(None, username="0712345678", password="wrong") is None
    user.is_active = False
    user.save(update_fields=["is_active"])
    assert authenticate(None, username="0712345678", password=PASSWORD) is None


def test_login_view_rate_limits_across_phone_formats(client):
    make_user(phone="0712345678")
    formats = ["0712345678", "+254712345678", "254712345678", "0712 345 678"]
    for i in range(10):
        client.post(reverse("accounts:login"), {"identifier": formats[i % 4], "password": "wrong"})
    response = client.post(reverse("accounts:login"), {"identifier": "0712345678", "password": PASSWORD})
    assert response.status_code == 200
    assert "Too many attempts" in response.content.decode()


def test_login_view_ignores_offsite_next(client):
    make_user(phone="0712345678")
    response = client.post(reverse("accounts:login") + "?next=https://evil.example/",
                           {"identifier": "0712345678", "password": PASSWORD})
    assert response.status_code == 302
    assert "evil" not in response["Location"]


def test_register_view_sends_code_then_verify_lands_on_onboarding(client, outbox):
    response = client.post(reverse("accounts:register"), {
        "full_name": "Wanjiku", "phone": "0712345678", "password": PASSWORD, "accept_terms": "on",
    })
    assert response.status_code == 302
    assert response["Location"] == reverse("accounts:verify_phone")
    client.post(reverse("accounts:verify_phone"), {"code": last_code(outbox)})
    assert client.get(reverse("accounts:home"))["Location"] == reverse("accounts:onboarding")


def test_unverified_user_is_sent_to_verification(client):
    make_user(phone="0712345678", verified=False)
    client.login(username="0712345678", password=PASSWORD)
    assert client.get(reverse("accounts:home"))["Location"] == reverse("accounts:verify_phone")
