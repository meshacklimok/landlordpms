"""
Shared Django settings. Environment specifics live in dev.py and prod.py.

For more information on this file, see
https://docs.djangoproject.com/en/6.1/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/6.1/ref/settings/
"""
import os
import urllib.parse
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.utils.csp import CSP
from dotenv import load_dotenv

load_dotenv()


# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/6.1/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise ImproperlyConfigured("SECRET_KEY is not set. Copy .env.example to .env and set it.")
# Never on by default; config.settings.dev turns it on.
DEBUG = False

ALLOWED_HOSTS = [h for h in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    # Foundations: shared code, running the platform, who is who
    "core",
    "ops",
    "accounts",

    # Property and people
    "properties",
    "tenants",
    "leases",
    "prospects",

    # Money in and out
    "billing",
    "payments",
    "mpesa",
    "banking",
    "expenses",

    # Day-to-day work on the buildings
    "meters",
    "maintenance",
    "inspections",
    "imports",

    # Paperwork
    "documents",
    "letters",
    "esign",

    # Talking to people and the portals they use
    "notifications",
    "portal",
    "owner_portal",
    "search",

    # Figures, the assistant and the developer console
    "reports",
    "ai_assistant",
    "console",

    # The business itself: plans, public site, support, audit and the API
    "subscriptions",
    "growth",
    "support",
    "audit",
    "api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "reports.pane.ReportPaneMiddleware",  # right inside CSP: lets the report centre frame a report drawn with ?pane=1, error pages too (D-148)
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.middleware.ActiveOrganizationMiddleware",
    "growth.campaigns.CaptureCampaignMiddleware",  # remembers ?ref= and ?utm_ for sign-up (D-133)
    "support.app_opens.AppOpenMiddleware",  # counts installed-app opens (D-102)
    "django.contrib.messages.middleware.MessageMiddleware",
    "core.sessions.SessionTimeoutMiddleware",  # after the two above: it reads memberships and leaves a message
    "accounts.middleware.AdminMFAMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.headers.ExtraSecurityHeadersMiddleware",  # Permissions-Policy, no-store for signed-in pages (D-139)
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / 'templates'],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "accounts.context_processors.organization",
                "notifications.context_processors.bell",
                "portal.context_processors.portal",
                "owner_portal.context_processors.owner_portal",
                "subscriptions.context_processors.banner",
                "reports.pane.report_pane",
                "growth.seo.context",  # title, description, canonical, Open Graph, JSON-LD or noindex (D-158)
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Database
# https://docs.djangoproject.com/en/6.1/ref/settings/#databases

def database_options(*, pool: bool, statement_timeout_ms: int, idle_in_transaction_ms: int) -> dict:
    """Connection options (doc 20 §1-2). Timeouts of 0 are off; a pool needs CONN_MAX_AGE 0."""
    options = {
        "sslmode": os.getenv("DB_SSLMODE", "prefer"),
        "connect_timeout": int(os.getenv("DB_CONNECT_TIMEOUT", "10")),
        # Shown in pg_stat_activity, so `db_health` can tell web, cron and restore connections apart.
        "application_name": os.getenv("DB_APP_NAME", "landlordpms"),
    }
    server = []
    if statement_timeout_ms:
        server.append(f"-c statement_timeout={statement_timeout_ms}")
    if idle_in_transaction_ms:
        server.append(f"-c idle_in_transaction_session_timeout={idle_in_transaction_ms}")
    if server:
        options["options"] = " ".join(server)
    if pool:
        options["pool"] = {"min_size": int(os.getenv("DB_POOL_MIN", "1")),
                           "max_size": int(os.getenv("DB_POOL_MAX", "4")),
                           "timeout": int(os.getenv("DB_POOL_TIMEOUT", "10"))}
    return options


