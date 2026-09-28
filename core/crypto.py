"""Encryption at rest for provider secrets such as Daraja keys (D-045 item 2).

Fernet (AES-128-CBC with HMAC-SHA256) from `cryptography`. `FIELD_ENCRYPTION_KEYS` is a
comma-separated list of Fernet keys: the first encrypts, all of them decrypt, so a key can be
rotated by putting the new one first and re-saving with `rotate`. Development falls back to a key
derived from SECRET_KEY; production must set the keys (config/settings/prod.py).
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class DecryptionError(Exception):
    """Stored text could not be decrypted with any configured key."""


def _keys() -> list[bytes]:
    raw = getattr(settings, "FIELD_ENCRYPTION_KEYS", "") or ""
    keys = [k.strip().encode() for k in (raw.split(",") if isinstance(raw, str) else raw) if k.strip()]
    if not keys:
        keys = [base64.urlsafe_b64encode(hashlib.sha256(b"field-encryption:" + settings.SECRET_KEY.encode()).digest())]
    return keys


def _fernet() -> MultiFernet:
    try:
        return MultiFernet([Fernet(k) for k in _keys()])
    except ValueError as e:
        raise ImproperlyConfigured("FIELD_ENCRYPTION_KEYS must be Fernet keys (Fernet.generate_key()).") from e


def encrypt(text: str) -> str:
    """Empty stays empty, so "not set" needs no special case."""
    if not text:
        return ""
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise DecryptionError("Stored secret cannot be decrypted with the configured keys.") from e


def rotate(token: str) -> str:
    """Re-encrypts with the first key."""
    return _fernet().rotate(token.encode()).decode() if token else ""


def check_keys() -> None:
    """Raises ImproperlyConfigured if a configured key is not a valid Fernet key."""
    _fernet()
