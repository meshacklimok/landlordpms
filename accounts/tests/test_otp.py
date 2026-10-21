"""Limits on login-code SMS: per phone a day, and for the whole platform a day."""

from datetime import timedelta

import pytest
from django.utils import timezone

from accounts.models import OTPCode

pytestmark = pytest.mark.django_db


def test_a_phone_has_a_daily_limit_across_purposes_and_the_platform_a_daily_ceiling(settings):
    from django.core.cache import cache

    from accounts import otp

    cache.clear()
    phone = "+254799000111"
    purposes = ["login", "register", "reset"]
    sent = 0
    with pytest.raises(otp.OTPError):
        for i in range(30):
            otp.issue_otp(phone, purposes[i % 3])
            sent += 1
            OTPCode.objects.filter(phone=phone).update(created_at=timezone.now() - timedelta(minutes=5))
    assert sent == otp.MAX_PER_PHONE_PER_DAY
    cache.clear()
    settings.OTP_SMS_DAILY_LIMIT = 2
    otp.issue_otp("+254799000201", "login")
    otp.issue_otp("+254799000202", "login")
    with pytest.raises(otp.OTPError):
        otp.issue_otp("+254799000203", "login")