DB_POOL = os.getenv("DB_POOL", "0") == "1"
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME", "landlordpms"),
        "USER": os.getenv("DB_USER", "landlordpms"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", "localhost"),
        "PORT": os.getenv("DB_PORT", "5432"),
        # Seconds a connection is reused; 0 opens one per request. Must be 0 with a pool.
        "CONN_MAX_AGE": 0 if DB_POOL else int(os.getenv("DB_CONN_MAX_AGE", "0")),
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": database_options(pool=DB_POOL,
                                    statement_timeout_ms=int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "0")),
                                    idle_in_transaction_ms=int(os.getenv("DB_IDLE_IN_TRANSACTION_MS", "0"))),
    }
}


# Authentication: phone-first login, email optional (doc 14 A11)
AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["accounts.backends.PhoneOrEmailBackend"]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "accounts:home"
LOGOUT_REDIRECT_URL = "accounts:login"

# Argon2 first (D-107). Passwords saved with PBKDF2 before then still work and are re-saved with Argon2 the next
# time their owner logs in.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

# How long a login lasts (D-107, core/sessions.py). Staff, meaning anyone in an organization or a Platform Admin, are
# logged out after an idle hour and must log in again after 12 hours. Tenants in the portal sign in on their own
# phones and keep a session for 14 idle days, 30 at most. The cookie lives as long as the longest of these.
SESSION_IDLE_MINUTES = int(os.getenv("SESSION_IDLE_MINUTES", "60"))
SESSION_MAX_HOURS = int(os.getenv("SESSION_MAX_HOURS", "12"))
PORTAL_SESSION_IDLE_DAYS = int(os.getenv("PORTAL_SESSION_IDLE_DAYS", "14"))
PORTAL_SESSION_MAX_DAYS = int(os.getenv("PORTAL_SESSION_MAX_DAYS", "30"))
SESSION_COOKIE_AGE = PORTAL_SESSION_MAX_DAYS * 24 * 60 * 60

# A request body (not counting uploaded files) over 5 MB is refused, and a form may not carry more than 1000 fields
# (D-139). Uploads have their own limits in documents/ and support/.
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 1000
DATA_UPLOAD_MAX_NUMBER_FILES = 20

# Password validation
# https://docs.djangoproject.com/en/6.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.1/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "Africa/Nairobi"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.1/howto/static-files/

STATIC_URL = "static/"
STATICFILES_DIRS = [
    BASE_DIR / 'static',
]

STATIC_ROOT = BASE_DIR / 'staticfiles'

# CSS and JS URLs carry ?v= of the file so a new deploy is never served an old copy from a cache (D-141).
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "core.storage.VersionedStaticFilesStorage"},
}

# Email
# https://docs.djangoproject.com/en/6.1/topics/email/#topic-email-configuration

MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.console.EmailBackend",
    },
}


MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'


# Cache: used for rate limits. Must be shared (Redis) in production.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

# How many of our own proxies sit in front of Django. Client IPs (rate limits, audit) are read
# from X-Forwarded-For only when this is set; 0 means use REMOTE_ADDR.
TRUSTED_PROXY_COUNT = int(os.getenv("TRUSTED_PROXY_COUNT", "0"))

# Content Security Policy (D-107). Scripts, styles and fonts come only from our own files (Bootstrap, its icons and
# Chart.js are served from static/vendor, so a blocked or unreachable CDN cannot leave pages unstyled), never
# inline scripts, so markup slipped into a page cannot run code; templates use data- attributes and static/js instead of
# onclick= or <script> blocks (core/tests/test_csp.py checks). Inline style attributes are still allowed: they cannot
# run code, and moving every one into a class is later work. Pages may not be framed, forms post only here, and
# browsers report what they block to /csp-report/. CSP_REPORT_ONLY=1 reports without blocking, to try a change.
CONTENT_SECURITY_POLICY = {
    "default-src": [CSP.SELF],
    "script-src": [CSP.SELF],
    "style-src": [CSP.SELF, CSP.UNSAFE_INLINE],
    "font-src": [CSP.SELF],
    # Bootstrap draws checkboxes and select arrows with data: SVGs.
    "img-src": [CSP.SELF, "data:"],
    "connect-src": [CSP.SELF],
    "worker-src": [CSP.SELF],
    "manifest-src": [CSP.SELF],
    "object-src": [CSP.NONE],
    "base-uri": [CSP.SELF],
    # Paying the subscription by Paystack posts here, then redirects to Paystack's checkout (D-126).
    "form-action": [CSP.SELF, "https://checkout.paystack.com"],
    "frame-ancestors": [CSP.NONE],
    "report-uri": "/csp-report/",
}
# The Paybill setup video (a link set in Django admin) plays in a privacy-friendly YouTube frame (D-174).
CONTENT_SECURITY_POLICY["frame-src"] = ["https://www.youtube-nocookie.com"]
if os.getenv("CSP_REPORT_ONLY") == "1":
    SECURE_CSP_REPORT_ONLY = CONTENT_SECURITY_POLICY
