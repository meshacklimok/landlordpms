"""Every page renders, and pages are gated by capability, not by role name."""

import pytest
from django.urls import reverse

from accounts import services
from accounts.models import Membership, Organization, Role

from .factories import PASSWORD, add_member, make_org, make_user, role
from .test_isolation import login

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner(client):
    m = make_org()
    login(client, m)
    return m


@pytest.mark.parametrize("name", ["accounts:login", "accounts:register", "accounts:password_reset"])
def test_public_pages_render(client, name):
    assert client.get(reverse(name)).status_code == 200


def test_visitors_see_the_front_page_at_home(client):
    assert client.get(reverse("accounts:home")).status_code == 200


def test_app_pages_redirect_anonymous_to_login(client):
    response = client.get(reverse("accounts:staff"))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("accounts:login"))


def test_owner_pages_render(client, owner):
    member = add_member(owner.organization, "caretaker")
    r = role(owner.organization, "caretaker")
    for url in [
        reverse("accounts:home"),
        reverse("accounts:staff"),
        reverse("accounts:invite"),
        reverse("accounts:member", args=[member.public_id]),
        reverse("accounts:roles"),
        reverse("accounts:role_create"),
        reverse("accounts:role_edit", args=[r.public_id]),
        reverse("accounts:role_edit", args=[owner.role.public_id]),
    ]:
        assert client.get(url).status_code == 200, url


@pytest.mark.parametrize("url_name", ["accounts:staff", "accounts:invite", "accounts:roles", "accounts:role_create"])
def test_caretaker_is_forbidden_from_admin_pages(client, url_name):
    org = make_org().organization
    login(client, add_member(org, "caretaker"))
    assert client.get(reverse(url_name)).status_code == 403


def test_onboarding_needs_an_email(client):
    user = make_user()
    client.login(username=user.phone, password=PASSWORD)
    response = client.post(reverse("accounts:onboarding"), {"name": "Kamau Estates", "org_type": "COMPANY"})
    assert response.url == reverse("accounts:email")
    assert not Membership.objects.filter(user=user).exists()


def test_onboarding_creates_org(client):
    user = make_user(email="kamau@example.com")
    client.login(username=user.phone, password=PASSWORD)
    response = client.post(reverse("accounts:onboarding"), {"name": "Kamau Estates", "org_type": "COMPANY"})
    assert response.status_code == 302
    m = Membership.objects.get(user=user)
    assert m.organization.name == "Kamau Estates"
    assert m.role.is_owner_role


def test_create_role_via_form(client, owner):
    response = client.post(reverse("accounts:role_create"), {
        "name": "Night guard", "description": "", "capabilities": ["properties.view", "units.view"],
    })
    assert response.status_code == 302
    r = Role.objects.get(organization=owner.organization, name="Night guard")
    assert services.role_codenames(r) == {"properties.view", "units.view"}


def test_owner_role_form_keeps_locked_capabilities(client, owner):
    # The template posts locked capabilities as hidden inputs, so saving the Owner role unchanged works.
    caps = sorted(services.role_codenames(owner.role))
    response = client.post(reverse("accounts:role_edit", args=[owner.role.public_id]),
                           {"name": owner.role.name, "description": "", "capabilities": caps})
    assert response.status_code == 302


def test_invite_via_form_sends_sms(client, owner, outbox):
    response = client.post(reverse("accounts:invite"), {
        "full_name": "Otieno", "phone": "0799000111", "role": role(owner.organization, "caretaker").pk,
        "all_properties": "on",
    })
    assert response.status_code == 302, response.content.decode()
    assert outbox[-1][0] == "+254799000111"
    assert "/invite/" in outbox[-1][1]


def test_member_override_via_form(client, owner):
    m = add_member(owner.organization, "caretaker")
    client.post(reverse("accounts:member", args=[m.public_id]),
                {"action": "override", "capability": "tenants.manage", "state": "grant"})
    assert m.overrides.get().granted is True


def test_frozen_org_blocks_everything(client, owner):
    Organization.objects.filter(pk=owner.organization.pk).update(status=Organization.Status.FROZEN)
    assert client.get(reverse("accounts:staff")).status_code == 403


def test_read_only_org_shows_banner_and_blocks_writes(client, owner):
    Organization.objects.filter(pk=owner.organization.pk).update(status=Organization.Status.READ_ONLY)
    home = client.get(reverse("accounts:home"))
    assert "subscription has lapsed" in home.content.decode()
    assert client.get(reverse("accounts:invite")).status_code == 403


def test_suspended_member_loses_access_on_next_request(client):
    owner = make_org()
    m = add_member(owner.organization, "manager", all_properties=True)
    login(client, m)
    assert client.get(reverse("accounts:staff")).status_code == 200
    services.set_member_active(owner, m, False)
    # The only membership is now inactive, so the user has no organization.
    assert client.get(reverse("accounts:staff"))["Location"] == reverse("accounts:onboarding")


def _org_form(**overrides):
    data = {"name": "Kamau Homes", "org_type": "COMPANY", "brand_name": "Kamau Homes Ltd",
            "billing_phone": "0712 345678", "billing_email": "Office@Kamau.example", "document_footer": "P.O. Box 1",
            "invoice_lead_days": "7", "late_fee_mode": "OFF"}
    data.update(overrides)
    return data


def test_owner_edits_the_organization_and_it_is_audited(client, owner):
    from audit.models import AuditEvent

    page = client.get(reverse("accounts:organization"))
    assert page.status_code == 200
    assert reverse("letters:settings") in page.content.decode()  # the other settings pages are listed
    response = client.post(reverse("accounts:organization"), _org_form())
    assert response.status_code == 302
    org = Organization.objects.get(pk=owner.organization_id)
    assert (org.name, org.org_type, org.display_name) == ("Kamau Homes", "COMPANY", "Kamau Homes Ltd")
    assert org.billing_phone == "+254712345678" and org.billing_email == "office@kamau.example"
    assert org.invoice_lead_days == 7 and not org.prorate_partial_months  # unticked box turns it off
    event = AuditEvent.objects.get(action="organization.update")
    assert event.changes["name"][1] == "Kamau Homes" and "invoice_lead_days" in event.changes
    # Saving the same values again changes nothing and records nothing.
    client.post(reverse("accounts:organization"), _org_form())
    assert AuditEvent.objects.filter(action="organization.update").count() == 1


def test_organization_form_rejects_bad_values(client, owner):
    before = Organization.objects.get(pk=owner.organization_id).name
    for bad in ({"name": "  "}, {"invoice_lead_days": "40"}, {"billing_phone": "12"}):
        assert client.post(reverse("accounts:organization"), _org_form(**bad)).status_code == 400, bad
    assert Organization.objects.get(pk=owner.organization_id).name == before


def test_only_organization_managers_open_the_settings_page(client):
    org = make_org().organization
    login(client, add_member(org, "manager", all_properties=True))
    assert client.get(reverse("accounts:organization")).status_code == 403
    assert client.post(reverse("accounts:organization"), _org_form()).status_code == 403
