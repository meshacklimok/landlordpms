"""Student hostels: rooms with beds, billing per bed or per room, semesters, guardians (D-188)."""

import datetime

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from accounts.models import Organization
from accounts.tests.factories import make_org, make_property
from accounts.tests.test_isolation import login
from billing import invoicing
from leases import services as lease_services
from leases.models import Lease, LeasePayer
from properties import hostel
from properties.models import Room, Unit
from tenants import services as tenant_services

pytestmark = pytest.mark.django_db

D = datetime.date


@pytest.fixture
def owner():
    m = make_org("Campus View")
    m.organization.org_type = Organization.Type.HOSTEL
    m.organization.save(update_fields=["org_type"])
    return m


@pytest.fixture
def prop(owner):
    return make_property(owner.organization)


def test_hostel_words_and_billing():
    org = Organization(org_type=Organization.Type.HOSTEL)
    assert str(org.vocab["tenants"]) == "Students"
    assert org.bills_rent


def test_rooms_with_beds_make_one_unit_per_bed(owner, prop):
    rooms = hostel.add_rooms(owner, prop, prefix="R", first=101, count=3, capacity=4, gender="F")
    assert [r.code for r in rooms] == ["R101", "R102", "R103"]
    beds = Unit.objects.filter(property=prop, room__code="R101").order_by("code")
    assert [u.code for u in beds] == ["R101-A", "R101-B", "R101-C", "R101-D"]
    assert all(u.unit_type == Unit.Type.BED_SPACE for u in beds)
    assert Unit.objects.filter(property=prop).count() == 12


def test_a_room_let_whole_is_one_unit(owner, prop):
    (room,) = hostel.add_rooms(owner, prop, prefix="S", first=1, count=1, capacity=3, let_whole=True, price=18000)
    (unit,) = room.units.all()
    assert (unit.code, unit.unit_type, unit.list_rent) == ("S1", Unit.Type.HOSTEL_ROOM, 18000)


def test_a_clashing_room_rolls_everything_back(owner, prop):
    hostel.add_rooms(owner, prop, prefix="R", first=102, count=1, capacity=2)
    with pytest.raises(ValidationError):
        hostel.add_rooms(owner, prop, prefix="R", first=101, count=3, capacity=2)
    assert Room.objects.filter(property=prop).count() == 1
    assert Unit.objects.filter(property=prop).count() == 2


def test_a_room_can_take_more_beds_but_not_fewer(owner, prop):
    (room,) = hostel.add_rooms(owner, prop, prefix="R", first=1, count=1, capacity=2, price=9000)
    hostel.update_room(owner, room, capacity=3)
    assert room.units.count() == 3
    assert room.units.filter(code="R1-C", list_rent=9000).exists()
    with pytest.raises(ValidationError):
        hostel.update_room(owner, room, capacity=2)


def test_a_room_with_an_open_booking_cannot_be_archived(owner, prop):
    (room,) = hostel.add_rooms(owner, prop, prefix="R", first=1, count=1, capacity=2)
    student = tenant_services.create_tenant(owner, name="Amina", phone="0712345601")
    lease_services.create_lease(owner, unit=room.units.first(), tenants=[student], start_date=D(2026, 1, 5), rent=8000)
    with pytest.raises(ValidationError):
        hostel.archive_room(owner, room)
    assert not Room.all_objects.get(pk=room.pk).is_archived


def test_a_semester_is_one_invoice_for_four_months(owner, prop):
    (room,) = hostel.add_rooms(owner, prop, prefix="R", first=1, count=1, capacity=1)
    student = tenant_services.create_tenant(owner, name="Amina", phone="0712345601")
    lease = lease_services.create_lease(owner, unit=room.units.first(), tenants=[student], start_date=D(2026, 1, 5),
                                        rent=8000, billing_frequency=Lease.Frequency.SEMESTER)
    lease = lease_services.activate_lease(owner, lease)
    assert invoicing.period_months(lease, D(2026, 4, 1)) == [D(2026, m, 1) for m in range(1, 5)]
    assert invoicing.period_months(lease, D(2026, 5, 1)) == [D(2026, m, 1) for m in range(5, 9)]