else:
    SECURE_CSP = CONTENT_SECURITY_POLICY

# Where the site is served, for links in messages sent outside a request (receipt links).
SITE_URL = os.getenv("SITE_URL", "http://127.0.0.1:8005").rstrip("/")

# SMS adapter (notifications/providers/sms.py). The console adapter prints codes during development.
SMS_BACKEND = os.getenv("SMS_BACKEND", "notifications.providers.sms.ConsoleSmsSender")
# These adapters used to live in core/. A server .env written then still works.
SMS_BACKEND = SMS_BACKEND.replace("core.sms.", "notifications.providers.sms.", 1)
# KES per SMS part, for the cost estimate shown before an announcement is sent.
SMS_PRICE_ESTIMATE = os.getenv("SMS_PRICE_ESTIMATE", "0.80")
# Africa's Talking (SMS_BACKEND=notifications.providers.sms.AfricasTalkingSmsSender). AT_USERNAME=sandbox uses the sandbox.
AT_USERNAME = os.getenv("AT_USERNAME", "")
AT_API_KEY = os.getenv("AT_API_KEY", "")
AT_SENDER_ID = os.getenv("AT_SENDER_ID", "")
# Messages a month the plan allows, per channel, so the console can show how near the limit is (D-138). 0: unknown.
MESSAGE_MONTHLY_LIMITS = {
    "EMAIL": int(os.getenv("EMAIL_MONTHLY_LIMIT", "0") or 0),
    "SMS": int(os.getenv("SMS_MONTHLY_LIMIT", "0") or 0),
    "WHATSAPP": int(os.getenv("WHATSAPP_MONTHLY_LIMIT", "0") or 0),
}
# Secret part of the callback URLs: /hooks/sms/africastalking/<token>/<delivery|inbound|optout>/
AT_CALLBACK_TOKEN = os.getenv("AT_CALLBACK_TOKEN", "")
# Tenant USSD menu (D-081): the code shown to tenants, e.g. *384*123#. Callback: /hooks/ussd/africastalking/<token>/
USSD_CODE = os.getenv("USSD_CODE", "")

# Webhooks (D-083 item 5): development may send to http:// and private addresses; production refuses it.
WEBHOOKS_ALLOW_PRIVATE = os.getenv("WEBHOOKS_ALLOW_PRIVATE") == "1"

# Alerts on staff phones (D-089, notifications/push.py). Empty keys: not offered. `manage.py vapid_keys` makes a pair.
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_SUBJECT = os.getenv("VAPID_SUBJECT", "mailto:support@example.com")
PUSH_BACKEND = os.getenv("PUSH_BACKEND", "notifications.push.WebPushSender")
# Push services beyond Google, Mozilla, Microsoft and Apple (comma-separated host names).
PUSH_ALLOWED_HOSTS = [h.strip().lower() for h in os.getenv("PUSH_ALLOWED_HOSTS", "").split(",") if h.strip()]

