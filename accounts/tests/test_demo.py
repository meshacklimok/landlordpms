"""The public "Try the demo" organization (D-210): sample data, read-only, and nothing is sent."""

import pytest
from django.core.management import call_command
from django.urls import reverse

from accounts import demo
from accounts.models import Organization
from accounts.tests.factories import add_member, make_org
from billing.models import Invoice
from leases.models import Lease
from meters.models import Meter
from mpesa.models import MpesaTransaction
from notifications import delivery
from notifications.models import Message
from payments.models import Payment
from properties.models import Property, Unit
from prospects.models import Prospect

pytestmark = pytest.mark.django_db


@pytest.fixture
def seeded():
    """A small demo organization, quick to make: the behaviour tests do not need the full data."""
    membership = make_org(name=demo.DEMO_ORG_NAME, owner=demo.demo_user())
    org = membership.organization
    org.is_demo = True
    org.save(update_fields=["is_demo"])
    return org


def test_seed_builds_the_full_demo_and_a_second_run_replaces_it():
    call_command("seed_public_demo")
    first = demo.demo_org()
    assert first.is_demo
    assert Property.objects.for_org(first).count() == 4
    assert Unit.objects.for_org(first).count() == 74
    assert Lease.objects.for_org(first).filter(status=Lease.Status.ACTIVE).count() >= 45
    assert Invoice.objects.filter(organization=first).count() > 300
    assert Payment.objects.filter(organization=first).count() > 250
    assert Meter.objects.filter(organization=first).count() >= 40
    assert Prospect.objects.filter(organization=first).count() >= 10
    assert MpesaTransaction.objects.filter(organization=first).count() > 200
    assert Message.objects.filter(organization=first, channel="IN_APP").exists()
    assert not Message.objects.filter(organization=first, status=Message.Status.QUEUED).exists()
    assert set(demo.STAFF_PHONES.values()) == {key for key, *_ in demo.STAFF}

    call_command("seed_public_demo")
    live = Organization.objects.filter(is_demo=True, archived_at__isnull=True)
    assert live.count() == 1
    assert live.get().pk != first.pk
    assert Organization.all_objects.filter(is_demo=True).count() == 2


def test_notify_does_nothing_for_the_demo(seeded):
    assert delivery.notify(seeded, "payment_received", phone="+254711000001") is None
    assert not Message.objects.filter(organization=seeded, to="+254711000001").exists()


def test_try_the_demo_signs_in_and_shows_the_home_page(client, seeded):
    response = client.post(reverse("growth:demo_enter"))
    assert response.status_code == 302
    assert response.url == reverse("accounts:home")
    page = client.get(reverse("accounts:home"), follow=True)
    assert page.status_code == 200
    assert b"demo with sample data" in page.content


def test_the_demo_user_has_no_password(seeded):
    assert not demo.demo_user().has_usable_password()
    assert not demo.demo_user().is_staff


def test_without_a_demo_organization_the_button_goes_to_the_booking_page(client):
    response = client.post(reverse("growth:demo_enter"))
    assert response.status_code == 302
    assert response.url == reverse("growth:demo")


def test_a_demo_visitor_cannot_change_anything(client, seeded):
    client.post(reverse("growth:demo_enter"))
    before = Property.objects.for_org(seeded).count()
    response = client.post(reverse("properties:create"), {"name": "Sneaky"}, headers={"Accept": "application/json"})
    assert response.status_code == 403
    assert response.json()["error"] == "demo_read_only"
    page = client.post(reverse("properties:create"), {"name": "Sneaky"}, headers={"Accept": "text/html"})
    assert page.status_code == 302
    assert Property.objects.for_org(seeded).count() == before


def test_a_demo_visitor_can_sign_out(client, seeded):
    client.post(reverse("growth:demo_enter"))
    response = client.post(reverse("accounts:logout"))
    assert response.status_code == 302
    assert response.url == reverse("accounts:login")


