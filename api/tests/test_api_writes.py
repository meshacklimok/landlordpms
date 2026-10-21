"""API writes: record payments, add and update tenants (D-087)."""

import datetime
import json

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from accounts.models import Organization
from accounts.tests.factories import make_org, make_property
from accounts.tests.test_isolation import login
from api import services
from api.models import ApiKey, IdempotentRequest
from audit.models import AuditEvent
from billing.tests.test_invoicing import FEB, bill, make_lease
from payments.models import Payment
from tenants.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()


@pytest.fixture
def owner():
    return make_org(name="Acme")


@pytest.fixture
def prop(owner):
    return make_property(owner.organization, name="Acacia")


@pytest.fixture
def lease(owner, prop):
    lease = make_lease(owner, prop)
    bill(lease, FEB)
    return lease


def key_with(owner, *scopes):
    return services.create_key(owner, name="sync", scopes=list(scopes))[1]


def send(client, secret, method, url, body, idem=None, content_type="application/json"):
    headers = {"HTTP_AUTHORIZATION": f"Bearer {secret}"}
    if idem:
        headers["HTTP_IDEMPOTENCY_KEY"] = idem
    data = body if isinstance(body, (str, bytes)) else json.dumps(body)
    return getattr(client, method)(url, data, content_type=content_type, **headers)


PAYMENTS = reverse("api:resource", args=["payments"])
TENANTS = reverse("api:resource", args=["tenants"])


def pay(client, secret, body, idem="pay-1"):
    return send(client, secret, "post", PAYMENTS, body, idem)


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------


def test_a_payment_is_recorded_confirmed_and_marked_as_from_the_api(client, owner, lease):
    secret = key_with(owner, "write_payments")
    response = pay(client, secret, {"lease_id": str(lease.public_id), "amount": "15000.00", "method": "BANK",
                                    "reference": "FT123", "paid_at": "2026-02-03"})
    assert response.status_code == 201, response.content
    body = response.json()
    payment = Payment.objects.get()
    assert body["id"] == str(payment.public_id) and body["status"] == "CONFIRMED" and body["receipt_number"]
    assert (body["amount"], body["method"], body["reference"], body["paid_at"]) == ("15000.00", "BANK", "FT123",
                                                                                   "2026-02-03")
    assert payment.recorded_by == owner.user and payment.tenant == lease.primary_tenant
    for action in ("payment.record", "payment.confirm"):
        event = AuditEvent.objects.get(action=action, object_id=str(payment.public_id))
        assert event.actor == owner.user and event.changes["api_key"][1].startswith("sync (lpk_")


def test_a_retry_with_the_same_idempotency_key_does_not_pay_twice(client, owner, lease):
    secret = key_with(owner, "write_payments")
    body = {"lease_id": str(lease.public_id), "amount": "5000", "method": "CASH"}
    first = pay(client, secret, body)
    again = pay(client, secret, dict(reversed(list(body.items()))))  # the same body, keys in another order
    assert again.status_code == 201 and again.json() == first.json() and again["Idempotent-Replayed"] == "true"
    assert Payment.objects.count() == 1
    other = pay(client, secret, {**body, "amount": "6000"})
    assert other.status_code == 422 and other.json()["error"]["code"] == "idempotency_mismatch"
    assert pay(client, secret, body, idem="pay-2").status_code == 201 and Payment.objects.count() == 2
    # After a day the key may be used again.
    IdempotentRequest.objects.update(created_at=timezone.now() - datetime.timedelta(days=1, seconds=1))
    assert "Idempotent-Replayed" not in pay(client, secret, body) and Payment.objects.count() == 3


def test_a_payment_needs_an_idempotency_key_and_a_json_body(client, owner, lease):
    secret = key_with(owner, "write_payments")
    body = {"lease_id": str(lease.public_id), "amount": "5000", "method": "CASH"}
    missing = pay(client, secret, body, idem=None)
    assert missing.status_code == 400 and missing.json()["error"]["code"] == "idempotency_key_required"
    assert pay(client, secret, body, idem="x" * 101).status_code == 400
    assert send(client, secret, "post", PAYMENTS, "amount=5", "k", content_type="text/plain").status_code == 415
    assert pay(client, secret, "{not json").json()["error"]["code"] == "bad_json"
    assert pay(client, secret, "[1, 2]").json()["error"]["code"] == "bad_json"
    assert send(client, secret, "post", PAYMENTS, {"pad": "x" * 17000}, "k").status_code == 413
    assert not Payment.objects.exists()


