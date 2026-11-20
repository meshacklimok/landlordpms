"""Local development settings."""
from .base import *  # noqa: F403

DEBUG = True

# The Next.js dev server (D-168) passes pages and form posts on to Django, so the browser's origin (:3000) is not
# the host Django sees (:8000). In production both are one address, so prod.py needs nothing like this.
CSRF_TRUSTED_ORIGINS = ["http://127.0.0.1:3000", "http://localhost:3000"]
# Local development email delivery through Resend SMTP
MAILERS = {
    "default": {
        "BACKEND": "notifications.providers.backend.SuppressingSMTPBackend",
        "OPTIONS": {
            "host": os.environ["EMAIL_HOST"],
            "port": int(os.getenv("EMAIL_PORT", "587")),
            "username": os.environ["EMAIL_HOST_USER"],
            "password": os.environ["EMAIL_HOST_PASSWORD"],
            "use_tls": os.getenv("EMAIL_USE_TLS", "1") == "1",
            "timeout": 15,
        },
    },
}
DEFAULT_FROM_EMAIL = "NyumbaPMS <noreply@nyumbapms.com>"
SERVER_EMAIL = DEFAULT_FROM_EMAIL
