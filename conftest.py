import pytest
from django.core.cache import cache

from core.sms import MemorySmsSender


@pytest.fixture(autouse=True)
def _test_settings(settings):
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.SMS_BACKEND = "core.sms.MemorySmsSender"
    MemorySmsSender.outbox.clear()
    cache.clear()
    yield
    MemorySmsSender.outbox.clear()


@pytest.fixture
def outbox():
    return MemorySmsSender.outbox