def test_a_whole_room_booking_names_several_students(owner, prop):
    (room,) = hostel.add_rooms(owner, prop, prefix="S", first=1, count=1, capacity=2, let_whole=True)
    a = tenant_services.create_tenant(owner, name="Amina", phone="0712345601")
    b = tenant_services.create_tenant(owner, name="Brenda", phone="0712345602")
    lease = lease_services.create_lease(owner, unit=room.units.get(), tenants=[a, b], start_date=D(2026, 1, 5),
                                        rent=18000)
    assert lease.lease_tenants.count() == 2


def test_the_guardian_phone_becomes_a_payer(owner, prop):
    (room,) = hostel.add_rooms(owner, prop, prefix="R", first=1, count=1, capacity=1)
    student = tenant_services.create_tenant(owner, name="Amina", phone="0712345601",
                                            emergency_contact_name="Mama Amina", emergency_contact_phone="0722000111")
    lease = lease_services.create_lease(owner, unit=room.units.get(), tenants=[student], start_date=D(2026, 1, 5),
                                        rent=8000)
    payer = LeasePayer.objects.get(lease=lease)
    assert payer.phone == "+254722000111" and payer.name == "Mama Amina"


def test_a_landlord_does_not_get_guardian_payers():
    m = make_org()
    prop = make_property(m.organization)
    unit = Unit.objects.create(organization=m.organization, property=prop, code="A1")
    t = tenant_services.create_tenant(m, name="Joe", phone="0712345601", emergency_contact_phone="0722000111")
    lease = lease_services.create_lease(m, unit=unit, tenants=[t], start_date=D(2026, 1, 5), rent=8000)
    assert not LeasePayer.objects.filter(lease=lease).exists()


def test_student_details_are_kept_for_hostels(owner):
    t = tenant_services.create_tenant(owner, name="Amina", phone="0712345601", student_number="S/123",
                                      institution="UoN", course="Law", year_of_study=2)
    t.refresh_from_db()
    assert (t.student_number, t.institution, t.year_of_study) == ("S/123", "UoN", 2)


def test_room_pages_work_for_a_hostel_and_are_hidden_from_others(client, owner, prop):
    login(client, owner)
    url = reverse("properties:rooms_create", args=[prop.public_id])
    assert client.get(url).status_code == 200
    resp = client.post(url, {"prefix": "R", "first": 101, "count": 2, "let": "beds", "capacity": 3, "gender": "M"})
    assert resp.status_code == 302
    page = client.get(reverse("properties:rooms", args=[prop.public_id])).content.decode()
    assert "R101-A" in page and "R102-C" in page
    room = Room.objects.get(property=prop, code="R101")
    assert client.get(reverse("properties:room", args=[room.public_id])).status_code == 200
    assert client.get(reverse("properties:create")).status_code == 200
    assert client.get(reverse("tenants:create")).status_code == 200


def test_room_pages_are_not_found_for_a_landlord(client):
    m = make_org()
    prop = make_property(m.organization)
    login(client, m)
    assert client.get(reverse("properties:rooms", args=[prop.public_id])).status_code == 404


# ---------------------------------------------------------------------------
# Phase 3: booking page, waitlist, term rollover (D-189)
# ---------------------------------------------------------------------------


def _book(client, token, **over):
    data = {"name": "Amina W", "phone": "0712345601", "gender": "F", "wants": "bed", "institution": "UoN"}
    data.update(over)
    return client.post(reverse("hostel_stay", args=[token]), data)


def test_the_booking_page_is_off_until_turned_on(client, owner, prop):
    hostel.add_rooms(owner, prop, prefix="R", first=1, count=1, capacity=2, gender="F", price=7000)
    assert client.get(reverse("hostel_stay", args=["nope"])).status_code == 404
    hostel.enable_booking_link(owner, prop)
    prop.refresh_from_db()
    page = client.get(reverse("hostel_stay", args=[prop.booking_token]))
    assert page.status_code == 200 and "Beds available" in page.content.decode()
    hostel.disable_booking_link(owner, prop)
    assert client.get(reverse("hostel_stay", args=[prop.booking_token or "x"])).status_code == 404


