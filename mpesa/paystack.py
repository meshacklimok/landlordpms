"""Paystack API client for our own account, which takes landlords' subscription payments (D-126).
Landlords' and tenants' rent does not go through Paystack (D-140).

`urllib`, no SDK, like the Daraja client. `credentials` is anything with `secret()` and `pk`
(`subscriptions.collect.PlatformPaystack`). Amounts go to Paystack in cents. `PAYSTACK_CLIENT` picks the
class, so tests use `FakePaystackClient`.
[VERIFY the checkout reply and the webhook fields against a Paystack Kenya test account.]
"""

import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)

BASE_URL = "https://api.paystack.co"
# The checkout lists these in this order, so Card comes first and the mobile money options after it.
CHECKOUT_CHANNELS = ["card", "mobile_money"]


class PaystackError(Exception):
    """Paystack refused the request or could not be reached. The message is safe to show staff."""


def signature_ok(secret_key: str, body: bytes, signature: str) -> bool:
    """Paystack signs each webhook: HMAC-SHA512 of the raw body with the secret key, in hex."""
    if not secret_key or not signature:
        return False
    expected = hmac.new(secret_key.encode(), body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


class PaystackClient:
    TIMEOUT = 20

    def __init__(self, credentials):
        self.credentials = credentials

    def _open(self, request: urllib.request.Request) -> dict:
        try:
            with urllib.request.urlopen(request, timeout=self.TIMEOUT) as response:  # noqa: S310 - fixed https
                body = json.loads(response.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            raise PaystackError(self._http_error(e)) from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise PaystackError(f"Could not reach Paystack: {getattr(e, 'reason', e)}") from None
        except ValueError:
            raise PaystackError("Paystack sent a reply that is not JSON.") from None
        if not body.get("status"):
            raise PaystackError(str(body.get("message") or "Paystack did not accept the request.")[:300])
        return body.get("data") or {}

    @staticmethod
    def _http_error(e: urllib.error.HTTPError) -> str:
        try:
            body = json.loads(e.read().decode())
        except (ValueError, AttributeError, OSError):
            body = {}
        return (str(body.get("message") or "") or f"HTTP {e.code}")[:300]

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        request = urllib.request.Request(  # noqa: S310 - a fixed https URL
            f"{BASE_URL}{path}", method=method, data=json.dumps(body).encode() if body is not None else None,
            # Paystack's firewall refuses urllib's default User-Agent (Cloudflare 1010).
            headers={"Authorization": f"Bearer {self.credentials.secret()}", "Content-Type": "application/json",
                     "User-Agent": "NyumbaPMS/1.0"})
        return self._open(request)

    # -- API -----------------------------------------------------------------------------------

    def initialize(self, *, email: str, amount_cents: int, reference: str, callback_url: str,
                   metadata: dict) -> dict:
        """A Paystack checkout page (card or M-Pesa) for one payment. Returns `authorization_url` to send
        the payer to; they come back to `callback_url` with `?reference=` (D-126)."""
        return self._request("POST", "/transaction/initialize", {
            "email": email, "amount": amount_cents, "currency": "KES", "reference": reference,
            "callback_url": callback_url, "metadata": metadata, "channels": CHECKOUT_CHANNELS})

    def charge_mobile_money(self, *, email: str, amount_cents: int, reference: str, phone: str,
                            metadata: dict) -> dict:
        """An M-Pesa prompt sent by Paystack to `phone` (+254...), for when Daraja is down. The answer comes by
        the signed webhook (charge.success / charge.failed) or a verify call, under the same `reference`."""
        return self._request("POST", "/charge", {
            "email": email, "amount": amount_cents, "currency": "KES", "reference": reference,
            "mobile_money": {"phone": phone, "provider": "mpesa"}, "metadata": metadata})

    def verify(self, reference: str) -> dict:
        """The transaction behind a reference: `status` is success, failed, abandoned, ongoing or pending."""
        return self._request("GET", f"/transaction/verify/{urllib.parse.quote(reference, safe='')}")

    def check(self) -> dict:
        """Any call the key may make: proves the key works."""
        return self._request("GET", "/transaction?perPage=1")


class FakePaystackClient(PaystackClient):
    """Records calls instead of reaching Paystack. `fail` makes the next call raise; `replies` maps a
    path prefix to the data returned."""

    calls: list[tuple] = []
    fail: str = ""
    replies: dict[str, dict] = {}

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        FakePaystackClient.calls.append((self.credentials.pk, method, path, body))
        if FakePaystackClient.fail:
            raise PaystackError(FakePaystackClient.fail)
        for prefix, reply in FakePaystackClient.replies.items():
            if path.startswith(prefix):
                return reply
        if path == "/transaction/initialize":
            return {"reference": body["reference"], "access_code": "fake",
                    "authorization_url": f"https://checkout.paystack.com/fake-{body['reference']}"}
        if path == "/charge":
            return {"reference": body["reference"], "status": "pay_offline"}
        return {}


def get_client(credentials) -> PaystackClient:
    return import_string(settings.PAYSTACK_CLIENT)(credentials)
