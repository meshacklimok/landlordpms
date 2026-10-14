"""Owners can require two-step login for every member (D-107)."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from accounts import mfa, services
from accounts.tests.factories import PASSWORD, add_member, make_org
from accounts.tests.test_mfa import code, enable
from audit.models import AuditEvent

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner():
    owner = make_org("Kilimani Homes")
    enable(owner.user)
    return owner


def _require(owner):
    services.set_require_mfa(owner, True)
    owner.organization.refresh_from_db()


def _verified(client, user):
    client.force_login(user)
    session = client.session
    session[mfa.VERIFIED] = True
    session.save()


def test_switching_it_on_needs_it_for_yourself_first():
    owner = make_org()
    with pytest.raises(ValidationError):
        services.set_require_mfa(owner, True)
    owner.organization.refresh_from_db()
    assert owner.organization.require_mfa is False


def test_switching_it_on_and_off_is_recorded(owner):
    _require(owner)
    assert owner.organization.require_mfa is True
    services.set_require_mfa(owner, False)
    actions = list(AuditEvent.objects.filter(organization=owner.organization, action__startswith="organization.require")
                   .order_by("id").values_list("action", flat=True))
    assert actions == ["organization.require_mfa_on", "organization.require_mfa_off"]


def test_only_those_who_manage_the_organization_may_switch_it(owner):
    manager = add_member(owner.organization, "manager", all_properties=True)
    enable(manager.user)
    with pytest.raises(PermissionDenied):
        services.set_require_mfa(manager, True)


def test_a_member_without_it_is_sent_to_set_it_up(client, owner):
    _require(owner)
    member = add_member(owner.organization, "manager", all_properties=True)
    client.force_login(member.user)
    response = client.get(reverse("accounts:home"))
    assert response.status_code == 302 and response["Location"] == reverse("accounts:security")
    page = client.get(reverse("accounts:security")).content.decode()
    assert "Kilimani Homes requires two-step login for all staff" in page


def test_a_member_with_it_gives_a_code_before_going_on(client, owner):
    _require(owner)
    member = add_member(owner.organization, "manager", all_properties=True)
    secret, _codes = enable(member.user)
    client.force_login(member.user)  # a session from before it was required: no code given yet
    path = reverse("properties:list")
    response = client.get(path)
    assert response.status_code == 302 and response["Location"].startswith(reverse("accounts:login_mfa"))
    client.post(reverse("accounts:login_mfa"), {"code": code(secret), "next": path})
    assert client.get(path).status_code == 200


def test_an_organization_that_does_not_require_it_is_unaffected(client, owner):
    other = make_org()
    client.force_login(other.user)
    assert client.get(reverse("accounts:home")).status_code == 200


def test_it_cannot_be_switched_off_while_required(client, owner):
    _require(owner)
    with pytest.raises(ValidationError) as exc:
        mfa.disable(owner.user, password=PASSWORD, code="000000")
    assert "Kilimani Homes requires two-step login" in str(exc.value)
    assert mfa.is_enabled(owner.user)
    _verified(client, owner.user)
    page = client.get(reverse("accounts:security")).content.decode()
    assert "Switch off two-step login" not in page and "It stays on while you are a member" in page


def test_the_staff_page_switches_it_and_shows_who_uses_it(client, owner):
    member = add_member(owner.organization, "manager", all_properties=True)
    _verified(client, owner.user)
    page = client.get(reverse("accounts:staff")).content.decode()
    assert "Two-step login for all staff" in page and "Require it" in page
    response = client.post(reverse("accounts:require_mfa"), {"on": "1"})
    assert response.status_code == 302
    owner.organization.refresh_from_db()
    assert owner.organization.require_mfa
    page = client.get(reverse("accounts:staff")).content.decode()
    assert "No 2-step yet" in page and member.user.full_name in page
    client.post(reverse("accounts:require_mfa"), {"on": "0"})
    owner.organization.refresh_from_db()
    assert not owner.organization.require_mfa


def test_members_who_may_not_switch_it_are_refused(client, owner):
    member = add_member(owner.organization, "manager", all_properties=True)
    client.force_login(member.user)
    assert client.post(reverse("accounts:require_mfa"), {"on": "1"}).status_code == 403
