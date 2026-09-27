"""Public vacancy link (D-040): shareable by unit managers and agents, read-only, revocable, not indexed."""

import datetime

import pytest
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from accounts.models import Organization
from accounts.tests.factories import add_member, fresh, make_org
from accounts.tests.test_isolation import login
from audit.models import AuditEvent
from leases import services as lease_services
from properties import services, views
from properties.models import Unit
from tenants import services as tenant_services

pytestmark = pytest.mark.django_db

TODAY = datetime.date.today()


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def prop(owner):
    return services.create_property(owner, name="Greenview", code="GV", area="Kilimani", county="NAIROBI")


@pytest.fixture
def unit(owner, prop):
    return services.create_unit(owner, prop, code="A1", type_label="2 bedroom", list_rent=25000)


def page(client, unit):
    return client.get(reverse("vacancy", args=[unit.share_token]))


def test_share_and_view_without_login(client, owner, unit):
    services.share_unit(owner, unit)
    r = page(client, unit)
    assert r.status_code == 200 and r["X-Robots-Tag"] == "noindex, nofollow"
    body = r.content.decode()
    assert "Available to let" in body and "2 bedroom" in body and "Kilimani" in body and "25,000" in body
    assert owner.user.phone in body
    assert "GV-A1" not in body  # the payment reference stays private
    assert AuditEvent.objects.filter(action="unit.share", object_id=str(unit.public_id)).exists()


def test_new_link_and_turning_off(client, owner, unit):
    old = services.share_unit(owner, unit).share_token
    services.share_unit(owner, unit)
    assert unit.share_token != old
    assert client.get(reverse("vacancy", args=[old])).status_code == 404
    services.unshare_unit(owner, unit)
    assert Unit.objects.get(pk=unit.pk).share_token is None
    assert client.get(reverse("vacancy", args=["nothing"])).status_code == 404
    assert AuditEvent.objects.filter(action="unit.unshare").exists()


def test_let_or_held_units_say_no_longer_available(client, owner, unit):
    services.share_unit(owner, unit)
    tenant = tenant_services.create_tenant(owner, name="Wanjiku", phone="0712345678")
    lease = lease_services.create_lease(owner, unit=unit, tenants=[tenant], start_date=TODAY + datetime.timedelta(
        days=10), end_date=None, rent=25000)
    assert "Available to let" in page(client, unit).content.decode()  # a draft holds nothing
    lease_services.activate_lease(owner, lease)
    body = page(client, unit).content.decode()
    assert "No longer available" in body and owner.user.phone not in body

    other = services.create_unit(owner, unit.property, code="A2")
    services.share_unit(owner, other)
    services.set_unit_status(owner, other, Unit.ManualStatus.UNDER_MAINTENANCE)
    assert "No longer available" in page(client, other).content.decode()


def test_hidden_when_archived_or_org_not_active(client, owner, unit):
    services.share_unit(owner, unit)
    Organization.objects.filter(pk=owner.organization_id).update(status=Organization.Status.FROZEN)
    assert page(client, unit).status_code == 404
    Organization.objects.filter(pk=owner.organization_id).update(status=Organization.Status.ACTIVE)
    services.archive_unit(owner, unit)
    assert page(client, unit).status_code == 404


def test_who_may_share(owner, prop, unit):
    agent = fresh(add_member(owner.organization, "leasing_agent", properties=[prop]))
    assert services.share_unit(agent, unit).shared_by == agent.user
    caretaker = fresh(add_member(owner.organization, "caretaker", properties=[prop]))
    with pytest.raises(PermissionDenied):
        services.share_unit(caretaker, unit)
    with pytest.raises(PermissionDenied):
        services.unshare_unit(make_org(), unit)


def test_contact_falls_back_to_the_organization_when_the_sharer_leaves(client, owner, prop, unit):
    from accounts import services as account_services

    agent = fresh(add_member(owner.organization, "leasing_agent", properties=[prop]))
    services.share_unit(agent, unit)
    assert agent.user.phone in page(client, unit).content.decode()
    account_services.set_member_active(owner, agent, False)
    body = page(client, unit).content.decode()
    assert agent.user.phone not in body and agent.user.full_name not in body
    assert owner.organization.display_name in body


def test_rate_limited(client, owner, unit, monkeypatch):
    monkeypatch.setattr(views, "VACANCY_RATE_LIMIT", 2)
    services.share_unit(owner, unit)
    assert [page(client, unit).status_code for _ in range(3)] == [200, 200, 429]


def test_share_from_the_unit_page(client, owner, unit):
    login(client, owner)
    url = reverse("properties:unit", args=[unit.public_id])
    assert "Create vacancy link" in client.get(url).content.decode()
    client.post(url, {"action": "share"})
    unit.refresh_from_db()
    r = client.get(url)
    assert r.context["share_url"].endswith(f"/v/{unit.share_token}/") and "wa.me" in r.content.decode()
    client.post(url, {"action": "unshare"})
    assert Unit.objects.get(pk=unit.pk).share_token is None
