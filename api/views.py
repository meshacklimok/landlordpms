"""API v1 (D-082 items 4 and 5): GET /api/v1/<resource>/ with `Authorization: Bearer <key>`.

Writes (D-087): POST payments and tenants, PATCH a tenant, through the same services as the pages.
"""

import datetime
import hashlib
import json
import uuid

from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from accounts.permissions import can, visible_properties
from billing.models import Invoice
from billing.selectors import visible_invoices
from core import ratelimit
from leases.models import Lease
from leases.services import visible_leases
from payments import services as payment_services
from payments.models import Payment
from payments.selectors import visible_payments
from properties.models import Property, Unit
from tenants import services as tenant_services
from tenants.models import Tenant
from tenants.services import visible_tenants

from . import services
from .models import ApiKey, IdempotentRequest

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
REQUESTS_PER_MINUTE = 120
MAX_BODY = 16 * 1024
IDEMPOTENCY_DAYS = 1
# What the API may write on a tenant: what it can read, less the status (D-087 item 1).
TENANT_WRITE_FIELDS = ("kind", "name", "contact_person", "phone", "alt_phone", "email", "language")
PAYMENT_FIELDS = ("lease_id", "amount", "method", "paid_at", "reference", "tenant_id")


def _money(value) -> str | None:
    return None if value is None else f"{value:.2f}"


def _iso(value) -> str | None:
    return None if value is None else value.isoformat()


def _property(p: Property) -> dict:
    return {"id": str(p.public_id), "name": p.name, "code": p.code, "category": p.category, "county": p.county,
            "area": p.area, "street": p.street, "updated_at": _iso(p.updated_at)}


def _unit(u: Unit) -> dict:
    return {"id": str(u.public_id), "property_id": str(u.property.public_id), "code": u.code,
            "type": u.unit_type, "label": u.type_label, "payment_reference": u.payment_reference,
            "asking_rent": _money(u.list_rent), "status": u.manual_status, "updated_at": _iso(u.updated_at)}


def _tenant(t) -> dict:
    # Never the ID number, KRA PIN, emergency contact or notes (item 4).
    return {"id": str(t.public_id), "kind": t.kind, "name": t.name, "phone": t.phone, "email": t.email,
            "status": t.status, "language": t.language, "updated_at": _iso(t.updated_at)}


def _lease(lease: Lease) -> dict:
    return {"id": str(lease.public_id), "number": lease.number, "unit_id": str(lease.unit.public_id),
            "tenant_ids": [str(lt.tenant.public_id) for lt in lease.lease_tenants.all()],
            "status": lease.status, "start_date": _iso(lease.start_date), "end_date": _iso(lease.end_date),
            "ended_on": _iso(lease.ended_on), "rent": _money(lease.current_rent),
            "deposit": _money(lease.deposit_amount), "currency": lease.currency,
            "billing_frequency": lease.billing_frequency, "due_day": lease.due_day,
            "updated_at": _iso(lease.updated_at)}


def _invoice(i: Invoice) -> dict:
    return {"id": str(i.public_id), "number": i.number, "lease_id": str(i.lease.public_id), "status": i.status,
            "period_start": _iso(i.period_start), "period_end": _iso(i.period_end),
            "issue_date": _iso(i.issue_date), "due_date": _iso(i.due_date), "currency": i.currency,
            "subtotal": _money(i.subtotal), "tax_total": _money(i.tax_total), "total": _money(i.total),
            "amount_paid": _money(i.amount_paid), "outstanding": _money(i.outstanding),
            "updated_at": _iso(i.updated_at)}


def _payment(p) -> dict:
    receipt = getattr(p, "receipt", None) if p.status == p.Status.CONFIRMED else None
    return {"id": str(p.public_id), "lease_id": str(p.lease.public_id),
            "tenant_id": str(p.tenant.public_id) if p.tenant_id else None,
            "amount": _money(p.amount), "currency": p.lease.currency, "method": p.method, "status": p.status,
            "paid_at": _iso(p.paid_at), "reference": p.reference,
            "receipt_number": receipt.number if receipt else None, "updated_at": _iso(p.updated_at)}