# The assistant (D-091, ai_assistant/). No key: not offered. Owners still switch it on per organization.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")  # only for AnthropicClient, which is not allowed in prod
ASSISTANT_MODEL = os.getenv("ASSISTANT_MODEL", "gpt-5.6-terra")
ASSISTANT_BACKEND = os.getenv("ASSISTANT_BACKEND", "ai_assistant.client.OpenAIClient")
# Reading photos of meter notebooks and utility bills (D-144, meters/ocr.py), on the same key. The limit is scans a
# month per organization; Organization.scan_limit overrides it for one landlord (0 turns scans off for them).
SCAN_MODEL = os.getenv("SCAN_MODEL", ASSISTANT_MODEL)
SCAN_MONTHLY_LIMIT = int(os.getenv("SCAN_MONTHLY_LIMIT", "100"))
# What the model costs us, for the usage pages (D-144): US dollars per million tokens, and dollars to shillings.
AI_PRICE_INPUT_PER_M = os.getenv("AI_PRICE_INPUT_PER_M", "2.00")
AI_PRICE_OUTPUT_PER_M = os.getenv("AI_PRICE_OUTPUT_PER_M", "12.00")
USD_TO_KES = os.getenv("USD_TO_KES", "129")

# WhatsApp adapter (notifications/providers/whatsapp.py). Empty: WhatsApp is not offered and tenants get SMS.
WHATSAPP_BACKEND = os.getenv("WHATSAPP_BACKEND", "")
WHATSAPP_BACKEND = WHATSAPP_BACKEND.replace("core.whatsapp.", "notifications.providers.whatsapp.", 1)
# Meta WhatsApp Cloud API (WHATSAPP_BACKEND=notifications.providers.whatsapp.CloudApiWhatsAppSender). Webhook: /hooks/whatsapp/
WA_API_VERSION = os.getenv("WA_API_VERSION", "v21.0")
WA_PHONE_NUMBER_ID = os.getenv("WA_PHONE_NUMBER_ID", "")
WA_ACCESS_TOKEN = os.getenv("WA_ACCESS_TOKEN", "")
WA_APP_SECRET = os.getenv("WA_APP_SECRET", "")
WA_VERIFY_TOKEN = os.getenv("WA_VERIFY_TOKEN", "")

# Platform billing: what landlords pay us (D-060). Prices are before VAT; VAT is added only when we are
# registered. [VERIFY] the rate and the SMS price with the accountant and the SMS provider.
PLATFORM_VAT_REGISTERED = os.getenv("PLATFORM_VAT_REGISTERED", "0") == "1"
PLATFORM_VAT_RATE = os.getenv("PLATFORM_VAT_RATE", "16")
PLATFORM_PAYBILL = os.getenv("PLATFORM_PAYBILL", "")
# Landlords paying their subscription online (D-126). Each way is offered when its keys are set and it is
# switched on in Django admin (Subscriptions > Online payment switch).
# Our own Paybill through Daraja: an M-Pesa prompt to the landlord's phone, and Paybill payments matched
# by the invoice's account number. SANDBOX or PRODUCTION; the shortcode defaults to PLATFORM_PAYBILL.
PLATFORM_DARAJA_ENVIRONMENT = os.getenv("PLATFORM_DARAJA_ENVIRONMENT", "SANDBOX")
PLATFORM_DARAJA_SHORTCODE = os.getenv("PLATFORM_DARAJA_SHORTCODE", "") or PLATFORM_PAYBILL
PLATFORM_DARAJA_CONSUMER_KEY = os.getenv("PLATFORM_DARAJA_CONSUMER_KEY", "")
PLATFORM_DARAJA_CONSUMER_SECRET = os.getenv("PLATFORM_DARAJA_CONSUMER_SECRET", "")
PLATFORM_DARAJA_PASSKEY = os.getenv("PLATFORM_DARAJA_PASSKEY", "")
# The secret part of our callback URLs (/hooks/plat/<token>/...): 32+ random characters.
PLATFORM_CALLBACK_TOKEN = os.getenv("PLATFORM_CALLBACK_TOKEN", "")
# Our own Paystack account: sk_test_... or sk_live_...; it also signs Paystack's webhooks.
PLATFORM_PAYSTACK_SECRET_KEY = os.getenv("PLATFORM_PAYSTACK_SECRET_KEY", "")
PLATFORM_KRA_PIN = os.getenv("PLATFORM_KRA_PIN", "")
# KES charged to the organization's SMS wallet per SMS part sent.
SMS_PRICE = os.getenv("SMS_PRICE", "1.00")
# Off in development and tests: organizations send SMS without a wallet. On in production.
SMS_WALLET_ENFORCED = os.getenv("SMS_WALLET_ENFORCED", "0") == "1"
# KRA eTIMS adapter for our invoices (subscriptions/etims.py); the default sends nothing.
ETIMS_ADAPTER = os.getenv("ETIMS_ADAPTER", "subscriptions.etims.NullEtimsAdapter")