def test_a_payment_is_checked_and_a_failed_one_can_be_retried_with_the_same_key(client, owner, lease):
    secret = key_with(owner, "write_payments")
    good = {"lease_id": str(lease.public_id), "amount": "5000", "method": "CASH"}
    unknown = pay(client, secret, {**good, "allocations": []})
    assert unknown.status_code == 400 and "allocations" in unknown.json()["error"]["message"]
    assert pay(client, secret, {**good, "amount": True}).status_code == 400
    assert pay(client, secret, {**good, "paid_at": "yesterday"}).status_code == 400
    assert pay(client, secret, {**good, "lease_id": "nope"}).status_code == 404
    bad = pay(client, secret, {**good, "amount": "-5"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid"
    assert "amount" in bad.json()["error"]["fields"]
    assert "method" in pay(client, secret, {**good, "method": "GOLD"}).json()["error"]["fields"]
    future = (timezone.localdate() + datetime.timedelta(days=2)).isoformat()
    assert "paid_at" in pay(client, secret, {**good, "paid_at": future}).json()["error"]["fields"]
    assert not Payment.objects.exists() and not IdempotentRequest.objects.exists()
    assert pay(client, secret, good).status_code == 201


def test_a_key_writes_only_to_its_own_organization(client, owner, lease):
    other = make_org(name="Other")
    secret = key_with(other, "write_payments", "write_tenants")
    response = pay(client, secret, {"lease_id": str(lease.public_id), "amount": "5000", "method": "CASH"})
    assert response.status_code == 404 and not Payment.objects.exists()
    patch = send(client, secret, "patch", reverse("api:item", args=["tenants", lease.primary_tenant.public_id]),
                 {"name": "Taken over"})
    assert patch.status_code == 404
    tenant_of_acme = pay(client, key_with(owner, "write_payments"),
                         {"lease_id": str(lease.public_id), "amount": "5", "method": "CASH",
                          "tenant_id": str(Tenant.objects.create(organization=other.organization, name="X",
                                                                 phone="+254722000999").public_id)})
    assert tenant_of_acme.status_code == 404


# ---------------------------------------------------------------------------
# Scopes and the organization's state
# ---------------------------------------------------------------------------


def test_write_scopes_bring_their_read_scope_and_are_needed_to_write(client, owner, lease):
    key, secret = services.create_key(owner, name="sync", scopes=["write_tenants"])
    assert key.scopes == ["tenants", "write_tenants"]
    assert "Add and update tenants" in key.scope_labels()
    assert client.get(TENANTS, HTTP_AUTHORIZATION=f"Bearer {secret}").status_code == 200
    denied = pay(client, secret, {"lease_id": str(lease.public_id), "amount": "5", "method": "CASH"})
    assert denied.status_code == 403
    read_only = key_with(owner, "tenants", "payments")
    assert send(client, read_only, "post", TENANTS, {"name": "A", "phone": "0722000111"}).status_code == 403
    leases = send(client, secret, "post", reverse("api:resource", args=["leases"]), {})
    assert leases.status_code == 405
    assert send(client, secret, "post", reverse("api:resource", args=["nope"]), {}).status_code == 404


def test_a_lapsed_organization_can_read_but_not_write(client, owner, lease):
    secret = key_with(owner, "write_payments", "write_tenants")
    Organization.objects.filter(pk=owner.organization_id).update(status=Organization.Status.READ_ONLY)
    assert client.get(PAYMENTS, HTTP_AUTHORIZATION=f"Bearer {secret}").status_code == 200
    response = pay(client, secret, {"lease_id": str(lease.public_id), "amount": "5000", "method": "CASH"})
    assert response.status_code == 403 and not Payment.objects.exists()
    assert send(client, secret, "post", TENANTS, {"name": "A", "phone": "0722000111"}).status_code == 403


def test_the_key_page_offers_write_scopes(client, owner):
    login(client, owner)
    page = client.get(reverse("api:keys")).content.decode()
    assert "May change" in page and "Record payments" in page and "data-write-scopes" in page
    response = client.post(reverse("api:keys"), {"name": "Bank", "write_scopes": ["write_payments"]})
    assert response.status_code == 200 and response.context["secret"]
    assert ApiKey.objects.get().scopes == ["payments", "write_payments"]
    assert client.post(reverse("api:keys"), {"name": "Nothing"}).context["form"].errors


# ---------------------------------------------------------------------------
# Tenants
# ---------------------------------------------------------------------------


def test_a_tenant_is_added_and_updated(client, owner):
    secret = key_with(owner, "write_tenants")
    created = send(client, secret, "post", TENANTS, {"name": " Wanjiru Kamau ", "phone": "0722 000 111",
                                                     "email": "w@example.com", "language": "sw"})
    assert created.status_code == 201, created.content
    body = created.json()
    tenant = Tenant.objects.get(public_id=body["id"])
    assert (tenant.name, tenant.phone, tenant.language) == ("Wanjiru Kamau", "+254722000111", "sw")
    assert body["phone"] == "+254722000111" and tenant.created_by == owner.user
    item = reverse("api:item", args=["tenants", tenant.public_id])
    assert client.get(item, HTTP_AUTHORIZATION=f"Bearer {secret}").json() == body
    updated = send(client, secret, "patch", item, {"email": "wanjiru@example.com"})
    assert updated.status_code == 200 and updated.json()["email"] == "wanjiru@example.com"
    event = AuditEvent.objects.get(action="tenant.update", object_id=str(tenant.public_id))
    assert event.changes["email"] == ["w@example.com", "wanjiru@example.com"] and "api_key" in event.changes
    assert "api_key" in AuditEvent.objects.get(action="tenant.create", object_id=str(tenant.public_id)).changes


def test_tenant_writes_are_checked(client, owner):
    secret = key_with(owner, "write_tenants")
    sensitive = send(client, secret, "post", TENANTS, {"name": "A", "phone": "0722000111", "id_number": "123"})
    assert sensitive.status_code == 400 and "id_number" in sensitive.json()["error"]["message"]
    assert send(client, secret, "post", TENANTS, {"name": "A"}).status_code == 400
    assert send(client, secret, "post", TENANTS, {"name": "A", "phone": 722000111}).status_code == 400
    phone = send(client, secret, "post", TENANTS, {"name": "A", "phone": "12"})
    assert phone.status_code == 422 and "phone" in phone.json()["error"]["fields"]
    assert not Tenant.objects.exists()
    tenant = Tenant.objects.get(public_id=send(client, secret, "post", TENANTS,
                                               {"name": "A", "phone": "0722000111"}).json()["id"])
    item = reverse("api:item", args=["tenants", tenant.public_id])
    assert send(client, secret, "patch", item, {}).status_code == 400
    assert send(client, secret, "patch", item, {"name": ""}).status_code == 400
    assert send(client, secret, "patch", item, {"notes": "x"}).status_code == 400
    assert send(client, secret, "put", item, {"name": "B"}).status_code == 405
    assert send(client, secret, "patch", reverse("api:item", args=["leases", tenant.public_id]), {}).status_code == 405
    missing = reverse("api:item", args=["tenants", "00000000-0000-0000-0000-000000000000"])
    assert client.get(missing, HTTP_AUTHORIZATION=f"Bearer {secret}").status_code == 404


def test_a_tenant_write_with_an_idempotency_key_is_kept(client, owner):
    secret = key_with(owner, "write_tenants")
    body = {"name": "A", "phone": "0722000111"}
    first = send(client, secret, "post", TENANTS, body, idem="t-1")
    again = send(client, secret, "post", TENANTS, body, idem="t-1")
    assert again.json() == first.json() and Tenant.objects.count() == 1
