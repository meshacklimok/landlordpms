"""Creating, revoking and checking API keys (D-082 items 2, 3 and 5)."""

import datetime
import hashlib
import hmac
import secrets

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import effective_capabilities, is_membership_usable, require
from audit import services as audit

from .models import WRITE_SCOPES, ApiKey

KEY_PREFIX = "lpk"
MAX_LIVE_KEYS = 10
EXPIRY_DAYS = (30, 90, 365)
TOUCH_EVERY = datetime.timedelta(minutes=1)
MANAGE = "organization.manage"


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def visible_keys(membership: Membership):
    return ApiKey.objects.filter(organization=membership.organization).select_related("membership__user", "revoked_by")


@transaction.atomic
def create_key(actor: Membership, *, name: str, scopes, expires_in_days: int | None = None,
               request=None) -> tuple[ApiKey, str]:
    """Returns the key and its secret. The secret is not stored: show it once."""
    require(actor, MANAGE)
    name = (name or "").strip()
    scopes = set(scopes or ())
    scopes = sorted(scopes | {str(WRITE_SCOPES[s]) for s in scopes if s in WRITE_SCOPES})
    if not name:
        raise ValidationError(_("Give the key a name."))
    if not scopes or not set(scopes) <= set(ApiKey.Scope.values):
        raise ValidationError(_("Choose what the key may read."))
    if expires_in_days is not None and expires_in_days not in EXPIRY_DAYS:
        raise ValidationError(_("Choose an expiry from the list."))
    live = ApiKey.objects.filter(ApiKey.live_q(), organization=actor.organization).select_for_update()
    if len(live) >= MAX_LIVE_KEYS:
        raise ValidationError(_("You already have %(n)s keys in use. Revoke one first.") % {"n": MAX_LIVE_KEYS})
    prefix = secrets.token_hex(4)
    while ApiKey.objects.filter(prefix=prefix).exists():
        prefix = secrets.token_hex(4)
    secret = f"{KEY_PREFIX}_{prefix}_{secrets.token_urlsafe(24)}"
    key = ApiKey.objects.create(
        organization=actor.organization, membership=actor, name=name[:60], prefix=prefix, key_hash=_hash(secret),
        scopes=scopes,
        expires_at=timezone.now() + datetime.timedelta(days=expires_in_days) if expires_in_days else None)
    audit.record("api_key.create", actor=actor.user, organization=actor.organization, obj=key, request=request,
                 changes={"name": [None, key.name], "scopes": [None, scopes],
                          "expires_at": [None, key.expires_at.isoformat() if key.expires_at else None]})
    return key, secret


@transaction.atomic
def revoke_key(actor: Membership, key: ApiKey, request=None) -> None:
    require(actor, MANAGE)
    if key.organization_id != actor.organization_id:
        raise ValidationError(_("Key not found."))
    key = ApiKey.objects.select_for_update().get(pk=key.pk)
    if key.revoked_at is not None:
        return
    key.revoked_at = timezone.now()
    key.revoked_by = actor.user
    key.save(update_fields=["revoked_at", "revoked_by", "updated_at"])
    audit.record("api_key.revoke", actor=actor.user, organization=actor.organization, obj=key, request=request,
                 changes={"revoked": [False, True]})


def authenticate(raw: str) -> ApiKey | None:
    """The live key for a bearer token whose maker may still manage the organization, or None."""
    parts = (raw or "").split("_")
    if len(parts) < 3 or parts[0] != KEY_PREFIX or len(parts[1]) != 8:
        return None
    key = (ApiKey.objects.filter(prefix=parts[1])
           .select_related("organization", "membership__user", "membership__role", "membership__organization")
           .first())
    if key is None or not hmac.compare_digest(key.key_hash, _hash(raw)) or not key.is_live:
        return None
    if not maker_holds_key(key.membership):
        return None
    now = timezone.now()
    if key.last_used_at is None or now - key.last_used_at >= TOUCH_EVERY:
        ApiKey.objects.filter(pk=key.pk).update(last_used_at=now)
        key.last_used_at = now
    return key


def maker_holds_key(membership: Membership) -> bool:
    """The maker still works here and may still manage the organization (item 3). Checked on the capability
    itself rather than `can`, so a lapsed (read-only) organization can still read."""
    return (is_membership_usable(membership) and membership.all_properties
            and MANAGE in effective_capabilities(membership))
