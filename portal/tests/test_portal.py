"""The tenant portal (D-055): invitations, claiming them, and that a tenant sees only their own data."""

import datetime
import re

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from accounts import identity
from accounts.tests.factories import add_member, make_org, make_property, make_user
from accounts.tests.test_identity import last_code
from audit.models import AuditEvent
from billing.models import Invoice
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


def test_invite_by_whatsapp_or_copied_link_sends_no_sms_and_shows_the_link_once(client, owner, a1, outbox):
    client.force_login(owner.user)
    tenant = tenant_of(a1)
    notifications.set_tenant_channel(owner, tenant, SMS, allowed=False)
    assert client.post(f"/tenants/{tenant.public_id}/portal/invite/", {"via": "sms"}).status_code == 302
    assert services.pending_invitation(tenant) is None and not outbox

    client.post(f"/tenants/{tenant.public_id}/portal/invite/", {"via": "whatsapp"})
    assert not outbox
    page = client.get(f"/tenants/{tenant.public_id}/").content.decode()
    match = re.search(r'value="([^"]*/my/invite/[^"]+)"', page)
    assert match and "https://wa.me/" in page and "Open WhatsApp" in page
    token = match.group(1).rsplit("/my/invite/", 1)[1].strip("/")
    assert services.get_invitation(token) == services.pending_invitation(tenant)
    assert 'id="portal-invite-url"' not in client.get(f"/tenants/{tenant.public_id}/").content.decode()

    client.post(f"/tenants/{tenant.public_id}/portal/invite/", {"via": "link"})
    page = client.get(f"/tenants/{tenant.public_id}/").content.decode()
    assert 'id="portal-invite-url"' in page and "Open WhatsApp" not in page


def test_the_tenant_page_card(client, owner, a1):
    client.force_login(owner.user)
    tenant = tenant_of(a1)
    page = client.get(f"/tenants/{tenant.public_id}/").content.decode()
    assert "Tenant portal" in page and f"/tenants/{tenant.public_id}/portal/invite/" in page
    services.invite_tenant(owner, tenant)
    page = client.get(f"/tenants/{tenant.public_id}/").content.decode()
    assert "Invited" in page and "Send SMS again" in page and "portal/cancel-invite/" in page
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


def test_the_payment_history_page(client, owner, a1, b7):
    pay(owner, a1, 5000, D(2024, 6, 3))
    client.force_login(join(owner, a1))
    url = f"/my/leases/{a1.public_id}/payments/"
    page = client.get(url)
    assert page["Cache-Control"] == "private, no-store" and "QAB12CD34E" in page.content.decode()
    assert "3 Jun 2024" in page.content.decode()
    only_2024 = client.get(url, {"year": 2024}).content.decode()
    assert "3 Jun 2024" in only_2024 and "QAB12CD34E" not in only_2024
    csv_file = client.get(url, {"download": "csv"})
    assert csv_file["Content-Type"].startswith("text/csv") and "QAB12CD34E" in csv_file.content.decode()
    assert client.get(f"/my/leases/{b7.public_id}/payments/").status_code == 404


def test_the_payments_page_search_pending_kinds_and_pay_button(client, owner, a1):
    from decimal import Decimal

    from payments.models import Payment

    client.force_login(join(owner, a1))
    url = f"/my/leases/{a1.public_id}/payments/"
    confirmed = a1.payments.get()
    pending = Payment.objects.create(organization=a1.organization, lease=a1, amount=Decimal("700"), method="MPESA",
                                     paid_at=D(2025, 2, 2), reference="QPENDING01")
    text = client.get(url).content.decode()
    assert "Being checked" in text and "QPENDING01" in text and "You owe" in text
    assert "QAB12CD34E" in text and "What it went towards" in text
    assert "QAB12CD34E" in client.get(url, {"q": "ab12cd"}).content.decode()
    miss = client.get(url, {"q": "nothing"}).content.decode()
    assert "QAB12CD34E" not in miss and "No payment matches" in miss
    assert selectors.pending_payments(a1) == [pending]
    kinds = dict(selectors.totals_by_kind(selectors.payment_history(a1)))
    assert sum(kinds.values()) == confirmed.amount


def test_a_tenant_reports_a_payment_to_the_office(client, owner, a1, b7):
    from portal.models import PortalMessage

    client.force_login(join(owner, a1))
    payment = a1.payments.get()
    response = client.post(f"/my/payments/{payment.public_id}/report/", {"note": "I paid 3,000 more"})
    assert response.status_code == 302 and response["Location"].endswith(f"/my/leases/{a1.public_id}/messages/")
    message = PortalMessage.objects.get(lease=a1)
    assert "I paid 3,000 more" in message.body and "QAB12CD34E" in message.body
    assert client.post(f"/my/payments/{b7.payments.get().public_id}/report/", {"note": "x"}).status_code == 404
    assert client.get(f"/my/payments/{payment.public_id}/report/").status_code == 405


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
    # A whole word, so a random CSRF token that happens to contain "B7" does not fail the test.
    assert not re.search(r"\bB7\b", page) and str(b7.public_id) not in page


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


