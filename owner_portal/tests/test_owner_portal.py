"""The owner portal (D-128): invitations, claiming them, and that an owner sees only their own figures."""

import datetime
from decimal import Decimal

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from accounts import services as account_services
from accounts.tests.factories import add_member, fresh, make_org, make_property, make_user
from audit.models import AuditEvent
from owner_portal import selectors, services
from owner_portal.models import OwnerAccount
from properties import services as property_services
from reports import owners, statements
from reports.models import OwnerRemittance
from reports.tests.test_income import bill, make_lease, pay

pytestmark = pytest.mark.django_db

D = datetime.date
JAN = D(2025, 1, 1)


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def jane(owner):
    return property_services.create_owner(owner, name="Jane Wanjiru", phone="+254712000111",
                                          email="jane@example.com")


@pytest.fixture
def prop(owner, jane):
    prop = make_property(owner.organization, name="Acacia Court")
    return property_services.update_property(owner, prop, owner=jane, management_fee_percent=Decimal("10"))


@pytest.fixture
def january(owner, prop):
    """Tenant A1 paid 20,000 in January: 18,000 due to Jane after the 10% fee."""
    lease = make_lease(owner, prop, code="A1")
    bill(lease, JAN)
    pay(owner, lease, 20000, D(2025, 1, 10))
    return lease


@pytest.fixture
def tom(owner):
    """Another owner in the same organization, with his own property and tenant."""
    tom = property_services.create_owner(owner, name="Tom Otieno", phone="+254712000222")
    baobab = make_property(owner.organization, name="Baobab Flats")
    property_services.update_property(owner, baobab, owner=tom)
    lease = make_lease(owner, baobab, code="B1", rent=50000)
    bill(lease, JAN)
    pay(owner, lease, 50000, D(2025, 1, 5))
    return tom


def join(owner, jane, user=None):
    """Invites Jane and accepts as a verified user on her phone."""
    _invitation, token = services.invite_owner(owner, jane)
    user = user or make_user(phone=jane.phone)
    services.accept_invitation(token, user)
    return user


# ---------------------------------------------------------------------------
# Inviting
# ---------------------------------------------------------------------------


def test_an_invitation_is_hashed_audited_and_replaces_the_pending_one(owner, jane, prop):
    first, token = services.invite_owner(owner, jane)
    assert first.token_hash != token and len(token) > 30
    assert first.phone == jane.phone and first.email == jane.email
    assert first.expires_at > timezone.now() + datetime.timedelta(days=6)
    second, _token = services.invite_owner(owner, jane)
    first.refresh_from_db()
    assert first.revoked_at is not None and second.is_pending
    assert services.pending_invitation(jane) == second
    assert AuditEvent.objects.filter(action="owner_portal.invite").count() == 2


def test_who_may_invite(owner, jane, prop):
    viewer = add_member(owner.organization, "viewer", all_properties=True)
    with pytest.raises(PermissionDenied):
        services.invite_owner(viewer, jane)
    # A manager who sees only one of Jane's two properties cannot open all of them to her.
    other = make_property(owner.organization, name="Cedar Court")
    property_services.update_property(owner, other, owner=jane)
    manager = add_member(owner.organization, "manager")
    account_services.set_property_scope(owner, manager, all_properties=False, properties=[prop])
    with pytest.raises(PermissionDenied):
        services.invite_owner(fresh(manager), jane)
    account_services.set_property_scope(owner, manager, all_properties=True)
    assert services.invite_owner(fresh(manager), jane)[0].is_pending


def test_an_owner_needs_a_phone_and_a_property(owner):
    nophone = property_services.create_owner(owner, name="No Phone", email="np@example.com")
    with pytest.raises(ValidationError):
        services.invite_owner(owner, nophone)
    noprops = property_services.create_owner(owner, name="No Props", phone="+254712000333")
    with pytest.raises(ValidationError):
        services.invite_owner(owner, noprops)


