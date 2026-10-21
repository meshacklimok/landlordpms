"""Webhooks (D-083): audit events become signed POSTs to the organization's own endpoints.

The `send_webhooks` job calls `run()`: it turns new audit events into deliveries (`enqueue`), sends the
ones that are due (`send_due`) and deletes old ones (`purge`).
"""

import datetime
import hashlib
import hmac
import http.client
import ipaddress
import json
import secrets
import socket
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import F, Max
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import effective_capabilities, require
from audit import services as audit
from audit.models import AuditEvent
from core import crypto

from . import views as api_views
from .models import WebhookDelivery, WebhookEndpoint
from .services import MANAGE, maker_holds_key

MAX_ENDPOINTS = 5
SECRET_PREFIX = "whsec"  # noqa: S105 - the start of every generated secret
SETTLE = datetime.timedelta(minutes=2)
KEEP = datetime.timedelta(days=30)
TIMEOUT = 10
CLAIM = datetime.timedelta(minutes=5)
RETRY_DELAYS = [datetime.timedelta(minutes=m) for m in (1, 5, 30, 120, 360, 720)]
AUTO_PAUSE_AFTER = 25
READ_BYTES = 1024
BATCH = 500
USER_AGENT = "NyumbaPMS-Webhooks/1"

E = WebhookEndpoint.Event
# Audit action -> webhook event (item 1).
ACTIONS = {
    "payment.confirm": E.PAYMENT_CONFIRMED,
    "payment.reverse": E.PAYMENT_REVERSED,
    "invoice.issue": E.INVOICE_ISSUED,
    "invoice.void": E.INVOICE_VOIDED,
    "lease.activate": E.LEASE_ACTIVATED,
    "lease.end": E.LEASE_ENDED,
    "lease.terminate": E.LEASE_ENDED,
    "lease.renewed": E.LEASE_ENDED,
    "lease.transferred": E.LEASE_ENDED,
    "tenant.create": E.TENANT_CREATED,
    "tenant.update": E.TENANT_UPDATED,
}
# Event prefix -> the API resource it carries (scope, capability, queryset and serializer).
RESOURCE_FOR = {"payment": "payments", "invoice": "invoices", "lease": "leases", "tenant": "tenants"}
PING = "ping"


# ---------------------------------------------------------------------------
# Safety (item 5)
# ---------------------------------------------------------------------------


