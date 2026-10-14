"""Repair reports typed without a connection and sent later (D-090)."""

import datetime
import json

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from audit.models import AuditEvent
from maintenance.models import MaintenanceRequest, OfflineReport
from properties import services as property_services
from reports.tests.test_income import make_lease

pytestmark = pytest.mark.django_db
REPORT_ID = "9b1c2d3e-4f5a-4b6c-8d7e-0f1a2b3c4d5e"
URL = reverse("maintenance:offline_report")


def send(client, body):
    return client.post(URL, json.dumps(body), content_type="application/json")


def report_for(prop, unit=None, **values):
    fields = {"property": str(prop.public_id), "unit": str(unit.public_id) if unit else "", "title": "Tap leaking",
              "description": "Under the kitchen sink", "kind": "PLUMBING", "priority": "HIGH", "from_tenant": ""}
    fields.update(values.pop("fields", {}))
    return {"id": values.get("id", REPORT_ID), "typed_at": values.get("typed_at"), "fields": fields}


def test_a_kept_report_is_recorded_once_from_when_it_was_typed(client, owner, prop, unit):
    login(client, owner)
    typed = timezone.now() - datetime.timedelta(hours=3)
    response = send(client, report_for(prop, unit, typed_at=typed.isoformat()))
    assert response.status_code == 201, response.content
    req = MaintenanceRequest.objects.get()
    assert response.json() == {"status": "recorded", "number": req.number,
                               "url": reverse("maintenance:detail", args=[req.public_id])}
    assert (req.property, req.unit, req.title, req.kind, req.priority, req.reported_by) == (
        prop, unit, "Tap leaking", "PLUMBING", "HIGH", owner.user)
    # The clock started when it was typed on the phone (item 4).
    assert abs((req.created_at - typed).total_seconds()) < 1
    target = req.respond_by - req.created_at
    assert target > datetime.timedelta(0) and req.respond_by < timezone.now() + target - datetime.timedelta(hours=2)
    kept = OfflineReport.objects.get()
    assert (kept.key, kept.user, kept.request) == (REPORT_ID, owner.user, req)
    event = AuditEvent.objects.get(action="maintenance.offline_report")
    assert event.changes["report"] == [None, REPORT_ID] and event.changes["typed_at"][1] == typed.isoformat()
    assert AuditEvent.objects.filter(action="maintenance.report").count() == 1
    # The answer was lost and the phone sends again: the same request.
    again = send(client, report_for(prop, unit, typed_at=typed.isoformat()))
    assert again.status_code == 200 and again.json()["status"] == "already" and again.json()["number"] == req.number
    assert MaintenanceRequest.objects.count() == 1


def test_an_odd_typing_time_is_replaced_with_the_arrival_time(client, owner, prop):
    login(client, owner)
    for n, typed in enumerate((timezone.now() + datetime.timedelta(hours=2),
                               timezone.now() - datetime.timedelta(days=8), "2026-01-01T10:00:00", None)):
        body = report_for(prop, id=f"report-{n:04d}", typed_at=typed if isinstance(typed, str | None)
                          else typed.isoformat())
        assert send(client, body).status_code == 201
    for req in MaintenanceRequest.objects.all():
        assert abs((req.created_at - timezone.now()).total_seconds()) < 60


def test_a_refused_report_records_nothing_and_says_why(client, owner, org, prop, unit):
    login(client, owner)
    blank = send(client, report_for(prop, fields={"title": "  "}))
    assert blank.status_code == 422 and blank.json()["status"] == "refused" and "title" in blank.json()["errors"]
    other_unit = make_lease(owner, make_property(org, name="Other"), code="Z1").unit
    wrong = send(client, report_for(prop, other_unit))
    assert wrong.status_code == 422 and wrong.json()["errors"] == {"unit": ["Choose a unit of this property."]}
    assert send(client, report_for(prop, fields={"unit": "nope"})).status_code == 422
    assert send(client, report_for(prop, fields={"kind": "MAGIC"})).json()["errors"]["kind"]
    vacant = property_services.create_unit(owner, prop, code="B9")
    tenantless = send(client, report_for(prop, vacant, fields={"from_tenant": "on"}))
    assert tenantless.status_code == 422 and "from_tenant" in tenantless.json()["errors"]
    assert not MaintenanceRequest.objects.exists() and not OfflineReport.objects.exists()
    # Fixed on the phone and sent again with the same id: recorded.
    assert send(client, report_for(prop, unit)).status_code == 201


def test_a_report_is_checked(client, owner, prop):
    login(client, owner)
    assert send(client, {"id": "short", "fields": {}}).status_code == 400
    assert send(client, {"id": REPORT_ID, "fields": "x"}).status_code == 400
    assert send(client, {**report_for(prop), "fields": {"property": str(prop.public_id), "title": 5}}).status_code \
        == 400
    assert client.post(URL, "{nope", content_type="application/json").status_code == 400
    assert send(client, {**report_for(prop), "pad": "x" * 17000}).status_code == 413
    assert client.get(URL).status_code == 405
    assert not MaintenanceRequest.objects.exists()


def test_only_someone_who_may_report_there_can_send(client, owner, org, prop):
    # Logged out: a redirect, which the phone treats as "try later".
    assert send(client, report_for(prop)).status_code == 302
    login(client, add_member(org, "accountant", all_properties=True))
    assert send(client, report_for(prop)).status_code == 403
    login(client, add_member(org, "caretaker", properties=[make_property(org, name="Elsewhere")]))
    refused = send(client, report_for(prop))
    assert refused.status_code == 422 and "property" in refused.json()["errors"]
    login(client, make_org(name="Other"))
    assert send(client, report_for(prop)).status_code == 422
    assert not MaintenanceRequest.objects.exists()


def test_the_same_id_from_two_people_is_two_reports(client, owner, org, prop):
    login(client, add_member(org, "caretaker", all_properties=True))
    assert send(client, report_for(prop)).status_code == 201
    login(client, owner)
    assert send(client, report_for(prop)).status_code == 201
    assert MaintenanceRequest.objects.count() == 2


def test_the_page_carries_what_the_phone_needs(client, owner, prop):
    login(client, owner)
    page = client.get(reverse("maintenance:create")).content.decode()
    assert "data-offline-report" in page and URL in page and "data-offline-list" in page
    assert "kept on this phone" in page and "js/offline.js" in page
