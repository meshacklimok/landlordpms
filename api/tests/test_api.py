"""Read-only API v1 with organization API keys (D-082)."""

import datetime
import hashlib

import pytest
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from accounts.models import Organization
from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from api import services, views
from api.models import ApiKey
from audit.models import AuditEvent
from billing.tests.test_invoicing import FEB, MAR, bill, make_lease
from payments import services as payment_services
from payments.models import Payment
from tenants import services as tenant_services

pytestmark = pytest.mark.django_db

D = datetime.date
ALL = list(ApiKey.Scope.values)


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()


@pytest.fixture
def owner():
    return make_org(name="Acme")


@pytest.fixture
def prop(owner):
    return make_property(owner.organization, name="Acacia")


def api(client, secret, resource="me", **params):
    url = reverse("api:me") if resource == "me" else reverse("api:resource", args=[resource])
    return client.get(url, params, HTTP_AUTHORIZATION=f"Bearer {secret}")


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------


def test_a_key_is_shown_once_and_only_its_hash_is_kept(owner):
    key, secret = services.create_key(owner, name=" Accounting sync ", scopes=["payments", "leases", "leases"],
                                      expires_in_days=90)
    assert secret.startswith(f"lpk_{key.prefix}_") and len(key.prefix) == 8
    assert key.key_hash == hashlib.sha256(secret.encode()).hexdigest() and secret not in key.key_hash
    assert key.name == "Accounting sync" and key.scopes == ["leases", "payments"] and key.membership == owner
    assert key.expires_at.date() == (timezone.now() + datetime.timedelta(days=90)).date()
    event = AuditEvent.objects.get(action="api_key.create", object_id=str(key.public_id))
    assert event.changes["scopes"] == [None, ["leases", "payments"]] and secret not in str(event.changes)


def test_creating_keys_is_checked(owner, prop):
    for bad in ({"name": "", "scopes": ALL}, {"name": "x", "scopes": []}, {"name": "x", "scopes": ["audit"]},
                {"name": "x", "scopes": ALL, "expires_in_days": 7}):
        with pytest.raises(ValidationError):
            services.create_key(owner, **bad)
    manager = add_member(owner.organization, "manager", properties=[prop])
    with pytest.raises(PermissionDenied):
        services.create_key(manager, name="x", scopes=ALL)
    for n in range(services.MAX_LIVE_KEYS):
        key, _secret = services.create_key(owner, name=f"k{n}", scopes=ALL)
    with pytest.raises(ValidationError):
        services.create_key(owner, name="one too many", scopes=ALL)
    services.revoke_key(owner, key)
    services.create_key(owner, name="after a revoke", scopes=ALL)
    other = make_org(name="Other")
    with pytest.raises(ValidationError):
        services.revoke_key(other, key)


# ---------------------------------------------------------------------------
# Who may call
# ---------------------------------------------------------------------------


def test_only_a_live_key_of_a_maker_who_still_manages_gets_in(client, owner):
    key, secret = services.create_key(owner, name="sync", scopes=ALL)
    response = api(client, secret)
    assert response.status_code == 200 and response["Cache-Control"] == "no-store"
    assert response.json()["organization"]["name"] == "Acme" and response.json()["key"]["scopes"] == sorted(ALL)
    key.refresh_from_db()
    assert key.last_used_at is not None
    assert client.get(reverse("api:me")).status_code == 401
    assert api(client, "lpk_00000000_nope").status_code == 401
    assert api(client, secret[:-1] + ("A" if secret[-1] != "A" else "B")).status_code == 401
    assert client.get(reverse("api:me"), HTTP_AUTHORIZATION=f"Token {secret}").status_code == 401
    # A lapsed organization can still read; a frozen one cannot.
    Organization.objects.filter(pk=owner.organization_id).update(status=Organization.Status.READ_ONLY)
    assert api(client, secret).status_code == 200
    Organization.objects.filter(pk=owner.organization_id).update(status=Organization.Status.FROZEN)
    body = api(client, secret)
    assert body.status_code == 401 and body.json()["error"]["code"] == "unauthorized"
    assert body["WWW-Authenticate"] == 'Bearer realm="api"'


