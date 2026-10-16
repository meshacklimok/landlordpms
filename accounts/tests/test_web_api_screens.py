"""The JSON behind the Next.js pickups sheet, switch guide and notification settings (D-168, D-161)."""

import datetime
import json

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Organization
from analytics.tests.test_console import signed_in
from leases import services as lease_services
from notifications import catalog
from notifications.models import NotificationTypeSetting
from properties import services as property_services
from tenants import services as tenant_services

from .factories import make_org, make_property, make_user
from .test_isolation import login

pytestmark = pytest.mark.django_db


@pytest.fixture
def collector():
    owner = make_org()
    owner.organization.org_type = Organization.Type.WASTE
    owner.organization.save(update_fields=["org_type"])
    return owner


def _contract(owner, prop, code="A1"):
    unit = property_services.create_unit(owner, prop, code=code)
    tenant = tenant_services.create_tenant(owner, name=f"Customer {code}", phone="0712345601")
    start = timezone.localdate() - datetime.timedelta(days=40)
    lease = lease_services.create_lease(owner, unit=unit, tenants=[tenant], start_date=start, end_date=None, rent=500)
    return lease_services.activate_lease(owner, lease)


def _url(prop):
    return reverse("leases:web_pickups", args=[prop.public_id])


def test_the_sheet_lists_the_route_and_saves_marks(client, collector):
    prop = make_property(collector.organization)
    lease = _contract(collector, prop)
    login(client, collector)
    today = timezone.localdate().isoformat()

    sheet = client.get(_url(prop), {"day": today}).json()
    assert [r["unit"] for r in sheet["rows"]] == ["A1"] and sheet["rows"][0]["outcome"] == ""

    body = {"day": today, "marks": {str(lease.pk): "MISSED"}}
    response = client.post(_url(prop), json.dumps(body), content_type="application/json")
    assert response.status_code == 200
    data = response.json()
    assert data["sheet"]["rows"][0]["outcome"] == "MISSED"
    assert data["sheet"]["missed"][0]["missed"] == 1

    # "All collected" fills only the rows with no mark in the submitted sheet; a mark the crew set stays.
    again = {"day": today, "all_done": True, "marks": {str(lease.pk): "MISSED"}}
    client.post(_url(prop), json.dumps(again), content_type="application/json")
    assert client.get(_url(prop), {"day": today}).json()["rows"][0]["outcome"] == "MISSED"
    client.post(_url(prop), json.dumps({"day": today, "all_done": True, "marks": {}}), content_type="application/json")
    assert client.get(_url(prop), {"day": today}).json()["rows"][0]["outcome"] == "DONE"


def test_a_day_that_has_not_come_is_refused(client, collector):
    prop = make_property(collector.organization)
    _contract(collector, prop)
    login(client, collector)
    tomorrow = (timezone.localdate() + datetime.timedelta(days=1)).isoformat()
    response = client.post(_url(prop), json.dumps({"day": tomorrow, "marks": {}}), content_type="application/json")
    assert response.status_code == 422 and response.json()["error"] == "invalid"


def test_only_garbage_collectors_have_a_sheet(client):
    owner = make_org()  # a company
    prop = make_property(owner.organization)
    login(client, owner)
    assert client.get(_url(prop)).status_code == 404


def test_another_organizations_route_is_not_found(client, collector):
    prop = make_property(collector.organization)
    other = make_org()
    other.organization.org_type = Organization.Type.WASTE
    other.organization.save(update_fields=["org_type"])
    login(client, other)
    assert client.get(_url(prop)).status_code == 404


def test_a_visitor_gets_401_from_every_screen_view(client):
    owner = make_org()
    prop = make_property(owner.organization)
    for url in (_url(prop), reverse("imports:web_switch"), reverse("console:web_notifications")):
        assert client.get(url).status_code == 401


def test_the_switch_guide_marks_finished_steps(client):
    owner = make_org()
    login(client, owner)
    data = client.get(reverse("imports:web_switch")).json()
    assert len(data["steps"]) == 6 and not any(s["done"] for s in data["steps"])
    assert data["can_import"] is True and data["can_announce"] is True


def test_notification_settings_are_for_the_platform_owner_only(client):
    signed_in(client, make_user())
    assert client.get(reverse("console:web_notifications")).status_code == 404  # no seat at all
    client.logout()
    signed_in(client, make_user(is_superuser=True, is_staff=True))
    types = client.get(reverse("console:web_notifications")).json()["types"]
    assert types and {"codename", "label", "channels", "enabled", "changed"} <= set(types[0])


def test_a_notification_type_can_be_changed_and_reset(client):
    signed_in(client, make_user(is_superuser=True, is_staff=True))
    base = catalog.TYPES[0]
    url = reverse("console:web_notification", args=[base.codename])
    body = {"enabled": False, "channels": [base.channels[0]], "offsets": [], "mandatory": False, "urgent": True}
    assert client.post(url, json.dumps(body), content_type="application/json").status_code == 200
    saved = client.get(url).json()["type"]
    assert saved["enabled"] is False and saved["urgent"] is True and saved["changed"] is True

    bad = dict(body, channels=["CARRIER_PIGEON"])
    response = client.post(url, json.dumps(bad), content_type="application/json")
    assert response.status_code == 422 and "channels" in response.json()["fields"]

    assert client.post(url, json.dumps({"reset": True}), content_type="application/json").status_code == 200
    assert not NotificationTypeSetting.objects.filter(type=base.codename).exists()
    assert client.get(reverse("console:web_notification", args=["nope"])).status_code == 404


def test_the_settings_form_is_sent_as_data_and_saved_with_a_logo(client, tmp_path, settings):
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    from .factories import make_org

    settings.MEDIA_ROOT = tmp_path
    owner = make_org()
    login(client, owner)
    url = reverse("accounts:web_organization")
    data = client.get(url).json()
    names = [f["name"] for f in data["fields"]]
    assert {"name", "logo", "remove_logo", "late_fee_mode", "invoice_lead_days"} <= set(names)
    mode = next(f for f in data["fields"] if f["name"] == "late_fee_mode")
    assert mode["kind"] == "select" and ["OFF", "No late fees"] in mode["choices"]
    assert data["logo_url"] is None and data["post_url"] == url

    picture = io.BytesIO()
    Image.new("RGB", (20, 10), "red").save(picture, "PNG")
    body = {"name": "Kamau Homes", "org_type": owner.organization.org_type, "invoice_lead_days": "7",
            "late_fee_mode": "OFF", "logo": SimpleUploadedFile("logo.png", picture.getvalue(), "image/png")}
    saved = client.post(url, body)
    assert saved.status_code == 200 and saved.json()["logo_url"]
    assert next(f for f in saved.json()["fields"] if f["name"] == "name")["value"] == "Kamau Homes"

    removed = client.post(url, {**{k: v for k, v in body.items() if k != "logo"}, "remove_logo": "on"})
    assert removed.json()["logo_url"] is None

    bad = client.post(url, {**{k: v for k, v in body.items() if k != "logo"}, "invoice_lead_days": "99"})
    assert bad.status_code == 422 and "invoice_lead_days" in bad.json()["fields"]