def test_a_free_bed_makes_a_prospect_not_a_waitlist_entry(client, owner, prop):
    from prospects.models import Prospect

    hostel.add_rooms(owner, prop, prefix="R", first=1, count=1, capacity=2, gender="F")
    hostel.enable_booking_link(owner, prop)
    prop.refresh_from_db()
    assert _book(client, prop.booking_token).status_code == 302
    p = Prospect.objects.get(property=prop)
    assert (p.gender, p.waitlisted, p.wants, p.assigned_to_id) == ("F", False, "Bed", owner.user_id)


def test_no_bed_for_that_gender_puts_the_student_on_the_waitlist(client, owner, prop):
    from prospects import services as prospects

    hostel.add_rooms(owner, prop, prefix="R", first=1, count=1, capacity=2, gender="M")
    hostel.enable_booking_link(owner, prop)
    prop.refresh_from_db()
    resp = _book(client, prop.booking_token)
    assert resp["Location"].endswith("?sent=waitlist")
    assert [p.name for p in prospects.waitlist(prop)] == ["Amina W"]
    # The same phone asking again adds to the same prospect.
    _book(client, prop.booking_token)
    assert len(prospects.waitlist(prop)) == 1


def test_the_form_needs_a_gender_and_the_trap_field_stays_empty(client, owner, prop):
    from prospects.models import Prospect

    hostel.enable_booking_link(owner, prop)
    prop.refresh_from_db()
    assert _book(client, prop.booking_token, gender="").status_code == 400
    _book(client, prop.booking_token, website="http://spam")
    assert not Prospect.objects.filter(property=prop).exists()


def test_waitlist_and_rollover_pages_open_for_staff(client, owner, prop):
    login(client, owner)
    assert client.get(reverse("properties:waitlist", args=[prop.public_id])).status_code == 200
    assert client.get(reverse("properties:rollover", args=[prop.public_id])).status_code == 200
    assert client.post(reverse("properties:booking_link", args=[prop.public_id]), {"action": "on"}).status_code == 302
    prop.refresh_from_db()
    assert prop.booking_token
    assert prop.booking_token in client.get(reverse("properties:rooms", args=[prop.public_id])).content.decode()


def test_rollover_drafts_renewals_for_the_students_who_stay(client, owner, prop):
    from leases.rollover import candidates

    hostel.add_rooms(owner, prop, prefix="R", first=1, count=2, capacity=1, price=8000)
    leases = []
    for i, unit in enumerate(Unit.objects.filter(property=prop).order_by("code")):
        s = tenant_services.create_tenant(owner, name=f"S{i}", phone=f"071234560{i + 1}")
        lease = lease_services.create_lease(owner, unit=unit, tenants=[s], start_date=D(2026, 10, 5),
                                            end_date=D(2027, 1, 31), rent=8000,
                                            billing_frequency=Lease.Frequency.SEMESTER)
        leases.append(lease_services.activate_lease(owner, lease))
    login(client, owner)
    url = reverse("properties:rollover", args=[prop.public_id])
    assert len(candidates(prop)) == 2
    resp = client.post(url, {"start_date": "2027-02-01", "end_date": "2027-05-31", "rent": "8500",
                             "lease": [str(leases[0].public_id)]})
    assert resp.status_code == 302
    draft = Lease.objects.get(previous_lease=leases[0])
    assert (draft.status, draft.start_date, draft.end_date, draft.rent_on(draft.start_date)) == (
        Lease.Status.DRAFT, D(2027, 2, 1), D(2027, 5, 31), 8500)
    assert not Lease.objects.filter(previous_lease=leases[1]).exists()
    # Doing it again for the same student is skipped, not doubled.
    client.post(url, {"start_date": "2027-02-01", "end_date": "2027-05-31", "lease": [str(leases[0].public_id)]})
    assert Lease.objects.filter(previous_lease=leases[0]).count() == 1