# Keys that encrypt provider secrets at rest (core/crypto.py): comma-separated Fernet keys, the first
# encrypts. Empty in development means a key derived from SECRET_KEY; production must set them.
FIELD_ENCRYPTION_KEYS = os.getenv("FIELD_ENCRYPTION_KEYS", "")

# Daraja (M-Pesa) client, one set of credentials per payment account (mpesa/daraja.py, D-045).
MPESA_CLIENT = os.getenv("MPESA_CLIENT", "mpesa.daraja.DarajaClient")
# Optional: only these IPs may call the M-Pesa callbacks (comma-separated). Empty allows any.
MPESA_ALLOWED_IPS = [ip.strip() for ip in os.getenv("MPESA_ALLOWED_IPS", "").split(",") if ip.strip()]
# Paystack, for landlords paying their subscription to us only (subscriptions/paystack.py, D-126, D-140).
PAYSTACK_CLIENT = os.getenv("PAYSTACK_CLIENT", "subscriptions.paystack.PaystackClient")
# Optional: only these IPs may call the Paystack webhook. Paystack's signature is checked either way.
PAYSTACK_ALLOWED_IPS = [ip.strip() for ip in os.getenv("PAYSTACK_ALLOWED_IPS", "").split(",") if ip.strip()]
# Paystack needs an email for each checkout; an organization without one gets <its id>@<this domain>.
PAYSTACK_EMAIL_DOMAIN = os.getenv("PAYSTACK_EMAIL_DOMAIN", "") or (
    urllib.parse.urlsplit(SITE_URL).hostname or "example.com")

# Operations (D-062, doc 18).
# Who gets job and health alerts from `check_jobs`; empty falls back to ADMINS.
OPS_ALERT_EMAILS = os.getenv("OPS_ALERT_EMAILS", "")
# "Name <email>, Name <email>": errors are emailed here when Sentry is not used.
ADMINS = [(a.split("<")[0].strip(), a.split("<")[1].rstrip(">").strip()) if "<" in a else (a.strip(), a.strip())
          for a in os.getenv("ADMINS", "").split(",") if a.strip()]
# Where `backup` writes; outside the web root. Empty means <repo>/backups.
BACKUP_DIR = os.getenv("BACKUP_DIR", "")
# One organization's data as a ZIP (D-177): kept outside media and the backups, deleted when the link expires.
DATA_EXPORT_DIR = os.getenv("DATA_EXPORT_DIR", "") or str(BASE_DIR / "exports")
DATA_EXPORT_HOURS = int(os.getenv("DATA_EXPORT_HOURS", "48"))
PG_DUMP = os.getenv("PG_DUMP", "")
PG_RESTORE = os.getenv("PG_RESTORE", "")
# Database alerts from `check_jobs` (doc 20 §22-24). DB_ALERT_SIZE_MB of 0 means no size limit is watched.
DB_ALERT_CONNECTIONS_PERCENT = int(os.getenv("DB_ALERT_CONNECTIONS_PERCENT", "80"))
DB_ALERT_LONG_QUERY_SECONDS = int(os.getenv("DB_ALERT_LONG_QUERY_SECONDS", "300"))
DB_ALERT_DISK_FREE_PERCENT = int(os.getenv("DB_ALERT_DISK_FREE_PERCENT", "10"))
DB_ALERT_DISK_FREE_GB = int(os.getenv("DB_ALERT_DISK_FREE_GB", "2"))
DB_ALERT_SIZE_MB = int(os.getenv("DB_ALERT_SIZE_MB", "0"))
# Attack alerts from `check_jobs` (D-108): more than this many in an hour, across the platform.
SECURITY_ALERT_SIGNIN_HOUR = int(os.getenv("SECURITY_ALERT_SIGNIN_HOUR", "300"))
SECURITY_ALERT_TWO_STEP_HOUR = int(os.getenv("SECURITY_ALERT_TWO_STEP_HOUR", "100"))
SECURITY_ALERT_CSP_HOUR = int(os.getenv("SECURITY_ALERT_CSP_HOUR", "500"))
# Days a sent message keeps its text and address before `purge_messages` clears them (doc 20, Retention) [VERIFY].
MESSAGE_TEXT_DAYS = int(os.getenv("MESSAGE_TEXT_DAYS", "730"))

