"""Fixed-window rate limiting on the Django cache (doc 14 D14).

Used for login, OTP requests and, later, payment callbacks. In production the
cache must be shared between processes (Redis), otherwise limits are per process.
"""

from django.core.cache import cache


def hit(key: str, limit: int, window_seconds: int) -> bool:
    """Count one attempt. Returns False when the limit is already reached."""
    cache_key = f"rl:{key}"
    added = cache.add(cache_key, 1, timeout=window_seconds)
    if added:
        return True
    try:
        count = cache.incr(cache_key)
    except ValueError:
        cache.set(cache_key, 1, timeout=window_seconds)
        return True
    return count <= limit


def is_limited(key: str, limit: int) -> bool:
    return (cache.get(f"rl:{key}") or 0) >= limit


def reset(key: str) -> None:
    cache.delete(f"rl:{key}")