def test_ticked_renewal_drafts_are_activated_together(client, owner, prop):
    hostel.add_rooms(owner, prop, prefix="R", first=1, count=2, capacity=1, price=8000)
    leases = []
    for i, unit in enumerate(Unit.objects.filter(property=prop).order_by("code")):
        s = tenant_services.create_tenant(owner, name=f"S{i}", phone=f"071234560{i + 1}")
        lease = lease_services.create_lease(owner, unit=unit, tenants=[s], start_date=D(2026, 10, 5),
                                            end_date=D(2027, 1, 31), rent=8000)
        leases.append(lease_services.activate_lease(owner, lease))
    login(client, owner)
    url = reverse("properties:rollover", args=[prop.public_id])
    client.post(url, {"start_date": "2027-02-01", "end_date": "2027-05-31",
                      "lease": [str(x.public_id) for x in leases]})
    drafts = list(Lease.objects.filter(previous_lease__in=leases))
    assert len(drafts) == 2 and "Renewal drafts waiting" in client.get(url).content.decode()
    resp = client.post(url, {"action": "activate", "lease": [str(drafts[0].public_id)]})
    assert resp.status_code == 302
    drafts[0].refresh_from_db(), drafts[1].refresh_from_db()
    assert (drafts[0].status, drafts[1].status) == (Lease.Status.ACTIVE, Lease.Status.DRAFT)
    client.post(url, {"action": "activate"})  # nothing ticked changes nothing
    drafts[1].refresh_from_db()
    assert drafts[1].status == Lease.Status.DRAFT


def test_a_waiting_student_is_texted_once_when_a_bed_frees_up(client, owner, prop):
    from notifications.models import Message
    from prospects import services as prospects
    from prospects.models import Prospect

    hostel.add_rooms(owner, prop, prefix="R", first=1, count=1, capacity=1, gender="F")
    unit = Unit.objects.get(property=prop)
    t = tenant_services.create_tenant(owner, name="Occupant", phone="0799000001")
    lease = lease_services.create_lease(owner, unit=unit, tenants=[t], start_date=D(2026, 10, 1), rent=8000)
    lease_services.activate_lease(owner, lease)
    hostel.enable_booking_link(owner, prop)
    prop.refresh_from_db()
    _book(client, prop.booking_token, name="Amina W", phone="0712345601")
    _book(client, prop.booking_token, name="Brenda K", phone="0712345602")
    assert Prospect.objects.filter(property=prop, waitlisted=True).count() == 2
    assert prospects.offer_free_beds()["bed_offers"] == 0  # the only bed is taken
    lease_services.end_lease(owner, lease, ended_on=D(2026, 10, 18))
    assert prospects.offer_free_beds(D(2026, 10, 20))["bed_offers"] == 1  # one bed, one offer: the first in line
    assert prospects.offer_free_beds(D(2026, 10, 20))["bed_offers"] == 0
    first = Prospect.objects.get(property=prop, phone="+254712345601")
    assert first.offered_at is not None
    assert Prospect.objects.get(property=prop, phone="+254712345602").offered_at is None
    assert Message.objects.filter(type="hostel_bed_free").count() <= 1


def test_the_json_for_the_room_map_and_waitlist(client, owner, prop):
    from accounts.tests.factories import add_member

    hostel.add_rooms(owner, prop, prefix="R", first=1, count=1, capacity=2, gender="F")
    login(client, owner)
    base = f"/properties/web-api/hostel/{prop.public_id}"
    data = client.get(f"{base}/rooms/").json()
    assert (data["free"], data["taken"], data["booking_url"]) == (2, 0, "")
    assert data["rooms"][0]["code"] == "R1" and len(data["rooms"][0]["units"]) == 2 and data["can_manage"]
    on = client.post(f"{base}/rooms/", {"action": "on"}, content_type="application/json").json()
    assert "/stay/" in on["map"]["booking_url"]
    off = client.post(f"{base}/rooms/", {"action": "off"}, content_type="application/json").json()
    assert off["map"]["booking_url"] == ""
    assert client.post(f"{base}/rooms/", {"action": "x"}, content_type="application/json").status_code == 400
    assert client.get(f"{base}/waitlist/").json()["rows"] == []
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    login(client, caretaker)
    assert client.post(f"{base}/rooms/", {"action": "on"}, content_type="application/json").status_code == 404


