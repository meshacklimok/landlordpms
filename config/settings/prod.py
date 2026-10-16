"""Production settings. Requires SECRET_KEY, ALLOWED_HOSTS, SITE_URL, DB_*, REDIS_URL, EMAIL_* and
FIELD_ENCRYPTION_KEYS in the environment."""
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
if not SITE_URL.startswith("https://"):
    raise ImproperlyConfigured("SITE_URL must start with https:// in production.")
CSRF_TRUSTED_ORIGINS = [SITE_URL]

if len(SECRET_KEY) < 50 or SECRET_KEY == "change-this-later" or SECRET_KEY.startswith("django-insecure"):  # noqa: F405, S105 - refuses placeholders
    raise ImproperlyConfigured("SECRET_KEY must be at least 50 random characters in production.")

SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
# One year (D-062 item 7). Add SECURE_HSTS_PRELOAD once the domain is settled [VERIFY].
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
SILENCED_SYSTEM_CHECKS = ["security.W021"]  # preload waits for the domain, as above; nothing else is silenced
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True
# Sessions are read from Redis and written through to the database, so a Redis restart logs nobody out (D-105).
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"
SESSION_COOKIE_SAMESITE = "Lax"
# The load balancer and uptime monitor may call the health check over plain HTTP.
SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
# Each worker keeps a small pool of connections (doc 20 §1). A statement that runs over 30 seconds, or a
# transaction left open for a minute, is ended so it cannot hold locks; run `migrate` with
# DB_STATEMENT_TIMEOUT_MS=0 when a migration builds an index on a large table.
DB_POOL = os.getenv("DB_POOL", "1") == "1"
DATABASES["default"]["CONN_MAX_AGE"] = 0 if DB_POOL else int(os.getenv("DB_CONN_MAX_AGE", "60"))  # noqa: F405
DATABASES["default"]["OPTIONS"] = database_options(  # noqa: F405
    pool=DB_POOL,
    statement_timeout_ms=int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "30000")),
    idle_in_transaction_ms=int(os.getenv("DB_IDLE_IN_TRANSACTION_MS", "60000")))

# We run behind one reverse proxy (it sets X-Forwarded-Proto above), so client IPs come from
# X-Forwarded-For. Without this every request would share the proxy's address and one rate limit.
TRUSTED_PROXY_COUNT = int(os.getenv("TRUSTED_PROXY_COUNT", "1"))

# Rate limits and OTP cooldowns must be shared by every worker process.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": _required("REDIS_URL"),
        "KEY_PREFIX": "nyumbapms",
        # A stalled Redis must fail fast, not hold every request; reports fall back to computing (core/reportcache.py).
        "OPTIONS": {"socket_connect_timeout": 2, "socket_timeout": 2, "health_check_interval": 30},
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
# A second provider for mail to tenants and owners (core/mail.py). Optional: without it, "default" carries everything.
if os.getenv("EMAIL_MESSAGES_HOST"):
    MAILERS["messages"] = {
        "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "OPTIONS": {
            "host": os.environ["EMAIL_MESSAGES_HOST"],
            "port": int(os.getenv("EMAIL_MESSAGES_PORT", "587")),
            "username": os.getenv("EMAIL_MESSAGES_USER", ""),
            "password": os.getenv("EMAIL_MESSAGES_PASSWORD", ""),
            "use_tls": os.getenv("EMAIL_MESSAGES_USE_TLS", "1") == "1",
            "timeout": 10,
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
# Subscription payments to our own Paybill or Paystack account (D-126).
if PLATFORM_DARAJA_CONSUMER_KEY:  # noqa: F405
    _required("PLATFORM_DARAJA_CONSUMER_SECRET")
    _required("PLATFORM_DARAJA_SHORTCODE")
    if PLATFORM_DARAJA_ENVIRONMENT not in ("SANDBOX", "PRODUCTION"):  # noqa: F405
        raise ImproperlyConfigured("PLATFORM_DARAJA_ENVIRONMENT must be SANDBOX or PRODUCTION.")
if PLATFORM_DARAJA_CONSUMER_KEY or PLATFORM_PAYSTACK_SECRET_KEY:  # noqa: F405
    if len(_required("PLATFORM_CALLBACK_TOKEN")) < 32:
        raise ImproperlyConfigured("PLATFORM_CALLBACK_TOKEN must be at least 32 random characters.")
if PLATFORM_PAYSTACK_SECRET_KEY and not PLATFORM_PAYSTACK_SECRET_KEY.startswith(("sk_test_", "sk_live_")):  # noqa: F405
    raise ImproperlyConfigured("PLATFORM_PAYSTACK_SECRET_KEY must be a Paystack secret key (sk_live_...).")
if WHATSAPP_BACKEND == "core.whatsapp.CloudApiWhatsAppSender":  # noqa: F405
    _required("WA_PHONE_NUMBER_ID")
    _required("WA_ACCESS_TOKEN")
    _required("WA_APP_SECRET")
    if len(_required("WA_VERIFY_TOKEN")) < 32:
        raise ImproperlyConfigured("WA_VERIFY_TOKEN must be at least 32 random characters.")
elif WHATSAPP_BACKEND and os.getenv("ALLOW_CONSOLE_SMS") != "1":  # noqa: F405
    raise ImproperlyConfigured("Set WHATSAPP_BACKEND to core.whatsapp.CloudApiWhatsAppSender or leave it empty.")

if WEBHOOKS_ALLOW_PRIVATE:  # noqa: F405
    raise ImproperlyConfigured("WEBHOOKS_ALLOW_PRIVATE is for development only (D-083 item 5).")

# Alerts on staff phones (D-089): both keys or neither, a real contact, and the real sender.
if bool(VAPID_PUBLIC_KEY) != bool(VAPID_PRIVATE_KEY):  # noqa: F405
    raise ImproperlyConfigured("Set both VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY, or neither.")
if VAPID_PUBLIC_KEY and (VAPID_SUBJECT.endswith("@example.com")  # noqa: F405
                         or not VAPID_SUBJECT.startswith(("mailto:", "https://"))):  # noqa: F405
    raise ImproperlyConfigured("VAPID_SUBJECT must be a mailto: or https:// contact for the push services.")
if PUSH_BACKEND != "notifications.push.WebPushSender":  # noqa: F405
    raise ImproperlyConfigured("PUSH_BACKEND must be notifications.push.WebPushSender in production.")

# The assistant (D-091, D-115): only the provider the privacy page names, in production.
if ASSISTANT_BACKEND != "ai_assistant.client.OpenAIClient":  # noqa: F405
    raise ImproperlyConfigured("ASSISTANT_BACKEND must be ai_assistant.client.OpenAIClient in production.")

# Daraja keys and other provider secrets are stored encrypted with these (D-045 item 2).
try:
    from cryptography.fernet import Fernet

    for _key in _required("FIELD_ENCRYPTION_KEYS").split(","):
        Fernet(_key.strip())
except ValueError as e:
    raise ImproperlyConfigured("FIELD_ENCRYPTION_KEYS must be comma-separated Fernet keys.") from e

# Organizations pay for their SMS in production (D-060 item 10).
SMS_WALLET_ENFORCED = os.getenv("SMS_WALLET_ENFORCED", "1") == "1"
