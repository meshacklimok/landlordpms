import pytest
from django.core.cache import cache

from core.sms import MemorySmsSender


@pytest.fixture(autouse=True)
def _test_settings(settings):
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.SMS_BACKEND = "core.sms.MemorySmsSender"
    # Never reach Safaricom from a test.
    settings.MPESA_CLIENT = "mpesa.daraja.FakeDarajaClient"
    from mpesa.daraja import FakeDarajaClient

    FakeDarajaClient.calls.clear()
    FakeDarajaClient.fail = ""
    FakeDarajaClient.replies = {}
    MemorySmsSender.outbox.clear()
    cache.clear()
    yield
    MemorySmsSender.outbox.clear()
    FakeDarajaClient.calls.clear()
    FakeDarajaClient.fail = ""


@pytest.fixture
def outbox():
    return MemorySmsSender.outbox