def test_the_invitation_goes_by_sms_and_email(client, owner, jane, prop, outbox):
    client.force_login(owner.user)
    response = client.post(f"/owner/owners/{jane.public_id}/invite/")
    assert response["Location"] == f"/properties/owners/{jane.public_id}/"
    to, body = outbox[-1]
    assert to == jane.phone and "/owner/invite/" in body and owner.organization.name in body
    token = body.rsplit("/owner/invite/", 1)[1].strip("/")
    invitation = services.get_invitation(token)
    assert invitation == services.pending_invitation(jane)
    assert invitation.sent_by_sms and invitation.sent_by_email
    assert mail.outbox[-1].to == ["jane@example.com"] and f"/owner/invite/{token}/" in mail.outbox[-1].body


def test_the_owner_page_card(client, owner, jane, prop):
    client.force_login(owner.user)
    url = f"/properties/owners/{jane.public_id}/"
    assert "Invite by SMS and email" in client.get(url).content.decode()
    client.post(f"/owner/owners/{jane.public_id}/invite/")
    assert "Cancel invitation" in client.get(url).content.decode()
    client.post(f"/owner/owners/{jane.public_id}/invite/cancel/")
    assert services.pending_invitation(jane) is None
    join(owner, jane)
    assert "Remove access" in client.get(url).content.decode()
    client.post(f"/owner/owners/{jane.public_id}/revoke/")
    assert services.live_account(jane) is None
    assert AuditEvent.objects.filter(action="owner_portal.revoke").exists()

    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    client.force_login(caretaker.user)
    assert client.post(f"/owner/owners/{jane.public_id}/invite/").status_code == 403


# ---------------------------------------------------------------------------
# Claiming
# ---------------------------------------------------------------------------


def test_accepting_needs_the_invited_verified_phone(owner, jane, prop):
    _inv, token = services.invite_owner(owner, jane)
    with pytest.raises(PermissionDenied):
        services.accept_invitation(token, make_user())
    user = make_user(phone=jane.phone, verified=False)
    with pytest.raises(PermissionDenied):
        services.accept_invitation(token, user)
    user.phone_verified_at = timezone.now()
    user.save(update_fields=["phone_verified_at"])
    account = services.accept_invitation(token, user)
    assert (account.owner, account.user, account.organization) == (jane, user, owner.organization)
    assert AuditEvent.objects.filter(action="owner_portal.accept").exists()
    with pytest.raises(ValidationError):  # used once
        services.accept_invitation(token, user)
    with pytest.raises(ValidationError):
        services.accept_invitation("not-a-token", user)


def test_an_expired_or_cancelled_invitation_fails(owner, jane, prop):
    invitation, token = services.invite_owner(owner, jane)
    invitation.expires_at = timezone.now() - datetime.timedelta(minutes=1)
    invitation.save(update_fields=["expires_at"])
    user = make_user(phone=jane.phone)
    with pytest.raises(ValidationError):
        services.accept_invitation(token, user)
    invitation, token = services.invite_owner(owner, jane)
    services.revoke_invitation(owner, invitation)
    with pytest.raises(ValidationError):
        services.accept_invitation(token, user)


def test_the_accept_page_flow(client, owner, jane, prop):
    _inv, token = services.invite_owner(owner, jane)
    page = client.get(f"/owner/invite/{token}/")
    assert page.status_code == 200 and owner.organization.name in page.content.decode()
    assert client.session["owner_invite_token"] == token
    assert client.post(f"/owner/invite/{token}/")["Location"].startswith("/login/?next=")
    client.force_login(make_user(phone=jane.phone))
    response = client.post(f"/owner/invite/{token}/")
    account = OwnerAccount.objects.get(owner=jane)
    assert response["Location"] == f"/owner/accounts/{account.public_id}/"
    assert "owner_invite_token" not in client.session
    assert client.get("/owner/invite/bogus/").status_code == 404