def test_a_key_stops_when_revoked_expired_or_its_maker_leaves(client, owner):
    key, secret = services.create_key(owner, name="sync", scopes=ALL)
    expiring, expiring_secret = services.create_key(owner, name="short", scopes=ALL, expires_in_days=30)
    ApiKey.objects.filter(pk=expiring.pk).update(expires_at=timezone.now() - datetime.timedelta(seconds=1))
    assert api(client, expiring_secret).status_code == 401
    owner.is_active = False
    owner.save(update_fields=["is_active"])
    assert api(client, secret).status_code == 401
    owner.is_active = True
    owner.save(update_fields=["is_active"])
    assert api(client, secret).status_code == 200
    services.revoke_key(owner, key)
    assert api(client, secret).status_code == 401
    assert AuditEvent.objects.filter(action="api_key.revoke", object_id=str(key.public_id)).count() == 1


def test_scopes_resources_methods_and_the_rate_limit(client, owner, monkeypatch):
    _key, secret = services.create_key(owner, name="money", scopes=["payments"])
    assert api(client, secret, "payments").status_code == 200
    forbidden = api(client, secret, "properties")
    assert forbidden.status_code == 403 and forbidden.json()["error"]["code"] == "forbidden"
    assert api(client, secret, "units").status_code == 403
    assert api(client, secret, "audit").status_code == 404
    post = client.post(reverse("api:resource", args=["payments"]), {}, content_type="application/json",
                       HTTP_AUTHORIZATION=f"Bearer {secret}")
    assert post.status_code == 403 and post.json()["error"]["message"] == "This key may not change payments."
    delete = client.delete(reverse("api:resource", args=["payments"]), HTTP_AUTHORIZATION=f"Bearer {secret}")
    assert delete.status_code == 405 and delete["Allow"] == "GET, POST"
    monkeypatch.setattr(views, "REQUESTS_PER_MINUTE", 2)
    cache.clear()
    assert api(client, secret).status_code == 200 and api(client, secret).status_code == 200
    limited = api(client, secret)
    assert limited.status_code == 429 and limited["Retry-After"] == "60"


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


def test_the_lists_hold_only_this_organizations_data_in_plain_json(client, owner, prop):
    lease = make_lease(owner, prop, code="A1")
    tenant = lease.primary_tenant
    tenant_services.update_tenant(owner, tenant, id_type="NATIONAL_ID", id_number="12345678")
    bill(lease, FEB)
    payment = payment_services.record_payment(owner, lease, amount=5000, method=Payment.Method.CASH,
                                              paid_at=D(2026, 2, 3))
    other = make_org(name="Other")
    make_lease(other, make_property(other.organization, name="Baobab"), code="Z9")
    _key, secret = services.create_key(owner, name="sync", scopes=ALL)

    properties = api(client, secret, "properties").json()["data"]
    assert [p["name"] for p in properties] == ["Acacia"]
    units = api(client, secret, "units").json()["data"]
    assert [(u["code"], u["property_id"]) for u in units] == [("A1", str(prop.public_id))]
    tenants = api(client, secret, "tenants").json()["data"]
    assert [t["id"] for t in tenants] == [str(tenant.public_id)]
    assert not {"id_number", "kra_pin", "notes", "emergency_contact_phone"} & set(tenants[0])
    assert "12345678" not in api(client, secret, "tenants").content.decode()
    leases = api(client, secret, "leases").json()["data"]
    assert leases[0]["id"] == str(lease.public_id) and leases[0]["rent"] == "15000.00"
    assert leases[0]["tenant_ids"] == [str(tenant.public_id)] and leases[0]["start_date"] == "2026-01-01"
    invoices = api(client, secret, "invoices").json()["data"]
    assert len(invoices) == 1 and invoices[0]["total"] == "15000.00" and invoices[0]["outstanding"] == "10000.00"
    payments = api(client, secret, "payments").json()["data"]
    payment.refresh_from_db()
    assert payments == [{
        "id": str(payment.public_id), "lease_id": str(lease.public_id), "tenant_id": payments[0]["tenant_id"],
        "amount": "5000.00", "currency": "KES", "method": "CASH", "status": "CONFIRMED", "paid_at": "2026-02-03",
        "reference": payment.reference, "receipt_number": payment.receipt.number,
        "updated_at": payment.updated_at.isoformat()}]