# resource: (scope, capability, queryset for the member, serializer)
RESOURCES = {
    "properties": (ApiKey.Scope.PROPERTIES, "properties.view",
                   lambda m: Property.objects.for_org(m.organization).filter(
                       pk__in=_property_ids(m)), _property),
    "units": (ApiKey.Scope.PROPERTIES, "units.view",
              lambda m: Unit.objects.for_org(m.organization).filter(
                  property__archived_at__isnull=True, property_id__in=_property_ids(m))
              .select_related("property"), _unit),
    "tenants": (ApiKey.Scope.TENANTS, "tenants.view", lambda m: visible_tenants(m, Tenant.objects.all()), _tenant),
    "leases": (ApiKey.Scope.LEASES, "leases.view",
               lambda m: visible_leases(m).select_related("unit").prefetch_related("lease_tenants__tenant"),
               _lease),
    "invoices": (ApiKey.Scope.INVOICES, "invoices.view",
                 lambda m: visible_invoices(m).exclude(status=Invoice.Status.DRAFT), _invoice),
    "payments": (ApiKey.Scope.PAYMENTS, "payments.view", visible_payments, _payment),
}


def _property_ids(membership):
    return visible_properties(membership, Property.objects.all()).values("pk")


def error(status: int, code: str, message: str, **headers) -> JsonResponse:
    response = JsonResponse({"error": {"code": code, "message": message}}, status=status)
    for name, value in headers.items():
        response[name.replace("_", "-")] = value
    return response


