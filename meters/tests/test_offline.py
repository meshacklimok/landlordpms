"""Meter rounds typed without a connection and sent later (D-088)."""

import datetime
import json

import pytest
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from audit.models import AuditEvent
from meters import services
from meters.models import MeterReading, OfflineRound

from .conftest import let, make_meter, read

pytestmark = pytest.mark.django_db
D = datetime.date
ROUND_ID = "3f2a9c1e-0b7d-4f7a-9e55-1c2d3e4f5a6b"


@pytest.fixture
def meters(owner, prop):
    _lease, u1 = let(owner, prop)
    _lease, u2 = let(owner, prop, code="A2")
    first, second = make_meter(owner, prop, [u1], label="W1"), make_meter(owner, prop, [u2], label="W2")
    for meter in (first, second):
        services.approve(owner, read(owner, meter, D(2025, 12, 31), 100))
    return first, second


def send(client, prop, body, **extra):
    return client.post(reverse("meters:offline_round", args=[prop.public_id]), json.dumps(body),
                       content_type="application/json", **extra)


def round_for(meters, **values):
    entries = [{"meter": str(m.public_id), "label": m.label, "value": v, "note": "", "replaced": False}
               for m, v in zip(meters, values.get("values", ("111", "205")), strict=False)]
    return {"id": values.get("id", ROUND_ID), "read_on": values.get("read_on", "2026-01-31"),
            "typed_at": "2026-01-31T09:15:00+03:00", "entries": entries}


def test_a_kept_round_is_recorded_once(client, owner, prop, meters):
    login(client, owner)
    response = send(client, prop, round_for(meters))
    assert response.status_code == 201 and response.json() == {"status": "recorded", "readings": 2}
    readings = MeterReading.objects.filter(read_on=D(2026, 1, 31))
    assert {(r.meter, str(r.value), r.status, r.recorded_by) for r in readings} == {
        (meters[0], "111.000", "SUBMITTED", owner.user), (meters[1], "205.000", "SUBMITTED", owner.user)}
    kept = OfflineRound.objects.get()
    assert (kept.key, kept.user, kept.property, kept.readings) == (ROUND_ID, owner.user, prop, 2)
    assert kept.typed_at.isoformat().startswith("2026-01-31T06:15")
    event = AuditEvent.objects.get(action="meters.offline_round")
    assert event.changes["round"] == [None, ROUND_ID] and event.changes["readings"] == [None, 2]
    assert AuditEvent.objects.filter(action="meters.reading_record").count() == 2 + 2  # the fixture's own two
    # The answer was lost and the phone sends again: nothing new.
    again = send(client, prop, round_for(meters))
    assert again.status_code == 200 and again.json() == {"status": "already"}
    assert MeterReading.objects.filter(read_on=D(2026, 1, 31)).count() == 2


def test_an_impossible_typed_at_is_ignored_not_a_server_error(client, owner, prop, meters):
    login(client, owner)
    response = send(client, prop, {**round_for(meters), "typed_at": "2026-01-31T25:00:00+03:00"})
    assert response.status_code == 201 and OfflineRound.objects.get().typed_at is None


def test_a_refused_round_records_nothing_and_says_why(client, owner, prop, meters):
    login(client, owner)
    read(owner, meters[1], D(2026, 2, 5), 210)  # someone else read W2 after the round's date
    response = send(client, prop, round_for(meters))
    assert response.status_code == 422
    body = response.json()
    assert body["status"] == "refused" and list(body["errors"]) == [str(meters[1].public_id)]
    assert "The last reading was on 05 Feb 2026" in body["errors"][str(meters[1].public_id)][0]
    assert not MeterReading.objects.filter(read_on=D(2026, 1, 31)).exists() and not OfflineRound.objects.exists()
    # Fixed on the phone and sent again with the same id: recorded.
    assert send(client, prop, round_for(meters[:1])).status_code == 201


def test_a_round_is_checked(client, owner, prop, meters):
    login(client, owner)
    assert send(client, prop, {"id": "short", "entries": []}).status_code == 400
    assert send(client, prop, {**round_for(meters), "entries": "x"}).status_code == 400
    assert send(client, prop, {**round_for(meters), "entries": [{"meter": "x", "value": 5}]}).status_code == 400
    bad = client.post(reverse("meters:offline_round", args=[prop.public_id]), "{nope",
                      content_type="application/json")
    assert bad.status_code == 400
    assert send(client, prop, round_for(meters, read_on="31/01/2026")).status_code == 422
    assert send(client, prop, round_for(meters, read_on="2026-02-30")).status_code == 422
    assert send(client, prop, {**round_for(meters), "entries": []}).status_code == 422
    gone = send(client, prop, {**round_for(meters), "entries": [{"meter": "nope", "value": "5"}]})
    assert gone.status_code == 422 and gone.json()["errors"] == {"nope": ["This meter is no longer on this property."]}
    future = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
    assert send(client, prop, round_for(meters, read_on=future)).status_code == 422
    assert not OfflineRound.objects.exists()
    assert client.get(reverse("meters:offline_round", args=[prop.public_id])).status_code == 405


def test_only_someone_who_may_read_meters_there_can_send(client, owner, prop, meters):
    # Logged out: a redirect, which the phone treats as "try later".
    assert send(client, prop, round_for(meters)).status_code == 302
    other = make_org(name="Other")
    login(client, other)
    assert send(client, prop, round_for(meters)).status_code == 404
    elsewhere = make_property(owner.organization, name="Elsewhere")
    caretaker = add_member(owner.organization, "caretaker", properties=[elsewhere])
    login(client, caretaker)
    assert send(client, prop, round_for(meters)).status_code == 404
    assert not OfflineRound.objects.exists()


def test_the_same_id_from_two_people_is_two_rounds(client, owner, prop, meters):
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    login(client, caretaker)
    assert send(client, prop, round_for(meters[:1])).status_code == 201
    login(client, owner)
    assert send(client, prop, round_for(meters[1:])).status_code == 201
    assert OfflineRound.objects.count() == 2


def test_the_pages_carry_what_the_phone_needs(client, owner, prop, meters):
    login(client, owner)
    page = client.get(reverse("meters:round", args=[prop.public_id])).content.decode()
    assert "data-offline-round" in page and reverse("meters:offline_round", args=[prop.public_id]) in page
    assert f'data-meter="{meters[0].public_id}"' in page and "data-offline-list" in page
    assert "kept on this phone" in page
    home = client.get(reverse("meters:list")).content.decode()
    assert "js/offline.js" in home and f'data-user="{owner.user.public_id}"' in home and "data-offline-badge" in home
    assert "data-logout" in home
