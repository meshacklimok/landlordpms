"""
Shared Django settings. Environment specifics live in dev.py and prod.py.

For more information on this file, see
https://docs.djangoproject.com/en/6.1/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/6.1/ref/settings/
"""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
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
    # LandlordPMS — Core
    "core",
    'accounts',
    'properties',
    'tenants',
    'leases',
    'imports',
    'billing',
    'payments',
    'mpesa',
    'notifications',
    'inspections',
    'letters',

    # LandlordPMS — Business Operations
    'maintenance',
    'expenses',
    'accounting',
    'utilities',
    'documents',
    'reports',
    'analytics',

    # LandlordPMS — Advanced
    'ai_assistant',
    'subscriptions',
    'audit',
    'support',

]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.middleware.ActiveOrganizationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
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
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Database
# https://docs.djangoproject.com/en/6.1/ref/settings/#databases

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME", "landlordpms"),
        "USER": os.getenv("DB_USER", "landlordpms"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", "localhost"),
        "PORT": os.getenv("DB_PORT", "5432"),
    }
}


# Authentication: phone-first login, email optional (doc 14 A11)
AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["accounts.backends.PhoneOrEmailBackend"]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "accounts:home"
LOGOUT_REDIRECT_URL = "accounts:login"

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

# Where the site is served, for links in messages sent outside a request (receipt links).
SITE_URL = os.getenv("SITE_URL", "http://127.0.0.1:8000").rstrip("/")

# SMS adapter (core/sms.py). The console adapter prints codes during development.
SMS_BACKEND = os.getenv("SMS_BACKEND", "core.sms.ConsoleSmsSender")
# KES per SMS part, for the cost estimate shown before an announcement is sent.
SMS_PRICE_ESTIMATE = os.getenv("SMS_PRICE_ESTIMATE", "0.80")
# Africa's Talking (SMS_BACKEND=core.sms.AfricasTalkingSmsSender). AT_USERNAME=sandbox uses the sandbox.
AT_USERNAME = os.getenv("AT_USERNAME", "")
AT_API_KEY = os.getenv("AT_API_KEY", "")
AT_SENDER_ID = os.getenv("AT_SENDER_ID", "")
# Secret part of the callback URLs: /hooks/sms/africastalking/<token>/<delivery|inbound|optout>/
AT_CALLBACK_TOKEN = os.getenv("AT_CALLBACK_TOKEN", "")

# WhatsApp adapter (core/whatsapp.py). Empty: WhatsApp is not offered and tenants get SMS.
WHATSAPP_BACKEND = os.getenv("WHATSAPP_BACKEND", "")
# Meta WhatsApp Cloud API (WHATSAPP_BACKEND=core.whatsapp.CloudApiWhatsAppSender). Webhook: /hooks/whatsapp/
WA_API_VERSION = os.getenv("WA_API_VERSION", "v21.0")
WA_PHONE_NUMBER_ID = os.getenv("WA_PHONE_NUMBER_ID", "")
WA_ACCESS_TOKEN = os.getenv("WA_ACCESS_TOKEN", "")
WA_APP_SECRET = os.getenv("WA_APP_SECRET", "")
WA_VERIFY_TOKEN = os.getenv("WA_VERIFY_TOKEN", "")

# Keys that encrypt provider secrets at rest (core/crypto.py): comma-separated Fernet keys, the first
# encrypts. Empty in development means a key derived from SECRET_KEY; production must set them.
FIELD_ENCRYPTION_KEYS = os.getenv("FIELD_ENCRYPTION_KEYS", "")

# Daraja (M-Pesa) client, one set of credentials per payment account (mpesa/daraja.py, D-045).
MPESA_CLIENT = os.getenv("MPESA_CLIENT", "mpesa.daraja.DarajaClient")
# Optional: only these IPs may call the M-Pesa callbacks (comma-separated). Empty allows any.
MPESA_ALLOWED_IPS = [ip.strip() for ip in os.getenv("MPESA_ALLOWED_IPS", "").split(",") if ip.strip()]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