def test_an_owner_only_user_lands_in_the_owner_portal(client, owner, jane, prop):
    client.force_login(join(owner, jane))
    assert client.get("/")["Location"] == "/owner/"
    response = client.get("/owner/")
    account = OwnerAccount.objects.get(owner=jane)
    assert response["Location"] == f"/owner/accounts/{account.public_id}/"  # one account: straight in
    page = client.get(response["Location"]).content.decode()
    assert 'href="/owner/"' in page and 'href="/tenants/"' not in page


def test_staff_pages_are_closed_to_an_owner(client, owner, jane, prop):
    client.force_login(join(owner, jane))
    for url in ("/tenants/", "/properties/", "/payments/", "/reports/", "/reports/owner-statement/"):
        response = client.get(url)
        assert response.status_code in (302, 403, 404), url
        if response.status_code == 302:
            assert response["Location"] == "/owner/", url


# ---------------------------------------------------------------------------
# What the owner sees
# ---------------------------------------------------------------------------


def test_the_account_page(client, owner, jane, january):
    owners.record_remittance(owner, jane, month=JAN, amount=Decimal("10000"), paid_on=D(2025, 2, 5),
                             method=OwnerRemittance.Method.MPESA, reference="QK12AB")
    voided = owners.record_remittance(owner, jane, month=JAN, amount=Decimal("999"), paid_on=D(2025, 2, 6),
                                      method=OwnerRemittance.Method.CASH, reference="WRONG")
    owners.void_remittance(owner, voided, reason="Typo")
    send = owners.send_statement(owner, statements.statement(owner, str(jane.public_id), JAN))
    client.force_login(join(owner, jane))
    account = OwnerAccount.objects.get(owner=jane)
    page = client.get(f"/owner/accounts/{account.public_id}/")
    assert page.status_code == 200 and page["Cache-Control"] == "private, no-store"
    text = page.content.decode()
    assert "QK12AB" in text and "WRONG" not in text  # voided payments are left out
    assert send.number in text and "Acacia Court" in text
    pdf = client.get(f"/owner/statements/{send.public_id}/pdf/")
    assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf"


def test_the_statement_hides_tenant_names(client, owner, jane, january):
    client.force_login(join(owner, jane))
    account = OwnerAccount.objects.get(owner=jane)
    page = client.get(f"/owner/accounts/{account.public_id}/statement/", {"month": "2025-01"})
    text = page.content.decode()
    assert page.status_code == 200 and "18,000.00" in text
    assert "Tenant A1" not in text and "privacy" in text
    pdf = client.get(f"/owner/accounts/{account.public_id}/statement/", {"month": "2025-01", "format": "pdf"})
    assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf"


def test_the_rent_payments_page(client, owner, jane, january, tom):
    client.force_login(join(owner, jane))
    account = OwnerAccount.objects.get(owner=jane)
    url = f"/owner/accounts/{account.public_id}/payments/"
    page = client.get(url)
    text = page.content.decode()
    assert page.status_code == 200 and page["Cache-Control"] == "private, no-store"
    assert "10 Jan 2025" in text and "Acacia Court" in text and "A1" in text and "20,000.00" in text
    assert "Baobab Flats" not in text and "50,000.00" not in text  # another owner's property
    assert "Tenant A1" not in text
    assert "10 Jan 2025" in client.get(url, {"year": 2025}).content.decode()
    assert "10 Jan 2025" not in client.get(url, {"q": "NOSUCHCODE"}).content.decode()
    csv_file = client.get(url, {"download": "csv"})
    assert csv_file["Content-Type"].startswith("text/csv") and "Acacia Court" in csv_file.content.decode()
    other = OwnerAccount.objects.filter(owner=tom).first()
    assert other is None or client.get(f"/owner/accounts/{other.public_id}/payments/").status_code == 404


def test_the_statement_matches_what_staff_see(owner, jane, january):
    staff = statements.statement(owner, str(jane.public_id), JAN)
    own = statements.for_owner(jane, JAN)
    assert (own.collected, own.fee, own.due) == (staff.collected, staff.fee, staff.due)
    assert own.show_tenants is False


