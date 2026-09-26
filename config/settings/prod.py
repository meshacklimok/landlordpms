"""Production settings. Requires SECRET_KEY, ALLOWED_HOSTS and DB_* in the environment."""
import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

DEBUG = False

if not os.getenv("ALLOWED_HOSTS"):
    raise ImproperlyConfigured("ALLOWED_HOSTS must be set in production.")

SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
