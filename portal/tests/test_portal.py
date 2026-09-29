"""The tenant portal (D-055): invitations, claiming them, and that a tenant sees only their own data."""

import datetime

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from accounts import identity
from accounts.tests.factories import add_member, make_org, make_property, make_user
from accounts.tests.test_identity import last_code
from audit.models import AuditEvent
from leases import services as lease_services
from notifications import services as notifications
from notifications.catalog import SMS
from payments import services as payment_services
from portal import selectors, services
from portal.models import PortalInvitation, TenantAccount
from properties import services as property_services
from reports.tests.test_income import bill, make_lease, pay

pytestmark = pytest.mark.django_db

D = datetime.date
JAN = D(2025, 1, 1)


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def acacia(owner):
    return make_property(owner.organization, name="Acacia Court")


@pytest.fixture
def a1(owner, acacia):
    """Tenant A1 on +254712000114, billed for January and paid 20,000 with reference QAB12CD34E."""
    lease = make_lease(owner, acacia, code="A1")
    bill(lease, JAN)
    payment = pay(owner, lease, 20000, D(2025, 1, 6))
    payment.reference = "QAB12CD34E"
    payment.save(update_fields=["reference"])
    return lease


@pytest.fixture
def b7(owner, acacia):
    """A neighbour in the same organization and property."""
    lease = make_lease(owner, acacia, code="B7")
    bill(lease, JAN)
    pay(owner, lease, 5000, D(2025, 1, 7))
    return lease


def tenant_of(lease):
    return lease.lease_tenants.get().tenant


def join(owner, lease, user=None):
    """Invites the lease's tenant and accepts as a verified user on the tenant's phone."""
    tenant = tenant_of(lease)
    _invitation, token = services.invite_tenant(owner, tenant)
    user = user or make_user(phone=tenant.phone)
    services.accept_invitation(token, user)
    return user


# ---------------------------------------------------------------------------
# Inviting
# ---------------------------------------------------------------------------


def test_an_invitation_is_hashed_audited_and_replaces_the_pending_one(owner, a1):
    tenant = tenant_of(a1)
    first, token = services.invite_tenant(owner, tenant)
    assert first.token_hash != token and len(token) > 30
    assert first.phone == tenant.phone and first.expires_at > timezone.now() + datetime.timedelta(days=6)
    second, _token = services.invite_tenant(owner, tenant)
    first.refresh_from_db()
    assert first.revoked_at is not None and second.is_pending
    assert services.pending_invitation(tenant) == second
    assert AuditEvent.objects.filter(action="portal.invite").count() == 2


def test_who_may_invite(owner, a1, acacia):
    tenant = tenant_of(a1)
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    with pytest.raises(PermissionDenied):
        services.invite_tenant(caretaker, tenant)
    with pytest.raises(PermissionDenied):
        services.invite_tenant(make_org(), tenant)
    elsewhere = make_property(owner.organization, name="Elsewhere")
    manager = add_member(owner.organization, "manager", properties=[elsewhere])
    with pytest.raises(PermissionDenied):
        services.invite_tenant(manager, tenant)
    services.invite_tenant(add_member(owner.organization, "manager", properties=[acacia]), tenant)


def test_tenants_who_cannot_be_invited(owner, a1, acacia):
    tenant = tenant_of(a1)
    notifications.set_tenant_channel(owner, tenant, SMS, allowed=False)
    with pytest.raises(ValidationError, match="SMS"):
        services.invite_tenant(owner, tenant)
    notifications.set_tenant_channel(owner, tenant, SMS, allowed=True)
    join(owner, a1)
    with pytest.raises(ValidationError, match="already"):
        services.invite_tenant(owner, tenant)
    # On a draft lease only.
    unit = property_services.create_unit(owner, acacia, code="D1")
    from tenants import services as tenant_services

    newcomer = tenant_services.create_tenant(owner, name="Newcomer", phone="0733000001")
    lease_services.create_lease(owner, unit=unit, tenants=[newcomer], start_date=JAN, end_date=None, rent=1000,
                                due_day=5, grace_days=3)
    with pytest.raises(ValidationError, match="lease"):
        services.invite_tenant(owner, newcomer)


def test_the_invitation_sms(client, owner, a1, outbox):
    client.force_login(owner.user)
    tenant = tenant_of(a1)
    response = client.post(f"/tenants/{tenant.public_id}/portal/invite/")
    assert response.status_code == 302
    to, body = outbox[-1]
    assert to == tenant.phone and "/my/invite/" in body and owner.organization.name in body
    token = body.rsplit("/my/invite/", 1)[1].strip("/")
    assert services.get_invitation(token) == services.pending_invitation(tenant)


