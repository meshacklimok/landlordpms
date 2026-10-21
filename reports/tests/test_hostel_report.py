"""The hostel report (D-192)."""

import datetime

import pytest

from accounts.models import Organization
from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from leases import services as lease_services
from leases.models import Lease
from properties import hostel as rooms
from properties.models import Unit
from reports import hostel as report
from reports.tests.test_income import bill
from tenants import services as tenant_services

pytestmark = pytest.mark.django_db

D = datetime.date
TODAY = D(2026, 10, 20)


@pytest.fixture
def owner():
    m = make_org("Campus View")
    m.organization.org_type = Organization.Type.HOSTEL
    m.organization.save(update_fields=["org_type"])
    return m


@pytest.fixture
def prop(owner):
    return make_property(owner.organization, name="Campus View Hostel")


@pytest.fixture
def term(owner, prop):
    """Two women's beds (one taken by Amina, parent Mama Amina, semester unpaid), two men's beds free."""
    rooms.add_rooms(owner, prop, prefix="F", first=1, count=1, capacity=2, gender="F", price=8000)
    rooms.add_rooms(owner, prop, prefix="M", first=1, count=1, capacity=2, gender="M", price=8000)
    student = tenant_services.create_tenant(owner, name="Amina", phone="0712345601",
                                            emergency_contact_name="Mama Amina", emergency_contact_phone="0722000111")
    unit = Unit.objects.get(property=prop, code="F1-A")
    lease = lease_services.create_lease(owner, unit=unit, tenants=[student], start_date=D(2026, 9, 1),
                                        end_date=D(2026, 12, 15), rent=8000, due_day=5, grace_days=3,
                                        billing_frequency=Lease.Frequency.SEMESTER)
    lease = lease_services.activate_lease(owner, lease)
    bill(lease, D(2026, 9, 1))
    return lease


def test_beds_the_term_and_who_owes(owner, prop, term):
    (row,) = report.hostel_report(owner, today=TODAY)
    assert (row.rooms, row.beds, row.taken, row.free) == (2, 4, 1, 3)
    assert row.by_gender["F"][:2] == [2, 1] and row.by_gender["M"][:2] == [2, 0]
    assert row.billed > 0 and row.paid == 0 and row.owed == row.billed  # the last term is cut at the end date
    (owing,) = row.owing
    assert (owing.student, owing.guardian, owing.unit) == ("Amina", "Mama Amina", "F1-A")
    assert [(e[0], e[1]) for e in row.ending] == [("Amina", "F1-A")]


def test_the_waitlist_is_counted_by_gender(client, owner, prop, term):
    from prospects.models import Prospect

    for name, phone, gender in (("Brenda", "+254712345602", "F"), ("Carol", "+254712345603", "M")):
        Prospect.objects.create(organization=owner.organization, property=prop, name=name, phone=phone,
                                gender=gender, waitlisted=True, wants="Bed")
    (row,) = report.hostel_report(owner, today=TODAY)
    assert row.waiting_total == 2 and row.by_gender["F"][2] == 1 and row.by_gender["M"][2] == 1
    assert row.ready == 2  # a free bed suits each


def test_the_page_csv_and_the_report_centre(client, owner, prop, term):
    login(client, owner)
    page = client.get("/reports/hostel/").content.decode()
    assert "Campus View Hostel" in page and "Mama Amina" in page
    assert "Hostel" in client.get("/reports/centre/").content.decode()
    body = client.get("/reports/hostel/?format=csv").content.decode("utf-8-sig")
    assert "Campus View Hostel,2,4,1,3" in body


def test_a_landlord_has_no_hostel_report(client):
    m = make_org("Landlord Co")
    login(client, m)
    assert client.get("/reports/hostel/").status_code == 404


def test_the_page_is_refused_without_financial_figures(client, owner):
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    login(client, caretaker)
    assert client.get("/reports/hostel/").status_code == 403


def test_json_for_the_next_screen(client, owner, prop, term):
    login(client, owner)
    data = client.get("/reports/web-api/hostel/").json()
    (row,) = data["rows"]
    assert (row["beds"], row["taken"], row["free"]) == (4, 1, 3)
    assert row["owing"][0]["guardian"] == "Mama Amina" and data["csv_url"].endswith("format=csv")
    assert client.get(f"/reports/web-api/hostel/?property={prop.public_id}").json()["selected"] == str(prop.public_id)


def test_json_is_hidden_from_landlords_and_caretakers(client, owner):
    login(client, make_org("Landlord Co"))
    assert client.get("/reports/web-api/hostel/").status_code == 404
    client.logout()
    login(client, add_member(owner.organization, "caretaker", all_properties=True))
    assert client.get("/reports/web-api/hostel/").status_code == 404