# Support and the public pages (D-061, D-063).
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "")
SUPPORT_WHATSAPP = os.getenv("SUPPORT_WHATSAPP", "")
# An https link (e.g. YouTube) to the Paybill setup video, shown on the M-Pesa setup pages. Empty hides it (D-174).
MPESA_SETUP_VIDEO_URL = os.getenv("MPESA_SETUP_VIDEO_URL", "")
DATA_PROTECTION_EMAIL = os.getenv("DATA_PROTECTION_EMAIL", "")
HOSTING_LOCATION = os.getenv("HOSTING_LOCATION", "")
# The Terms and Privacy Policy (D-095): the company's legal name, and true once a lawyer has checked the texts.
LEGAL_NAME = os.getenv("LEGAL_NAME", "")
# The company number, postal and physical address, and the ODPC registration number, shown on both pages.
LEGAL_REGISTRATION = os.getenv("LEGAL_REGISTRATION", "")
LEGAL_ADDRESS = os.getenv("LEGAL_ADDRESS", "")
ODPC_REGISTRATION = os.getenv("ODPC_REGISTRATION", "")
LEGAL_REVIEWED = os.getenv("LEGAL_REVIEWED", "").lower() in ("1", "true", "yes")

# Plain lines to the console; the process manager keeps them. Never log request bodies, codes or secrets.
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"}},
    "filters": {"require_debug_false": {"()": "django.utils.log.RequireDebugFalse"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
        "mail_admins": {"class": "django.utils.log.AdminEmailHandler", "level": "ERROR",
                        "filters": ["require_debug_false"]},
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "django.request": {"handlers": ["console", "mail_admins"], "level": "ERROR", "propagate": False},
        "django.security": {"handlers": ["console", "mail_admins"], "level": "WARNING", "propagate": False},
    },
}

# Error tracking (D-062 item 5): only when SENTRY_DSN is set and sentry-sdk is installed.
SENTRY_DSN = os.getenv("SENTRY_DSN", "")
if SENTRY_DSN:
    try:
        import sentry_sdk
    except ImportError as e:
        raise ImproperlyConfigured("SENTRY_DSN is set but sentry-sdk is not installed.") from e
    # No personal data in error reports (D-097 item 5): no request bodies and no local variables, which hold
    # phone numbers, names and amounts; send_default_pii=False already drops cookies, headers and the user.
    sentry_sdk.init(dsn=SENTRY_DSN, send_default_pii=False, traces_sample_rate=0.1,
                    max_request_body_size="never", include_local_variables=False,
                    environment=os.getenv("SENTRY_ENVIRONMENT", "production"),
                    release=os.getenv("SENTRY_RELEASE") or None)
    # Sentry has the errors; do not email them as well.
    LOGGING["loggers"]["django.request"]["handlers"] = ["console"]

# Product analytics (D-140): server-side events only (core/productevents.py). No key means nothing is sent.
POSTHOG_API_KEY = os.getenv("POSTHOG_API_KEY", "")
POSTHOG_HOST = os.getenv("POSTHOG_HOST", "https://eu.i.posthog.com")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
