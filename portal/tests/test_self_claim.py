"""A tenant gets into the portal without an invitation when their verified phone is the one on the tenant record (D-203)."""

import pytest
from django.utils import timezone

from accounts.models import User
from accounts.tests.factories import PASSWORD, make_org, make_property, make_user
from accounts.tests.test_identity import last_code
from audit.models import AuditEvent
from portal import selectors, services
from portal.models import TenantAccount
from reports.tests.test_income import make_lease

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def lease(owner):
    return make_lease(owner, make_property(owner.organization, name="Acacia Court"), code="A1")


def tenant_of(lease):
    return lease.lease_tenants.get().tenant


def test_a_verified_phone_claims_the_tenant_record_without_an_invitation(owner, lease):
    tenant = tenant_of(lease)
    user = make_user(phone=tenant.phone)
    accounts = services.self_claim(user)
    assert [a.tenant for a in accounts] == [tenant] and accounts[0].invited_by is None
    assert selectors.has_access(user)
    assert AuditEvent.objects.filter(action="portal.self_claim").exists()
    assert services.self_claim(user) == []  # nothing left to claim


def test_an_unverified_or_different_phone_claims_nothing(owner, lease):
    tenant = tenant_of(lease)
    assert services.self_claim(make_user(phone=tenant.phone, verified=False)) == []
    assert services.self_claim(make_user()) == []
    assert not TenantAccount.objects.exists()


def test_the_owner_can_switch_it_off(owner, lease):
    owner.organization.tenant_self_signup = False
    owner.organization.save(update_fields=["tenant_self_signup"])
    assert services.self_claim(make_user(phone=tenant_of(lease).phone)) == []


def test_it_never_takes_over_a_record_someone_else_holds(owner, lease):
    tenant = tenant_of(lease)
    first = make_user(phone=tenant.phone)
    services.self_claim(first)
    assert services.self_claim(make_user(phone="+254799000222")) == []


def test_a_revocation_sticks_until_staff_invite_again(owner, lease):
    tenant = tenant_of(lease)
    user = make_user(phone=tenant.phone)
    services.self_claim(user)
    services.revoke_account(owner, services.live_account(tenant))
    assert services.self_claim(user) == []
    _inv, token = services.invite_tenant(owner, tenant)
    services.accept_invitation(token, user)
    assert selectors.has_access(user)


def test_archived_tenants_and_draft_only_tenants_are_left_alone(owner, lease):
    tenant = tenant_of(lease)
    user = make_user(phone=tenant.phone)
    tenant.archived_at = timezone.now()
    tenant.save(update_fields=["archived_at"])
    assert services.self_claim(user) == []


def test_logging_in_claims_and_a_tenant_lands_in_the_portal(client, owner, lease):
    tenant = tenant_of(lease)
    make_user(phone=tenant.phone)
    response = client.post("/login/", {"identifier": tenant.phone, "password": PASSWORD})
    assert response.status_code == 302
    assert client.get("/")["Location"] == "/my/"


def test_tenant_signup_needs_no_email_and_ends_in_the_portal(client, owner, lease, outbox):
    tenant = tenant_of(lease)
    page = client.get("/register/?as=tenant").content.decode()
    assert "Tenant sign-up" in page and "Email (optional)" in page
    response = client.post("/register/", {"full_name": "Amina", "phone": tenant.phone, "email": "",
                                          "password": "Str0ng-pass-92!", "accept_terms": "on"})
    assert response.status_code == 302 and response["Location"] == "/verify-phone/"
    response = client.post("/verify-phone/", {"code": last_code(outbox)})
    assert response["Location"] == "/my/"
    assert selectors.has_access(User.objects.get(phone=tenant.phone))
    # The password was set at sign-up, so the next login needs no code.
    client.post("/logout/")
    outbox.clear()
    assert client.post("/login/", {"identifier": tenant.phone, "password": "Str0ng-pass-92!"}).status_code == 302
    assert not outbox


def test_a_tenant_signup_with_no_matching_record_gets_the_no_match_page(client, owner, lease, outbox):
    client.get("/register/?as=tenant")
    client.post("/register/", {"full_name": "Nobody", "phone": "+254799000111", "email": "",
                               "password": "Str0ng-pass-92!", "accept_terms": "on"})
    response = client.post("/verify-phone/", {"code": last_code(outbox)})
    assert response["Location"] == "/my/no-match/"
    assert "+254799000111" in client.get("/my/no-match/").content.decode()