def test_the_tenant_page_card(client, owner, a1):
    client.force_login(owner.user)
    tenant = tenant_of(a1)
    page = client.get(f"/tenants/{tenant.public_id}/").content.decode()
    assert "Tenant portal" in page and f"/tenants/{tenant.public_id}/portal/invite/" in page
    services.invite_tenant(owner, tenant)
    page = client.get(f"/tenants/{tenant.public_id}/").content.decode()
    assert "Invited" in page and "Send again" in page and "portal/cancel-invite/" in page
    client.post(f"/tenants/{tenant.public_id}/portal/cancel-invite/")
    assert services.pending_invitation(tenant) is None
    join(owner, a1)
    page = client.get(f"/tenants/{tenant.public_id}/").content.decode()
    assert "Remove access" in page
    client.post(f"/tenants/{tenant.public_id}/portal/remove/")
    assert services.live_account(tenant) is None
    assert AuditEvent.objects.filter(action="portal.revoke").exists()
    viewer = add_member(owner.organization, "caretaker", all_properties=True)
    client.force_login(viewer.user)
    assert "Tenant portal" not in client.get(f"/tenants/{tenant.public_id}/").content.decode()
    assert client.post(f"/tenants/{tenant.public_id}/portal/invite/").status_code == 403


# ---------------------------------------------------------------------------
# Accepting
# ---------------------------------------------------------------------------


def test_accepting_needs_the_invited_verified_phone(owner, a1):
    tenant = tenant_of(a1)
    _inv, token = services.invite_tenant(owner, tenant)
    with pytest.raises(PermissionDenied):
        services.accept_invitation(token, make_user())
    user = make_user(phone=tenant.phone, verified=False)
    with pytest.raises(PermissionDenied):
        services.accept_invitation(token, user)
    user.phone_verified_at = timezone.now()
    user.save(update_fields=["phone_verified_at"])
    account = services.accept_invitation(token, user)
    assert account.tenant == tenant and account.organization == owner.organization
    assert AuditEvent.objects.filter(action="portal.accept").exists()
    with pytest.raises(ValidationError):  # used once
        services.accept_invitation(token, account.user)


def test_expired_revoked_or_changed_phone_invitations_do_not_work(owner, a1):
    tenant = tenant_of(a1)
    user = make_user(phone=tenant.phone)
    inv, token = services.invite_tenant(owner, tenant)
    PortalInvitation.objects.filter(pk=inv.pk).update(expires_at=timezone.now() - datetime.timedelta(seconds=1))
    with pytest.raises(ValidationError):
        services.accept_invitation(token, user)
    inv, token = services.invite_tenant(owner, tenant)
    services.revoke_invitation(owner, inv)
    with pytest.raises(ValidationError):
        services.accept_invitation(token, user)
    _inv, token = services.invite_tenant(owner, tenant)
    tenant.phone = "+254733999999"
    tenant.save(update_fields=["phone"])
    with pytest.raises(PermissionDenied):
        services.accept_invitation(token, user)
    with pytest.raises(ValidationError):
        services.accept_invitation("not-a-token", user)
    assert not TenantAccount.objects.exists()


def test_the_accept_page_flow(client, owner, a1):
    tenant = tenant_of(a1)
    _inv, token = services.invite_tenant(owner, tenant)
    page = client.get(f"/my/invite/{token}/")
    assert page.status_code == 200 and owner.organization.name in page.content.decode()
    assert client.session["portal_invite_token"] == token
    assert client.post(f"/my/invite/{token}/")["Location"].startswith("/login/?next=")
    user = make_user(phone=tenant.phone)
    client.force_login(user)
    response = client.post(f"/my/invite/{token}/")
    assert response.status_code == 302 and response["Location"] == "/my/"
    assert "portal_invite_token" not in client.session
    assert client.get("/my/invite/bogus/").status_code == 404


def test_phone_verification_resumes_the_portal_invitation(client, owner, a1, outbox):
    tenant = tenant_of(a1)
    _inv, token = services.invite_tenant(owner, tenant)
    client.get(f"/my/invite/{token}/")
    user = make_user(phone=tenant.phone, verified=False)
    client.force_login(user)
    identity.send_phone_verification(user)
    response = client.post("/verify-phone/", {"code": last_code(outbox)})
    assert response["Location"] == f"/my/invite/{token}/"


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def test_a_tenant_only_user_lands_in_the_portal(client, owner, a1):
    user = join(owner, a1)
    client.force_login(user)
    assert client.get("/")["Location"] == "/my/"
    page = client.get("/my/").content.decode()
    assert 'href="/my/"' in page
    assert 'href="/tenants/"' not in page and 'role="combobox"' not in page  # no staff menu, no search box


def test_a_member_who_is_also_a_tenant_gets_a_link(client, owner, a1):
    tenant = tenant_of(a1)
    other = make_org(owner=make_user(phone=tenant.phone))
    join(owner, a1, user=other.user)
    client.force_login(other.user)
    page = client.get("/").content.decode()
    assert "My home" in page and 'href="/my/"' in page


