"""Health checks for `/healthz`, the status page and alerts (D-061 item 1). No details leave here."""

import uuid

from django.core.cache import cache
from django.db import connection


def database() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone() == (1,)
    except Exception:
        return False


def cache_ok() -> bool:
    try:
        key, value = "healthz:probe", uuid.uuid4().hex
        cache.set(key, value, 30)
        return cache.get(key) == value
    except Exception:
        return False


def checks() -> dict[str, bool]:
    return {"database": database(), "cache": cache_ok()}
