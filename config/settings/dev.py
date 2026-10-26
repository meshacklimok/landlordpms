"""Local development settings."""
from .base import *  # noqa: F403

DEBUG = True

# The Next.js dev server (D-168) passes pages and form posts on to Django, so the browser's origin (:3000) is not
# the host Django sees (:8000). In production both are one address, so prod.py needs nothing like this.
CSRF_TRUSTED_ORIGINS = ["http://127.0.0.1:3000", "http://localhost:3000"]