def check_url(url: str) -> str | None:
    """Raises ValidationError unless the URL is https on the standard port and resolves only to public
    addresses. Development may allow http and private addresses with WEBHOOKS_ALLOW_PRIVATE.

    Returns the checked address to connect to (None when private addresses are allowed), so the POST
    does not look the name up again and get a different, private answer (DNS rebinding)."""
    allow_private = getattr(settings, "WEBHOOKS_ALLOW_PRIVATE", False)
    parts = urlsplit(url or "")
    if parts.scheme != "https" and not (allow_private and parts.scheme == "http"):
        raise ValidationError(_("Use an https:// address."))
    if not parts.hostname or parts.username or parts.password:
        raise ValidationError(_("Enter a web address without a user name or password."))
    try:
        port = parts.port
    except ValueError:
        raise ValidationError(_("The port in this address is not valid.")) from None
    if allow_private:
        return None
    if port not in (None, 443):
        raise ValidationError(_("Use the standard https port (443)."))
    try:
        infos = socket.getaddrinfo(parts.hostname, 443, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        raise ValidationError(_("This address could not be found.")) from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            raise ValidationError(_("This address points to a private network. Use a public address."))
    return infos[0][4][0]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # a 3xx comes back as an HTTPError and counts as a failure


class _PinnedHTTPS(urllib.request.HTTPSHandler):
    """Connects to `address` instead of looking the host up again; TLS still checks the host name."""

    def __init__(self, address: str):
        super().__init__()
        self.address = address

    def https_open(self, req):
        def connection(host, **kwargs):
            conn = http.client.HTTPSConnection(host, **kwargs)
            conn._create_connection = lambda where, *a, **k: socket.create_connection((self.address, where[1]), *a, **k)
            return conn

        return self.do_open(connection, req, context=self._context)


def _post(url: str, body: bytes, headers: dict, address: str | None = None) -> tuple[int, str]:
    """(HTTP status, at most 1 KB of the answer). Raises OSError when nothing came back."""
    handlers = [urllib.request.ProxyHandler({}), _NoRedirect]
    if address:
        handlers.append(_PinnedHTTPS(address))
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310 - check_url: https
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            return response.status, response.read(READ_BYTES).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            excerpt = exc.read(READ_BYTES).decode("utf-8", "replace")
        except OSError:
            excerpt = ""
        return exc.code, excerpt


def sign(secret: str, timestamp: int, body: bytes) -> str:
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


# ---------------------------------------------------------------------------
# Endpoints (item 2)
# ---------------------------------------------------------------------------


def visible_endpoints(membership: Membership):
    return WebhookEndpoint.objects.filter(organization=membership.organization).select_related("membership__user")


def _clean_events(events) -> list[str]:
    events = sorted(set(events or ()))
    if not events or not set(events) <= set(E.values):
        raise ValidationError(_("Choose the events to send."))
    return events


def _own(actor: Membership, endpoint: WebhookEndpoint) -> WebhookEndpoint:
    require(actor, MANAGE)
    if endpoint.organization_id != actor.organization_id:
        raise ValidationError(_("Webhook not found."))
    return WebhookEndpoint.objects.select_for_update().get(pk=endpoint.pk)


@transaction.atomic
def create_endpoint(actor: Membership, *, url: str, events, request=None) -> tuple[WebhookEndpoint, str]:
    """Returns the endpoint and its signing secret. Show the secret once."""
    require(actor, MANAGE)
    url = (url or "").strip()
    events = _clean_events(events)
    check_url(url)
    existing = WebhookEndpoint.objects.filter(organization=actor.organization).select_for_update()
    if len(existing) >= MAX_ENDPOINTS:
        raise ValidationError(_("You already have %(n)s webhooks. Delete one first.") % {"n": MAX_ENDPOINTS})
    secret = f"{SECRET_PREFIX}_{secrets.token_urlsafe(32)}"
    # No backfill: only events after this one are sent.
    last = AuditEvent.objects.filter(organization=actor.organization).aggregate(m=Max("pk"))["m"] or 0
    endpoint = WebhookEndpoint.objects.create(
        organization=actor.organization, membership=actor, url=url, events=events,
        secret_encrypted=crypto.encrypt(secret), last_audit_id=last)
    audit.record("webhook.create", actor=actor.user, organization=actor.organization, obj=endpoint, request=request,
                 changes={"url": [None, url], "events": [None, events]})
    return endpoint, secret


@transaction.atomic
def update_events(actor: Membership, endpoint: WebhookEndpoint, events, request=None) -> WebhookEndpoint:
    endpoint = _own(actor, endpoint)
    events = _clean_events(events)
    if events != endpoint.events:
        audit.record("webhook.update", actor=actor.user, organization=actor.organization, obj=endpoint,
                     request=request, changes={"events": [endpoint.events, events]})
        endpoint.events = events
        endpoint.save(update_fields=["events", "updated_at"])
    return endpoint


@transaction.atomic
def pause(actor: Membership, endpoint: WebhookEndpoint, request=None) -> None:
    endpoint = _own(actor, endpoint)
    if endpoint.paused_at is None:
        endpoint.paused_at = timezone.now()
        endpoint.pause_reason = _("Paused by %(who)s.") % {"who": actor.user.full_name}
        endpoint.save(update_fields=["paused_at", "pause_reason", "updated_at"])
        audit.record("webhook.pause", actor=actor.user, organization=actor.organization, obj=endpoint,
                     request=request, changes={"paused": [False, True]})


@transaction.atomic
def resume(actor: Membership, endpoint: WebhookEndpoint, request=None) -> None:
    endpoint = _own(actor, endpoint)
    check_url(endpoint.url)
    if endpoint.paused_at is not None:
        endpoint.paused_at = None
        endpoint.pause_reason = ""
        endpoint.consecutive_failures = 0
        endpoint.save(update_fields=["paused_at", "pause_reason", "consecutive_failures", "updated_at"])
        audit.record("webhook.resume", actor=actor.user, organization=actor.organization, obj=endpoint,
                     request=request, changes={"paused": [True, False]})


@transaction.atomic
def delete_endpoint(actor: Membership, endpoint: WebhookEndpoint, request=None) -> None:
    endpoint = _own(actor, endpoint)
    audit.record("webhook.delete", actor=actor.user, organization=actor.organization, obj=endpoint, request=request,
                 changes={"url": [endpoint.url, None]})
    endpoint.delete()


def ping(actor: Membership, endpoint: WebhookEndpoint, request=None) -> WebhookDelivery:
    """Sends a test event now, once, without retries."""
    with transaction.atomic():
        endpoint = _own(actor, endpoint)
        delivery = WebhookDelivery.objects.create(organization=endpoint.organization, endpoint=endpoint, event=PING)
        delivery.payload = _payload(delivery, PING, timezone.now(), {"message": "Test from NyumbaPMS"})
        delivery.save(update_fields=["payload", "updated_at"])
    return attempt(delivery, retry=False)


def redeliver(actor: Membership, delivery: WebhookDelivery, request=None) -> WebhookDelivery:
    """Sends a delivery again now. If that fails it is retried on the usual schedule."""
    require(actor, MANAGE)
    if delivery.organization_id != actor.organization_id:
        raise ValidationError(_("Delivery not found."))
    if delivery.status != WebhookDelivery.Status.FAILED:
        raise ValidationError(_("Only a failed delivery can be sent again."))
    WebhookDelivery.objects.filter(pk=delivery.pk).update(
        status=WebhookDelivery.Status.PENDING, attempts=0, next_attempt_at=timezone.now())
    audit.record("webhook.redeliver", actor=actor.user, organization=actor.organization, obj=delivery.endpoint,
                 request=request, changes={"delivery": [None, str(delivery.public_id)]})
    delivery.refresh_from_db()
    return attempt(delivery)


# ---------------------------------------------------------------------------
# Turning audit events into deliveries (items 1 and 3)
# ---------------------------------------------------------------------------


def blocked_reason(endpoint: WebhookEndpoint) -> str:
    """Why nothing is sent right now, or "" when it may send."""
    if endpoint.paused_at is not None:
        return endpoint.pause_reason or _("Paused.")
    if not maker_holds_key(endpoint.membership):
        return _("Waiting: the person who added this webhook can no longer manage the organization.")
    return ""


def _payload(delivery: WebhookDelivery, event: str, created_at, obj) -> dict:
    return {"id": str(delivery.public_id), "type": event, "created_at": created_at.isoformat(),
            "data": {"object": obj}}


def _object_for(member: Membership, event: str, object_id: str):
    _scope, _capability, queryset, serialize = api_views.RESOURCES[RESOURCE_FOR[event.split(".")[0]]]
    row = queryset(member).filter(public_id=object_id).first() if object_id else None
    return serialize(row) if row is not None else None


def enqueue(now=None) -> int:
    """New deliveries for audit events at least SETTLE old. An endpoint whose maker cannot send keeps its
    cursor, so its events wait (up to KEEP) until the maker can send again."""
    now = now or timezone.now()
    created = 0
    for endpoint in WebhookEndpoint.objects.select_related(
            "organization", "membership__user", "membership__role", "membership__organization"):
        member = endpoint.membership
        if not maker_holds_key(member):
            continue
        capabilities = effective_capabilities(member)
        horizon = now - SETTLE
        events = (AuditEvent.objects.filter(organization_id=endpoint.organization_id,
                                            pk__gt=endpoint.last_audit_id, created_at__lte=horizon)
                  .order_by("pk"))
        batch = list(events.filter(action__in=ACTIONS, created_at__gte=now - KEEP)[:BATCH])
        for audit_event in batch:
            event = ACTIONS[audit_event.action]
            capability = api_views.RESOURCES[RESOURCE_FOR[event.split(".")[0]]][1]
            if event not in endpoint.events or capability not in capabilities:
                continue
            with transaction.atomic():
                delivery, made = WebhookDelivery.objects.get_or_create(
                    endpoint=endpoint, audit_event=audit_event,
                    defaults={"organization_id": endpoint.organization_id, "event": event, "next_attempt_at": now})
                if made:
                    obj = _object_for(member, event, audit_event.object_id)
                    delivery.payload = _payload(delivery, event, audit_event.created_at, obj)
                    delivery.save(update_fields=["payload", "updated_at"])
                    created += 1
        if len(batch) == BATCH:
            cursor = batch[-1].pk
        else:
            cursor = events.aggregate(m=Max("pk"))["m"] or endpoint.last_audit_id
        WebhookEndpoint.objects.filter(pk=endpoint.pk, last_audit_id__lt=cursor).update(last_audit_id=cursor)
    return created


# ---------------------------------------------------------------------------
# Sending (item 4)
# ---------------------------------------------------------------------------


def attempt(delivery: WebhookDelivery, *, retry: bool = True, now=None) -> WebhookDelivery:
    """Sends one delivery once and records the outcome."""
    endpoint = delivery.endpoint
    body = json.dumps(delivery.payload, cls=DjangoJSONEncoder, separators=(",", ":")).encode()
    stamp = int(time.time())
    headers = {
        "Content-Type": "application/json", "User-Agent": USER_AGENT,
        "X-LPMS-Event": delivery.event, "X-LPMS-Delivery": str(delivery.public_id),
        "X-LPMS-Signature": sign(crypto.decrypt(endpoint.secret_encrypted), stamp, body),
    }
    status, excerpt, error = None, "", ""
    try:
        address = check_url(endpoint.url)
        status, excerpt = _post(endpoint.url, body, headers, address)
    except ValidationError as exc:
        error = " ".join(exc.messages)
    except (OSError, ValueError) as exc:  # timeouts, refused connections, TLS errors
        error = str(exc) or exc.__class__.__name__
    now = now or timezone.now()
    ok = status is not None and 200 <= status < 300
    delivery.attempts += 1
    delivery.last_status_code = status
    delivery.last_error = "" if ok else (error or _("Answered %(code)s.") % {"code": status})[:300]
    delivery.response_excerpt = excerpt[:READ_BYTES]
    if ok:
        delivery.status = WebhookDelivery.Status.SENT
        delivery.delivered_at = now
    elif retry and delivery.attempts <= len(RETRY_DELAYS):
        delivery.next_attempt_at = now + RETRY_DELAYS[delivery.attempts - 1]
    else:
        delivery.status = WebhookDelivery.Status.FAILED
    delivery.save(update_fields=["attempts", "last_status_code", "last_error", "response_excerpt", "status",
                                 "delivered_at", "next_attempt_at", "updated_at"])
    if ok:
        WebhookEndpoint.objects.filter(pk=endpoint.pk).update(consecutive_failures=0, last_success_at=now)
    else:
        WebhookEndpoint.objects.filter(pk=endpoint.pk).update(consecutive_failures=F("consecutive_failures") + 1)
        WebhookEndpoint.objects.filter(
            pk=endpoint.pk, paused_at__isnull=True, consecutive_failures__gte=AUTO_PAUSE_AFTER,
        ).update(paused_at=now, pause_reason=_("Paused after %(n)s failed deliveries in a row.")
                 % {"n": AUTO_PAUSE_AFTER})
    return delivery


def send_due(now=None) -> dict:
    now = now or timezone.now()
    counts = {"sent": 0, "will_retry": 0, "failed": 0}
    due = list(WebhookDelivery.objects.filter(
        status=WebhookDelivery.Status.PENDING, next_attempt_at__lte=now, endpoint__paused_at__isnull=True,
    ).order_by("next_attempt_at", "pk").values_list("pk", flat=True)[:BATCH])
    for pk in due:
        # Claim the row in a short transaction, then POST outside it: no lock or open transaction is
        # held while a slow endpoint answers. A run that dies mid-send leaves it due again after CLAIM.
        with transaction.atomic():
            delivery = (WebhookDelivery.objects.select_for_update(skip_locked=True)
                        .filter(pk=pk, status=WebhookDelivery.Status.PENDING, next_attempt_at__lte=now).first())
            if delivery is None:
                continue
            endpoint = (WebhookEndpoint.objects
                        .select_related("membership__user", "membership__role", "membership__organization")
                        .get(pk=delivery.endpoint_id))
            if blocked_reason(endpoint):  # paused by an earlier failure in this run, or the maker left
                continue
            WebhookDelivery.objects.filter(pk=pk).update(next_attempt_at=now + CLAIM)
        delivery.endpoint = endpoint
        attempt(delivery, now=now)
        if delivery.status == WebhookDelivery.Status.SENT:
            counts["sent"] += 1
        elif delivery.status == WebhookDelivery.Status.FAILED:
            counts["failed"] += 1
        else:
            counts["will_retry"] += 1
    return counts


def purge(now=None) -> int:
    now = now or timezone.now()
    deleted, _rows = WebhookDelivery.objects.filter(created_at__lt=now - KEEP).delete()
    return deleted


def run(now=None) -> dict:
    now = now or timezone.now()
    counts = {"queued": enqueue(now)}
    counts.update(send_due(now))
    counts["purged"] = purge(now)
    return counts