class Problem(Exception):
    """An error answer raised from inside a write."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@method_decorator(csrf_exempt, name="dispatch")  # bearer keys, no cookies (D-082 item 5)
class ApiView(View):
    """Authenticates the bearer key, applies the rate limit and marks every answer no-store."""

    http_method_names = ["get", "post", "patch"]
    allowed = ("GET",)

    def dispatch(self, request, *args, **kwargs):
        if request.method not in self.allowed:
            response = error(405, "method_not_allowed", f"Allowed: {', '.join(self.allowed)}.",
                             Allow=", ".join(self.allowed))
        else:
            header = request.headers.get("Authorization", "")
            raw = header[7:].strip() if header[:7].lower() == "bearer " else ""
            key = services.authenticate(raw) if raw else None
            if key is None:
                response = error(401, "unauthorized", "Missing, wrong, revoked or expired API key.",
                                 WWW_Authenticate='Bearer realm="api"')
            elif not ratelimit.hit(f"api:{key.pk}", REQUESTS_PER_MINUTE, 60):
                response = error(429, "rate_limited", f"At most {REQUESTS_PER_MINUTE} requests a minute.",
                                 Retry_After="60")
            else:
                request.api_key = key
                response = super().dispatch(request, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response


class MeView(ApiView):
    def get(self, request):
        key = request.api_key
        return JsonResponse({"organization": {"id": str(key.organization.public_id),
                                              "name": key.organization.name},
                             "key": {"id": str(key.public_id), "name": key.name, "scopes": key.scopes,
                                     "expires_at": _iso(key.expires_at)}})


def _readable(request, resource):
    """(queryset, serializer) for a resource the key may read, or an error answer."""
    if resource not in RESOURCES:
        return error(404, "not_found", "No such resource.")
    scope, capability, queryset, serialize = RESOURCES[resource]
    key, member = request.api_key, request.api_key.membership
    if scope not in key.scopes or not can(member, capability):
        return error(403, "forbidden", f"This key may not read {resource}.")
    return queryset(member), serialize


class ResourceView(ApiView):
    allowed = ("GET", "POST")

    def get(self, request, resource):
        found = _readable(request, resource)
        if isinstance(found, JsonResponse):
            return found
        qs, serialize = found
        params = request.GET
        try:
            limit = int(params.get("limit") or DEFAULT_LIMIT)
        except ValueError:
            return error(400, "bad_parameter", "limit must be a number.")
        if not 1 <= limit <= MAX_LIMIT:
            return error(400, "bad_parameter", f"limit must be between 1 and {MAX_LIMIT}.")
        if params.get("updated_since"):
            since = _parse_since(params["updated_since"])
            if since is None:
                return error(400, "bad_parameter", "updated_since must be an ISO date or date-time.")
            qs = qs.filter(updated_at__gte=since)
        if params.get("cursor"):
            try:
                after = signing.loads(params["cursor"], salt=f"api.cursor.{resource}")
            except signing.BadSignature:
                return error(400, "bad_parameter", "cursor is not valid.")
            qs = qs.filter(pk__gt=after)
        rows = list(qs.order_by("pk")[:limit + 1])
        more = len(rows) > limit
        rows = rows[:limit]
        return JsonResponse({
            "data": [serialize(row) for row in rows],
            "next_cursor": signing.dumps(rows[-1].pk, salt=f"api.cursor.{resource}") if more else None,
        })

    def post(self, request, resource):
        return _write(request, CREATES, resource)


def _parse_since(value: str) -> datetime.datetime | None:
    try:
        moment = parse_datetime(value)
        if moment is None:
            day = parse_date(value)
            if day is None:
                return None
            moment = datetime.datetime.combine(day, datetime.time.min)
    except ValueError:
        return None
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment)
    return moment


# ---------------------------------------------------------------------------
# Writes (D-087)
# ---------------------------------------------------------------------------


def _find(queryset, value, name: str):
    try:
        public_id = uuid.UUID(str(value))
    except ValueError:
        raise Problem(404, "not_found", f"No such {name}.") from None
    obj = queryset.filter(public_id=public_id).first()
    if obj is None:
        raise Problem(404, "not_found", f"No such {name}.")
    return obj


def _fields(data: dict, allowed) -> dict:
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise Problem(400, "bad_parameter", f"Unknown field(s): {', '.join(unknown)}.")
    return data


def _text(data: dict, name: str, required=False) -> str | None:
    value = data.get(name)
    if value is None:
        if required:
            raise Problem(400, "bad_parameter", f"{name} is required.")
        return None
    if not isinstance(value, str):
        raise Problem(400, "bad_parameter", f"{name} must be a string.")
    if required and not value.strip():
        raise Problem(400, "bad_parameter", f"{name} is required.")
    return value


def _create_payment(request, data: dict):
    member = request.api_key.membership
    _fields(data, PAYMENT_FIELDS)
    lease = _find(visible_leases(member), _text(data, "lease_id", required=True), "lease")
    amount = data.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, (str, int, float)):
        raise Problem(400, "bad_parameter", "amount is required, as a string such as \"15000.00\".")
    paid_at = None
    if data.get("paid_at") is not None:
        text = _text(data, "paid_at")
        paid_at = parse_date(text) if len(text) == 10 else None
        if paid_at is None:
            raise Problem(400, "bad_parameter", "paid_at must be a date such as 2026-10-01.")
    tenant = None
    if data.get("tenant_id") is not None:
        tenant = _find(visible_tenants(member, Tenant.objects.all()), _text(data, "tenant_id"), "tenant")
    payment = payment_services.record_payment(
        member, lease, amount=str(amount), method=_text(data, "method", required=True), paid_at=paid_at,
        reference=_text(data, "reference") or "", tenant=tenant, request=request)
    payment = Payment.objects.select_related("lease", "tenant").get(pk=payment.pk)
    return 201, _payment(payment)


def _create_tenant(request, data: dict):
    _fields(data, TENANT_WRITE_FIELDS)
    fields = {f: _text(data, f) for f in TENANT_WRITE_FIELDS if f in data}
    name, phone = _text(data, "name", required=True), _text(data, "phone", required=True)
    fields.pop("name"), fields.pop("phone")
    tenant = tenant_services.create_tenant(request.api_key.membership, name=name, phone=phone, request=request,
                                           **fields)
    return 201, _tenant(tenant)


def _update_tenant(request, data: dict, public_id):
    member = request.api_key.membership
    tenant = _find(visible_tenants(member, Tenant.objects.all()), public_id, "tenant")
    _fields(data, TENANT_WRITE_FIELDS)
    if not data:
        raise Problem(400, "bad_parameter", "Send at least one field to change.")
    fields = {f: _text(data, f, required=f in ("name", "phone")) for f in data}
    tenant = tenant_services.update_tenant(member, tenant, request=request, **fields)
    return 200, _tenant(tenant)


# resource: (write scope, needs an Idempotency-Key, handler)
CREATES = {
    "payments": (ApiKey.Scope.WRITE_PAYMENTS, True, _create_payment),
    "tenants": (ApiKey.Scope.WRITE_TENANTS, False, _create_tenant),
}
UPDATES = {
    "tenants": (ApiKey.Scope.WRITE_TENANTS, False, _update_tenant),
}


def _invalid(exc: ValidationError) -> JsonResponse:
    if hasattr(exc, "error_dict"):
        fields = {name: [str(m) for m in messages] for name, messages in exc.message_dict.items()}
        message = " ".join(m for messages in fields.values() for m in messages)
    else:
        fields, message = {}, " ".join(str(m) for m in exc.messages)
    response = error(422, "invalid", message)
    body = json.loads(response.content)
    body["error"]["fields"] = fields
    return JsonResponse(body, status=422)


def _write(request, table: dict, resource: str, *args) -> JsonResponse:
    """Runs one write: scope, body, Idempotency-Key, then the service (D-087 items 2 to 4)."""
    if resource not in RESOURCES:
        return error(404, "not_found", "No such resource.")
    if resource not in table:
        return error(405, "method_not_allowed", f"{resource} cannot be changed this way.", Allow="GET")
    scope, needs_key, handler = table[resource]
    key = request.api_key
    if scope not in key.scopes:
        return error(403, "forbidden", f"This key may not change {resource}.")
    idem = request.headers.get("Idempotency-Key", "").strip()
    if needs_key and not idem:
        return error(400, "idempotency_key_required", "Send an Idempotency-Key header, unique for each payment.")
    if len(idem) > 100:
        return error(400, "bad_parameter", "Idempotency-Key is at most 100 characters.")
    if request.content_type != "application/json":
        return error(415, "unsupported_media_type", "Send the body as application/json.")
    if len(request.body) > MAX_BODY:
        return error(413, "too_large", f"The body is at most {MAX_BODY // 1024} KB.")
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return error(400, "bad_json", "The body is not valid JSON.")
    if not isinstance(data, dict):
        return error(400, "bad_json", "The body must be a JSON object.")
    digest = hashlib.sha256(f"{request.method} {request.path}\n{json.dumps(data, sort_keys=True)}".encode()).hexdigest()
    with transaction.atomic():
        ApiKey.objects.select_for_update().filter(pk=key.pk).exists()  # one write at a time per key
        if idem:
            IdempotentRequest.objects.filter(
                api_key=key, created_at__lt=timezone.now() - datetime.timedelta(days=IDEMPOTENCY_DAYS)).delete()
            prior = IdempotentRequest.objects.filter(api_key=key, key=idem).first()
            if prior is not None:
                if prior.request_hash != digest:
                    return error(422, "idempotency_mismatch",
                                 "This Idempotency-Key was used with a different request.")
                response = JsonResponse(prior.body, status=prior.status)
                response["Idempotent-Replayed"] = "true"
                return response
        try:
            with transaction.atomic():
                status, body = handler(request, data, *args)
        except Problem as exc:
            return error(exc.status, exc.code, exc.message)
        except PermissionDenied:
            return error(403, "forbidden", f"The key's maker may not change {resource}.")
        except ValidationError as exc:
            return _invalid(exc)
        if idem:
            IdempotentRequest.objects.create(api_key=key, key=idem, request_hash=digest, status=status, body=body)
    return JsonResponse(body, status=status)


class ItemView(ApiView):
    allowed = ("GET", "PATCH")

    def get(self, request, resource, public_id):
        found = _readable(request, resource)
        if isinstance(found, JsonResponse):
            return found
        qs, serialize = found
        row = qs.filter(public_id=public_id).first()
        if row is None:
            return error(404, "not_found", "No such item.")
        return JsonResponse(serialize(row))

    def patch(self, request, resource, public_id):
        return _write(request, UPDATES, resource, public_id)