def test_the_json_is_not_found_for_a_landlord(client):
    m = make_org("Landlord Co")
    p = make_property(m.organization)
    login(client, m)
    assert client.get(f"/properties/web-api/hostel/{p.public_id}/rooms/").status_code == 404


def _post(client, url, payload):
    return client.post(url, payload, content_type="application/json")


def test_json_forms_add_edit_and_archive_rooms(client, owner, prop):
    login(client, owner)
    base = f"/properties/web-api/hostel/{prop.public_id}"
    form = client.get(f"{base}/rooms/new/").json()
    assert {"prefix", "first", "count", "let", "capacity", "price", "gender"} <= {f["name"] for f in form["fields"]}
    assert next(f for f in form["fields"] if f["name"] == "let")["kind"] == "radio"
    bad = _post(client, f"{base}/rooms/new/", {"values": {"first": "101", "count": "0", "let": "beds", "capacity": "2"}})
    assert bad.status_code == 422 and "count" in bad.json()["fields"]
    ok = _post(client, f"{base}/rooms/new/", {"values": {"prefix": "R", "first": "101", "count": "2", "let": "beds",
                                                         "capacity": "2", "price": "8000", "gender": "F"}})
    assert ok.status_code == 200 and ok.json()["done"] == "added" and Room.objects.filter(property=prop).count() == 2
    clash = _post(client, f"{base}/rooms/new/", {"values": {"prefix": "R", "first": "101", "count": "1", "let": "beds",
                                                            "capacity": "2"}})
    assert clash.status_code == 422
    room = Room.objects.get(property=prop, code="R101")
    url = f"/properties/web-api/hostel/room/{room.public_id}/"
    assert client.get(url).json()["room"]["code"] == "R101"
    assert _post(client, url, {"values": {"gender": "F", "capacity": "3"}}).json()["done"] == "updated"
    assert room.units.count() == 3
    assert _post(client, url, {"action": "archive"}).json()["done"] == "archived"


def test_json_next_term_drafts_then_activates(client, owner, prop):
    hostel.add_rooms(owner, prop, prefix="R", first=1, count=2, capacity=1, price=8000)
    leases = []
    for i, unit in enumerate(Unit.objects.filter(property=prop).order_by("code")):
        s = tenant_services.create_tenant(owner, name=f"S{i}", phone=f"071234560{i + 1}")
        lease = lease_services.create_lease(owner, unit=unit, tenants=[s], start_date=D(2026, 10, 5),
                                            end_date=D(2027, 1, 31), rent=8000)
        leases.append(lease_services.activate_lease(owner, lease))
    login(client, owner)
    url = f"/properties/web-api/hostel/{prop.public_id}/rollover/"
    term = client.get(url).json()
    assert len(term["students"]) == 2 and term["drafts"] == [] and term["can_activate"]
    ids = [str(x.public_id) for x in leases]
    assert _post(client, url, {"action": "draft", "values": {"start_date": "2027-02-01"}, "leases": ids}).status_code == 422
    assert _post(client, url, {"action": "draft", "values": {"start_date": "2027-02-01", "end_date": "2027-05-31"},
                               "leases": []}).status_code == 422
    made = _post(client, url, {"action": "draft", "values": {"start_date": "2027-02-01", "end_date": "2027-05-31"},
                               "leases": ids}).json()
    assert len(made["term"]["drafts"]) == 2
    done = _post(client, url, {"action": "activate", "leases": [made["term"]["drafts"][0]["id"]]}).json()
    assert len(done["term"]["drafts"]) == 1