def test_a_real_landlord_is_not_blocked(client):
    from accounts.tests.factories import PASSWORD

    membership = make_org(name="Real Flats")
    client.login(phone=membership.user.phone, password=PASSWORD)
    response = client.post(reverse("properties:create"), {"name": "Mine"}, headers={"Accept": "application/json"})
    assert response.status_code != 403 or response.json().get("error") != "demo_read_only"


def test_a_signed_in_landlord_keeps_their_session_when_they_press_try_the_demo(client, seeded):
    from accounts.tests.factories import PASSWORD

    membership = make_org(name="Real Flats")
    client.login(phone=membership.user.phone, password=PASSWORD)
    client.post(reverse("growth:demo_enter"))
    assert client.session["_auth_user_id"] == str(membership.user.pk)


def test_the_landing_page_offers_the_demo(client):
    response = client.get(reverse("growth:landing") if False else "/")
    assert b"Try the demo" in response.content


def _staff_in(org):
    """The sample staff of a small demo organization, as the full seed makes them."""
    for _key, name, phone, role_key, _codes in demo.STAFF[1:]:
        add_member(org, role_key, user=demo.demo_user(phone, name), all_properties=True)


def test_a_visitor_can_look_around_as_another_sample_person(client, seeded):
    _staff_in(seeded)
    client.post(reverse("growth:demo_enter"))
    page = client.get(reverse("growth:demo_as", args=["accountant"]))
    assert page.status_code == 302
    assert page.url == reverse("accounts:home")
    assert client.session["_auth_user_id"] == str(demo.demo_user("+254700000002").pk)
    assert b"Look around as" in client.get(reverse("accounts:home"), follow=True).content


def test_switching_to_an_unknown_person_stays_where_it_is(client, seeded):
    client.post(reverse("growth:demo_enter"))
    page = client.get(reverse("growth:demo_as", args=["nobody"]))
    assert page.url == reverse("growth:demo")


def test_start_your_free_trial_leaves_the_demo(client, seeded):
    client.post(reverse("growth:demo_enter"))
    page = client.get(reverse("growth:demo_leave"))
    assert page.status_code == 302
    assert page.url == reverse("accounts:register")
    assert "_auth_user_id" not in client.session


def test_the_pages_with_secrets_or_costs_are_closed_to_a_demo_visitor(client, seeded):
    client.post(reverse("growth:demo_enter"))
    for path in ("/subscription/", "/staff/api-keys/", "/mpesa/settings/", "/security/", "/delete-account/"):
        page = client.get(path, headers={"Accept": "application/json"})
        assert page.status_code == 403, path
        assert page.json()["error"] == "demo_read_only"
        assert client.get(path, headers={"Accept": "text/html"}).status_code == 302


def test_a_real_landlord_can_use_the_pages_a_demo_visitor_cannot(client):
    from accounts.tests.factories import PASSWORD

    membership = make_org(name="Real Flats")
    client.login(phone=membership.user.phone, password=PASSWORD)
    page = client.get("/subscription/")
    assert page.status_code != 403


def test_a_visitor_can_look_around_as_the_sample_tenant_and_change_nothing(client, seeded):
    from accounts.models import Membership
    from accounts.tests.factories import make_property
    from portal.models import TenantAccount
    from reports.tests.test_income import make_lease

    owner = Membership.objects.get(organization=seeded, user=demo.demo_user())
    lease = make_lease(owner, make_property(seeded, name="Sample"), code="A1")
    tenant = lease.lease_tenants.get().tenant
    TenantAccount.objects.create(organization=seeded, tenant=tenant, user=demo.demo_user(demo.TENANT_PHONE, tenant.name))
    client.post(reverse("growth:demo_enter"))
    page = client.get(reverse("growth:demo_as", args=["tenant"]))
    assert page.url == reverse("portal:home")
    home = client.get(reverse("portal:home"))
    assert home.status_code == 200
    assert b"Look around as" in home.content
    refused = client.post(reverse("portal:repair_create", args=[lease.public_id]), {}, headers={"Accept": "application/json"})
    assert refused.status_code == 403
