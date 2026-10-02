import importlib

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


@pytest.fixture(autouse=True)
def _seeded_plans(request):
    """A transactional test flushes the database, removing the plans migration 0002 seeds; put them back."""
    marker = request.node.get_closest_marker("django_db")
    if marker is None:
        return
    request.getfixturevalue("transactional_db" if marker.kwargs.get("transaction") else "db")
    from django.apps import apps

    from subscriptions.models import Plan

    if not Plan.objects.exists():
        importlib.import_module("subscriptions.migrations.0002_seed_plans").seed(apps, None)


@pytest.fixture
def outbox():
    return MemorySmsSender.outbox
