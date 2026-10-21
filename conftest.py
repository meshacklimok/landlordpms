import importlib
import os
import tempfile

import pytest
from django.conf import settings as django_settings
from django.core.cache import cache

from notifications.providers.sms import MemorySmsSender


def pytest_configure(config):
    """One run per test database at a time.

    Two runs sharing a database clear each other's tables and deadlock on teardown, so a second run stops
    with a message instead. TEST_DB_SUFFIX=2 gives a run its own database (test_landlordpms_2).
    """
    database = django_settings.DATABASES["default"]
    name = database.setdefault("TEST", {}).get("NAME") or f"test_{database['NAME']}"
    if suffix := os.environ.get("TEST_DB_SUFFIX"):
        name = f"{name}_{suffix}"
        database["TEST"]["NAME"] = name
    if hasattr(config, "workerinput"):  # an xdist worker: the controller holds the lock
        return
    handle = open(os.path.join(tempfile.gettempdir(), f"{name}.pytest.lock"), "a+")  # noqa: SIM115 - held for the run
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise pytest.UsageError(f"Another test run is using the database {name}. Wait for it to finish, "
                                "or run this one with TEST_DB_SUFFIX=2 to use its own database.") from None
    config._test_db_lock = handle  # released when the run ends


@pytest.fixture(autouse=True)
def _test_settings(settings):
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.SMS_BACKEND = "notifications.providers.sms.MemorySmsSender"
    # Never reach Safaricom from a test.
    settings.MPESA_CLIENT = "mpesa.daraja.FakeDarajaClient"
    settings.PAYSTACK_CLIENT = "subscriptions.paystack.FakePaystackClient"
    # Never reach a push service from a test; tests that push set the VAPID keys.
    settings.PUSH_BACKEND = "notifications.push.MemoryPushSender"
    settings.VAPID_PUBLIC_KEY = settings.VAPID_PRIVATE_KEY = ""
    # Never reach the model provider from a test (D-091); tests script FakeClient.
    settings.ASSISTANT_BACKEND = "ai_assistant.client.FakeClient"
    settings.OPENAI_API_KEY = "test-key"
    from ai_assistant.client import FakeClient

    FakeClient.script = []
    FakeClient.calls = []
    from notifications.push import MemoryPushSender

    MemoryPushSender.outbox.clear()
    MemoryPushSender.status = 201
    from mpesa.daraja import FakeDarajaClient

    FakeDarajaClient.calls.clear()
    FakeDarajaClient.fail = ""
    FakeDarajaClient.replies = {}
    from subscriptions.paystack import FakePaystackClient

    FakePaystackClient.calls.clear()
    FakePaystackClient.fail = ""
    FakePaystackClient.replies = {}
    MemorySmsSender.outbox.clear()
    MemorySmsSender.senders.clear()
    cache.clear()
    yield
    MemorySmsSender.outbox.clear()
    MemorySmsSender.senders.clear()
    FakeDarajaClient.calls.clear()
    FakeDarajaClient.fail = ""


@pytest.fixture(autouse=True)
def _seeded_plans(request):
    """A transactional test flushes the database, removing the plans migrations 0002 and 0004 seed; put them back."""
    marker = request.node.get_closest_marker("django_db")
    if marker is None:
        return
    request.getfixturevalue("transactional_db" if marker.kwargs.get("transaction") else "db")
    from django.apps import apps

    from subscriptions.models import Plan

    if not Plan.objects.exists():
        importlib.import_module("subscriptions.migrations.0002_seed_plans").seed(apps, None)
        importlib.import_module("subscriptions.migrations.0004_unit_bands_and_one_off").seed(apps, None)
    from growth.models import Article

    if not Article.objects.exists():  # the nine starting articles come from migration 0007 (D-178)
        importlib.import_module("growth.migrations.0007_load_articles").load(apps, None)


@pytest.fixture
def outbox():
    return MemorySmsSender.outbox
