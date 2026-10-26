"""What Resend and Brevo tell us about mail we sent: an address that bounced for good or marked us as spam goes
on the suppression list (notifications/suppression.py). Soft bounces and delays are the provider's to retry."""

import base64
import hashlib
import hmac
import time

from .models import EmailSuppression
from .suppression import suppress

Reason = EmailSuppression.Reason

# Brevo transactional events that mean the address is dead or hostile.
BREVO_HARD = {"hard_bounce": Reason.HARD_BOUNCE, "invalid_email": Reason.HARD_BOUNCE,
              "spam": Reason.COMPLAINT, "complaint": Reason.COMPLAINT}
TOLERANCE = 5 * 60  # seconds a signed Resend request may be old


def resend_signature_ok(secret: str, headers, body: bytes) -> bool:
    """Resend signs with Svix: HMAC-SHA256 of "id.timestamp.body" with the key after "whsec_"."""
    msg_id, stamp = headers.get("svix-id", ""), headers.get("svix-timestamp", "")
    if not (secret and msg_id and stamp.isdigit()) or abs(time.time() - int(stamp)) > TOLERANCE:
        return False
    try:
        key = base64.b64decode(secret.removeprefix("whsec_"))
    except ValueError:
        return False
    expected = base64.b64encode(hmac.new(key, f"{msg_id}.{stamp}.".encode() + body, hashlib.sha256).digest()).decode()
    return any(hmac.compare_digest(part.split(",", 1)[-1], expected)
               for part in headers.get("svix-signature", "").split())


def resend_event(payload: dict) -> int:
    """Handles one Resend event; returns how many addresses were added."""
    data = payload.get("data") or {}
    kind = payload.get("type", "")
    if kind == "email.complained":
        reason, detail = Reason.COMPLAINT, "Marked as spam"
    elif kind == "email.bounced" and (data.get("bounce") or {}).get("type", "").lower() == "permanent":
        reason, detail = Reason.HARD_BOUNCE, (data["bounce"].get("message") or "Permanent bounce")
    else:
        return 0
    return sum(suppress(a, reason, provider="Resend", detail=detail) for a in data.get("to") or [])


def brevo_event(payload: dict) -> int:
    reason = BREVO_HARD.get(str(payload.get("event", "")).lower())
    if reason is None:
        return 0
    return int(suppress(payload.get("email", ""), reason, provider="Brevo",
                        detail=str(payload.get("reason") or payload.get("event"))))