def test_invoice_pdf_is_own_and_issued_only(client, owner, a1, b7):
    client.force_login(join(owner, a1))
    mine, theirs = a1.invoices.get(), b7.invoices.get()
    r = client.get(f"/my/invoices/{mine.public_id}/pdf/")
    assert r.status_code == 200 and r["Content-Type"] == "application/pdf" and r.content.startswith(b"%PDF-")
    assert client.get(f"/my/invoices/{theirs.public_id}/pdf/").status_code == 404
    Invoice.objects.filter(pk=mine.pk).update(status=Invoice.Status.DRAFT)
    assert client.get(f"/my/invoices/{mine.public_id}/pdf/").status_code == 404


# ---------------------------------------------------------------------------
# Notice to vacate (D-106)
# ---------------------------------------------------------------------------


def test_a_tenant_gives_notice_once_in_the_portal(client, owner, a1, b7):
    client.force_login(join(owner, a1))
    url = f"/my/leases/{a1.public_id}/notice/"
    assert "Give notice to vacate" in client.get(f"/my/leases/{a1.public_id}/").content.decode()
    today = timezone.localdate()
    too_soon = client.post(url, {"move_out_on": (today + datetime.timedelta(days=3)).isoformat(), "confirm": "on"})
    assert too_soon.status_code == 200 and "earliest move-out day" in too_soon.content.decode()
    day = a1.earliest_move_out(today)
    r = client.post(url, {"move_out_on": day.isoformat(), "reason": "New job", "confirm": "on"})
    assert r.status_code == 302
    notice = a1.notices.get()
    assert notice.via == "PORTAL" and notice.tenant == tenant_of(a1) and notice.move_out_on == day
    assert AuditEvent.objects.filter(action="lease.notice", object_id=str(a1.public_id)).exists()
    assert "To change it, call" in client.get(f"/my/leases/{a1.public_id}/").content.decode()
    with pytest.raises(ValidationError):
        lease_services.portal_give_notice(r.wsgi_request.user, a1, move_out_on=day)
    # Not someone else's lease.
    assert client.get(f"/my/leases/{b7.public_id}/notice/").status_code == 404


def test_the_lease_page_uses_the_organizations_own_words(client, owner, a1):
    client.force_login(join(owner, a1))
    page = client.get(f"/my/leases/{a1.public_id}/").content.decode()
    assert "Your lease" in page

    org = owner.organization
    org.org_type = org.Type.ESTATE
    org.save(update_fields=["org_type"])
    page = client.get(f"/my/leases/{a1.public_id}/").content.decode()
    assert "Your membership" in page and "Your lease" not in page


def test_billers_that_are_not_landlords_have_no_deposit_or_notice_to_vacate(client, owner, a1):
    client.force_login(join(owner, a1))
    page = client.get(f"/my/leases/{a1.public_id}/").content.decode()
    assert "Give notice to vacate" in page and "Deposit" in page
    assert client.get(f"/my/leases/{a1.public_id}/notice/").status_code == 200

    org = owner.organization
    org.org_type = org.Type.WATER
    org.save(update_fields=["org_type"])
    page = client.get(f"/my/leases/{a1.public_id}/").content.decode()
    assert "Give notice to vacate" not in page and "Moving out" not in page
    assert "Deposit" not in page and "Notice to leave" not in page
    assert client.get(f"/my/leases/{a1.public_id}/notice/").status_code == 404
    assert client.post(f"/my/leases/{a1.public_id}/notice/", {"move_out_on": "2030-01-01"}).status_code == 404


def test_the_platform_owner_can_view_a_tenant_portal_read_only(client, owner, a1):
    from django.urls import reverse

    from console import viewas
    from console.tests.test_console import owner as console_owner

    join(owner, a1)
    account = TenantAccount.objects.get()
    console_owner(client)
    org = owner.organization
    r = client.post(reverse("console:view_as", args=[org.public_id]), {"account": str(account.public_id)})
    assert r.status_code == 302 and r.url == reverse("portal:home")
    page = client.get(reverse("portal:home"))
    assert page.status_code == 200 and b"You are viewing as" in page.content
    assert client.post(reverse("portal:home")).status_code in (302, 403)
    assert AuditEvent.objects.filter(action="console.view_as", organization=org).count() == 1
    client.post(reverse("console:view_as_stop"))
    assert viewas.TENANT_KEY not in client.session
    assert AuditEvent.objects.filter(action="console.view_as", organization=org).count() == 2


def test_the_platform_owner_can_view_as_any_staff_seat(client, owner):
    from django.urls import reverse

    from console import viewas
    from console.tests.test_console import owner as console_owner

    seat = add_member(owner.organization, "accountant")
    console_owner(client)
    r = client.post(reverse("console:view_as", args=[owner.organization.public_id]),
                    {"membership": str(seat.public_id)})
    assert r.status_code == 302 and client.session[viewas.MEMBER_KEY] == str(seat.public_id)
    page = client.get(reverse("accounts:home"))
    assert page.status_code in (200, 302)
    assert client.post(reverse("accounts:home")).status_code in (302, 403)
    bad = client.post(reverse("console:view_as", args=[owner.organization.public_id]),
                      {"membership": "00000000-0000-0000-0000-000000000000"})
    assert bad.status_code == 302