def test_an_owner_sees_only_their_own(client, owner, jane, january, tom):
    tom_user = make_user(phone=tom.phone)
    _inv, token = services.invite_owner(owner, tom)
    tom_account = services.accept_invitation(token, tom_user)
    tom_send = owners.send_statement(owner, statements.statement(owner, str(tom.public_id), JAN), sms=True)

    client.force_login(join(owner, jane))
    for url in (f"/owner/accounts/{tom_account.public_id}/",
                f"/owner/accounts/{tom_account.public_id}/statement/",
                f"/owner/statements/{tom_send.public_id}/pdf/"):
        assert client.get(url).status_code == 404, url
    jane_account = OwnerAccount.objects.get(owner=jane)
    text = client.get(f"/owner/accounts/{jane_account.public_id}/").content.decode()
    assert "Baobab Flats" not in text


def test_no_access_is_a_404(client, owner, jane, prop):
    client.force_login(make_user())
    assert client.get("/owner/").status_code == 404
    user = join(owner, jane)
    services.revoke_account(owner, services.live_account(jane))
    client.force_login(user)
    assert client.get("/owner/").status_code == 404
    assert not selectors.has_access(user)


def test_an_archived_organization_closes_the_portal(owner, jane, prop):
    user = join(owner, jane)
    owner.organization.archived_at = timezone.now()
    owner.organization.save(update_fields=["archived_at"])
    assert not selectors.has_access(user)


def test_one_login_for_two_organizations(client, owner, jane, prop):
    other = make_org()
    jane2 = property_services.create_owner(other, name="Jane Wanjiru", phone=jane.phone)
    property_services.update_property(other, make_property(other.organization, name="Elgon View"), owner=jane2)
    user = join(owner, jane)
    _inv, token = services.invite_owner(other, jane2)
    services.accept_invitation(token, user)
    client.force_login(user)
    page = client.get("/owner/")
    assert page.status_code == 200
    text = page.content.decode()
    assert owner.organization.name in text and other.organization.name in text


# ---------------------------------------------------------------------------
# Today's picture (D-217)
# ---------------------------------------------------------------------------


def test_the_live_page_shows_owed_rent_and_empty_units_but_no_tenant_names(client, owner, jane, prop, january, tom):
    from django.urls import reverse

    from leases.models import Lease
    from owner_portal import live

    user = join(owner, jane)
    account = selectors.live_accounts(user).get()
    # A1 was billed in January and is paid; February is billed and unpaid.
    bill(Lease.objects.get(unit__code="A1"), D(2025, 2, 1))
    data = live.live(account, today=D(2025, 4, 1))
    assert data["owed"] == Decimal("20000.00") and data["owing_leases"] == 1
    # Tom's property is never in Jane's picture.
    assert [r.property.name for r in data["rows"]] == ["Acacia Court"]
    client.force_login(user)
    response = client.get(reverse("owner_portal:live", args=[account.public_id]))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Acacia Court" in body and "Baobab" not in body
    assert response["Cache-Control"] == "private, no-store"


def test_every_page_the_owner_portal_links_to_opens(client, owner, jane, prop, january):
    """A crawl from /owner/ as an invited owner with a month of figures: no link ends in an error (audit 2026-10-24)."""
    import re

    _invitation, token = services.invite_owner(owner, jane)
    user = make_user(phone=jane.phone)
    services.accept_invitation(token, user)
    client.force_login(user)
    seen, queue, bad = set(), ["/owner/"], []
    while queue and len(seen) < 60:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        page = client.get(url)
        if page.status_code >= 400:
            bad.append((url, page.status_code))
        if page.status_code == 302 and page.url.startswith("/owner/"):
            queue.append(page.url)
        if page.status_code != 200 or "html" not in page["Content-Type"]:
            continue
        for href in re.findall(r'href="(/owner/[^"#?]*)', page.content.decode()):
            if href not in seen and "/invite/" not in href:
                queue.append(href)
    assert len(seen) > 3 and not bad, (len(seen), bad)