def test_paging_with_a_signed_cursor_and_updated_since(client, owner, prop):
    lease = make_lease(owner, prop, code="A1")
    bill(lease, FEB)
    bill(lease, MAR)
    _key, secret = services.create_key(owner, name="sync", scopes=ALL)
    first = api(client, secret, "invoices", limit=1).json()
    assert len(first["data"]) == 1 and first["next_cursor"]
    second = api(client, secret, "invoices", limit=1, cursor=first["next_cursor"]).json()
    assert len(second["data"]) == 1 and second["data"][0]["id"] != first["data"][0]["id"]
    assert second["next_cursor"] is None
    # A cursor is tied to its list and cannot be made up.
    assert api(client, secret, "payments", cursor=first["next_cursor"]).status_code == 400
    assert api(client, secret, "invoices", cursor="123").status_code == 400
    for bad in ({"limit": 0}, {"limit": 201}, {"limit": "x"}, {"updated_since": "yesterday"}):
        response = api(client, secret, "invoices", **bad)
        assert response.status_code == 400 and response.json()["error"]["code"] == "bad_parameter"
    later = (timezone.now() + datetime.timedelta(minutes=5)).isoformat()
    assert api(client, secret, "invoices", updated_since=later).json()["data"] == []
    assert len(api(client, secret, "invoices", updated_since="2020-01-01").json()["data"]) == 2


# ---------------------------------------------------------------------------
# The key page
# ---------------------------------------------------------------------------


def test_the_key_page_creates_shows_once_and_revokes(client, owner, prop):
    login(client, owner)
    url = reverse("api:keys")
    assert b"API keys" in client.get(reverse("accounts:staff")).content
    page = client.get(url)
    assert page.status_code == 200 and b"No API keys yet." in page.content
    response = client.post(url, {"name": "Accounting sync", "scopes": ["payments", "invoices"],
                                 "expires_in_days": "90"})
    assert response.status_code == 200 and response["Cache-Control"] == "no-store"
    secret = response.context["secret"]
    assert secret.encode() in response.content
    assert api(client, secret, "payments").status_code == 200
    again = client.get(url)
    assert secret.encode() not in again.content and b"Accounting sync" in again.content
    key = ApiKey.objects.get()
    assert client.post(reverse("api:revoke", args=[key.public_id])).status_code == 302
    assert api(client, secret, "payments").status_code == 401
    assert client.post(url, {"name": "", "scopes": []}).context["form"].errors


def test_staff_without_organization_manage_cannot_see_keys(client, owner, prop):
    key, _secret = services.create_key(owner, name="sync", scopes=ALL)
    manager = add_member(owner.organization, "manager", properties=[prop])
    login(client, manager)
    assert client.get(reverse("api:keys")).status_code == 403
    assert client.post(reverse("api:revoke", args=[key.public_id])).status_code == 403
    assert b"api-keys" not in client.get(reverse("accounts:staff")).content
    other = make_org(name="Other")
    client.logout()
    login(client, other)
    assert client.post(reverse("api:revoke", args=[key.public_id])).status_code == 404
    assert ApiKey.objects.get(pk=key.pk).revoked_at is None
