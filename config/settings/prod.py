"""Production settings. Requires SECRET_KEY, ALLOWED_HOSTS, SITE_URL, DB_*, REDIS_URL and EMAIL_* in the environment."""
import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

DEBUG = False


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ImproperlyConfigured(f"{name} must be set in production.")
    return value


_required("ALLOWED_HOSTS")
SITE_URL = _required("SITE_URL").rstrip("/")

SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
SECURE_HSTS_INCLUDE_SUBDOMAINS = True

# We run behind one reverse proxy (it sets X-Forwarded-Proto above), so client IPs come from
# X-Forwarded-For. Without this every request would share the proxy's address and one rate limit.
TRUSTED_PROXY_COUNT = int(os.getenv("TRUSTED_PROXY_COUNT", "1"))

# Rate limits and OTP cooldowns must be shared by every worker process.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": _required("REDIS_URL"),
    }
}

MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "OPTIONS": {
            "host": _required("EMAIL_HOST"),
            "port": int(os.getenv("EMAIL_PORT", "587")),
            "username": os.getenv("EMAIL_HOST_USER", ""),
            "password": os.getenv("EMAIL_HOST_PASSWORD", ""),
            "use_tls": os.getenv("EMAIL_USE_TLS", "1") == "1",
            "timeout": 10,
        },
    },
}
DEFAULT_FROM_EMAIL = SERVER_EMAIL = _required("DEFAULT_FROM_EMAIL")

# The console adapter would print login codes to the log instead of sending them. It is allowed only
# when explicitly asked for (a staging server without an SMS account).
if SMS_BACKEND.startswith("core.sms.") and SMS_BACKEND != "core.sms.AfricasTalkingSmsSender" \
        and os.getenv("ALLOW_CONSOLE_SMS") != "1":  # noqa: F405
    raise ImproperlyConfigured("Set SMS_BACKEND to a real SMS adapter (or ALLOW_CONSOLE_SMS=1 on staging).")
if SMS_BACKEND == "core.sms.AfricasTalkingSmsSender":  # noqa: F405
    _required("AT_USERNAME")
    _required("AT_API_KEY")
    if len(_required("AT_CALLBACK_TOKEN")) < 32:
        raise ImproperlyConfigured("AT_CALLBACK_TOKEN must be at least 32 random characters.")
