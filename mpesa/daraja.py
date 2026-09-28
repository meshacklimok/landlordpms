"""Safaricom Daraja API client (D-045 item 9). One client per payment account's credentials.

`urllib`, no SDK. The OAuth token is cached per account for a little less than its lifetime.
`MPESA_CLIENT` picks the class, so tests use `FakeDarajaClient`. [VERIFY endpoints and response
fields against the current Daraja docs and the sandbox.]
"""

import base64
import hashlib
import json
import logging
import urllib.error
import urllib.request

from django.conf import settings
from django.core.cache import cache
from django.utils.module_loading import import_string

from .models import DarajaCredentials

logger = logging.getLogger(__name__)

BASE_URLS = {
    DarajaCredentials.Environment.SANDBOX: "https://sandbox.safaricom.co.ke",
    DarajaCredentials.Environment.PRODUCTION: "https://api.safaricom.co.ke",
}


class DarajaError(Exception):
    """Daraja refused the request or could not be reached. The message is safe to show staff."""


class DarajaClient:
    TIMEOUT = 20

    def __init__(self, credentials: DarajaCredentials):
        self.credentials = credentials
        self.base_url = BASE_URLS[credentials.environment]

    # -- plumbing ----------------------------------------------------------------------------

    def _cache_key(self) -> str:
        # Changes when the keys change, so a stale token is never reused after new keys are saved.
        digest = hashlib.sha256(self.credentials.consumer_key_encrypted.encode()).hexdigest()[:16]
        return f"daraja:token:{self.credentials.pk}:{digest}"

    def _open(self, request: urllib.request.Request) -> dict:
        try:
            with urllib.request.urlopen(request, timeout=self.TIMEOUT) as response:  # noqa: S310 - fixed https
                return json.loads(response.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            raise DarajaError(self._http_error(e)) from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise DarajaError(f"Could not reach Daraja: {getattr(e, 'reason', e)}") from None
        except ValueError:
            raise DarajaError("Daraja sent a reply that is not JSON.") from None

    @staticmethod
    def _http_error(e: urllib.error.HTTPError) -> str:
        try:
            body = json.loads(e.read().decode())
        except (ValueError, AttributeError, OSError):
            body = {}
        detail = body.get("errorMessage") or body.get("ResponseDescription") or ""
        code = body.get("errorCode") or ""
        return (f"{code} {detail}".strip() or f"HTTP {e.code}")[:300]

    def token(self) -> str:
        key = self._cache_key()
        token = cache.get(key)
        if token:
            return token
        creds = self.credentials
        basic = base64.b64encode(f"{creds.secret('consumer_key')}:{creds.secret('consumer_secret')}".encode())
        request = urllib.request.Request(f"{self.base_url}/oauth/v1/generate?grant_type=client_credentials",
                                         headers={"Authorization": f"Basic {basic.decode()}"})
        data = self._open(request)
        token = data.get("access_token")
        if not token:
            raise DarajaError("Daraja did not return an access token. Check the consumer key and secret.")
        try:
            lifetime = int(data.get("expires_in", 3599))
        except (TypeError, ValueError):
            lifetime = 3599
        cache.set(key, token, max(lifetime - 120, 60))
        return token

    def _post(self, path: str, body: dict) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {self.token()}", "Content-Type": "application/json"})
        return self._open(request)

    # -- API -----------------------------------------------------------------------------------

    def register_urls(self, confirmation_url: str, validation_url: str) -> dict:
        """C2B: tells Safaricom where to send payments made to this shortcode."""
        data = self._post("/mpesa/c2b/v2/registerurl", {
            "ShortCode": self.credentials.shortcode, "ResponseType": "Completed",
            "ConfirmationURL": confirmation_url, "ValidationURL": validation_url})
        if str(data.get("ResponseCode", "")) not in ("0", "00000000") and "success" not in \
                str(data.get("ResponseDescription", "")).lower():
            raise DarajaError(str(data.get("ResponseDescription") or data.get("errorMessage")
                                  or "Daraja did not accept the URLs.")[:300])
        return data


class FakeDarajaClient(DarajaClient):
    """Records calls instead of reaching Safaricom. `fail` makes the next call raise."""

    calls: list[tuple] = []
    fail: str = ""

    def token(self) -> str:
        return "fake-token"

    def _post(self, path: str, body: dict) -> dict:
        FakeDarajaClient.calls.append((self.credentials.shortcode, path, body))
        if FakeDarajaClient.fail:
            raise DarajaError(FakeDarajaClient.fail)
        return {"ResponseCode": "0", "ResponseDescription": "Success"}


def get_client(credentials: DarajaCredentials) -> DarajaClient:
    return import_string(settings.MPESA_CLIENT)(credentials)