def test_staff_pages_are_closed_to_a_tenant(client, owner, a1):
    client.force_login(join(owner, a1))
    for url in ("/tenants/", f"/leases/{a1.public_id}/", "/billing/invoices/", "/payments/", "/search/?q=tenant",
                "/reports/"):
        response = client.get(url)
        assert response.status_code in (302, 403, 404), url
        if response.status_code == 302:
            assert response["Location"] == "/my/", url


# ---------------------------------------------------------------------------
# What the tenant sees
# ---------------------------------------------------------------------------


def test_the_home_and_lease_pages(client, owner, a1):
    client.force_login(join(owner, a1))
    home = client.get("/my/")
    assert home["Cache-Control"] == "private, no-store"
    page = home.content.decode()
    assert "Acacia Court · A1" in page and f"/my/leases/{a1.public_id}/" in page
    page = client.get(f"/my/leases/{a1.public_id}/").content.decode()
    assert "QAB12CD34E" in page and "Brought forward" in page and "Payments and receipts" in page
    payment = a1.payments.get()
    assert f"/my/receipts/{payment.public_id}/" in page
    receipt = client.get(f"/my/receipts/{payment.public_id}/")
    assert receipt.status_code == 200 and receipt["Content-Type"] == "application/pdf"


def test_the_summary(owner, a1):
    bill(a1, D(2025, 2, 1))
    summary = selectors.summary(a1)
    assert summary.balance == 20000 and summary.next_due.period_start == D(2025, 2, 1)
    assert summary.overdue == 20000  # February's grace ended long ago
    statement = selectors.statement(a1, today=D(2025, 3, 15))
    assert statement["start"] == D(2024, 4, 1) and statement["closing"] == 20000


def test_no_neighbour_data(client, owner, a1, b7):
    client.force_login(join(owner, a1))
    assert client.get(f"/my/leases/{b7.public_id}/").status_code == 404
    assert client.get(f"/my/receipts/{b7.payments.get().public_id}/").status_code == 404
    page = client.get("/my/").content.decode()
    assert "B7" not in page and "Tenant B7" not in page


def test_no_data_from_another_organization(client, owner, a1):
    other = make_org()
    other_lease = make_lease(other, make_property(other.organization), code="A1")  # same tenant phone
    other_payment = pay(other, other_lease, 1000, D(2025, 1, 6))
    client.force_login(join(owner, a1))
    assert client.get(f"/my/leases/{other_lease.public_id}/").status_code == 404
    assert client.get(f"/my/receipts/{other_payment.public_id}/").status_code == 404
    assert list(selectors.own_leases(TenantAccount.objects.get().user)) == [a1]


def test_access_ends_when_revoked_or_archived(client, owner, a1):
    user = join(owner, a1)
    client.force_login(user)
    assert client.get("/my/").status_code == 200
    tenant = tenant_of(a1)
    tenant.archived_at = timezone.now()
    tenant.save(update_fields=["archived_at"])
    assert client.get("/my/").status_code == 404
    assert client.get(f"/my/leases/{a1.public_id}/").status_code == 404
    tenant.archived_at = None
    tenant.save(update_fields=["archived_at"])
    services.revoke_account(owner, services.live_account(tenant))
    assert client.get("/my/").status_code == 404
    assert client.get("/")["Location"] == "/welcome/"


def test_draft_leases_are_not_shown(owner, a1, acacia):
    user = join(owner, a1)
    unit = property_services.create_unit(owner, acacia, code="D2")
    draft = lease_services.create_lease(owner, unit=unit, tenants=[tenant_of(a1)], start_date=JAN, end_date=None,
                                        rent=1000, due_day=5, grace_days=3)
    assert selectors.own_lease(user, draft.public_id) is None
    assert selectors.own_lease_ids(user) == [a1.pk]


def test_a_reversed_payment_has_no_receipt(client, owner, a1):
    client.force_login(join(owner, a1))
    payment = a1.payments.get()
    payment_services.reverse_payment(owner, payment, reason="Bounced")
    assert client.get(f"/my/receipts/{payment.public_id}/").status_code == 404
    assert "QAB12CD34E" not in "".join(p.reference for p in selectors.payments(a1))


def test_staff_notes_are_not_shown(client, owner, a1):
    payment = a1.payments.get()
    payment_services.reverse_payment(owner, payment, reason="Secret staff note about the cheque")
    client.force_login(join(owner, a1))
    page = client.get(f"/my/leases/{a1.public_id}/").content.decode()
    assert "Secret staff note" not in page and "Payment reversed" in page


def test_portal_pages_need_a_login_and_access(client, owner, a1):
    assert client.get("/my/").status_code == 302
    client.force_login(make_user())
    assert client.get("/my/").status_code == 404
    client.force_login(owner.user)
    assert client.get("/my/").status_code == 404
