import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from properties import services as property_services
from properties.models import Unit
from prospects import services
from prospects.models import Prospect, Viewing
from reports.home import home
from reports.tests.test_income import make_lease

pytestmark = pytest.mark.django_db


def detail(p):
    return reverse("prospects:detail", args=[p.public_id])


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------


def test_list_and_detail(client, owner, prospect):
    login(client, owner)
    r = client.get(reverse("prospects:list"))
    assert r.status_code == 200 and "Wanjiku Kamau" in r.text
    r = client.get(detail(prospect))
    assert r.status_code == 200 and "Book a viewing" in r.text and "Make tenant" in r.text


def test_out_of_scope_is_404(client, org, prospect):
    elsewhere = add_member(org, "leasing_agent", properties=[make_property(org)])
    login(client, elsewhere)
    assert client.get(detail(prospect)).status_code == 404
    assert client.post(detail(prospect), {"action": "note", "text": "x"}).status_code == 404
    assert client.get(reverse("prospects:edit", args=[prospect.public_id])).status_code == 404
    assert client.get(reverse("prospects:convert", args=[prospect.public_id])).status_code == 404


def test_other_org_is_404(client, prospect):
    login(client, make_org())
    assert client.get(detail(prospect)).status_code == 404


def test_no_capability_is_denied(client, org, prop):
    caretaker = add_member(org, "caretaker", properties=[prop])
    login(client, caretaker)
    assert client.get(reverse("prospects:list")).status_code == 403
    assert client.get(reverse("prospects:create")).status_code == 403


def test_nav_link(client, owner, org, prop):
    login(client, owner)
    assert reverse("prospects:list") in client.get(reverse("properties:list")).text
    client.logout()
    login(client, add_member(org, "caretaker", properties=[prop]))
    assert reverse("prospects:list") not in client.get(reverse("properties:list")).text


# ---------------------------------------------------------------------------
# Adding
# ---------------------------------------------------------------------------


def test_create_from_a_unit(client, agent, prop, unit):
    login(client, agent)
    r = client.get(reverse("prospects:create") + f"?unit={unit.public_id}")
    assert r.status_code == 200
    r = client.post(reverse("prospects:create"), {
        "property": prop.public_id, "unit": unit.public_id, "name": "Otieno", "phone": "0722111222",
        "source": "PHONE", "assigned_to": agent.user.public_id, "note": "Wants 2 bedrooms"})
    p = Prospect.objects.get(name="Otieno")
    assert r.status_code == 302 and r["Location"] == detail(p)
    assert p.unit == unit and p.assigned_to == agent.user and p.source == "PHONE"


def test_create_warns_about_duplicates(client, owner, prop, prospect):
    login(client, owner)
    data = {"property": prop.public_id, "name": "Wanjiku again", "phone": "+254712345678", "source": "WALK_IN",
            "assigned_to": owner.user.public_id}
    r = client.post(reverse("prospects:create"), data)
    assert r.status_code == 200 and "already an open prospect" in r.text
    assert Prospect.objects.count() == 1
    r = client.post(reverse("prospects:create"), {**data, "confirm_duplicate": "on"})
    assert r.status_code == 302 and Prospect.objects.count() == 2


def test_edit(client, owner, prospect):
    login(client, owner)
    url = reverse("prospects:edit", args=[prospect.public_id])
    assert client.get(url).status_code == 200
    r = client.post(url, {"name": "Wanjiku K", "phone": "0712345678", "source": "REFERRAL", "unit": "",
                          "move_in_by": "2026-11-01"})
    assert r.status_code == 302
    prospect.refresh_from_db()
    assert prospect.name == "Wanjiku K" and prospect.unit is None
    assert prospect.move_in_by == datetime.date(2026, 11, 1)


# ---------------------------------------------------------------------------
# Actions on one prospect
# ---------------------------------------------------------------------------


def test_actions(client, owner, prospect, unit):
    login(client, owner)
    url = detail(prospect)
    today = timezone.localdate()
    client.post(url, {"action": "note", "text": "Called", "contacted": "on",
                      "next_contact_on": (today + datetime.timedelta(days=2)).isoformat()})
    prospect.refresh_from_db()
    assert prospect.status == Prospect.Status.CONTACTED
    when = (timezone.localtime() + datetime.timedelta(days=1)).strftime("%Y-%m-%dT10:00")
    r = client.post(url, {"action": "viewing", "unit": unit.public_id, "scheduled_at": when, "with_user": ""})
    assert r.status_code == 302
    v = prospect.viewings.get()
    client.post(url, {"action": "outcome", "viewing": v.public_id, "status": "DONE", "outcome": "Liked it"})
    v.refresh_from_db()
    assert v.status == Viewing.Status.DONE and v.outcome == "Liked it"
    client.post(url, {"action": "hold", "unit": unit.public_id,
                      "until": (today + datetime.timedelta(days=5)).isoformat()})
    unit.refresh_from_db()
    assert unit.manual_status == Unit.ManualStatus.RESERVED
    r = client.get(reverse("properties:unit", args=[unit.public_id]))
    assert "Held for Wanjiku Kamau" in r.text
    client.post(url, {"action": "release"})
    unit.refresh_from_db()
    assert unit.manual_status == Unit.ManualStatus.NORMAL
    client.post(url, {"action": "lose", "reason": "PRICE", "note": ""})
    prospect.refresh_from_db()
    assert prospect.status == Prospect.Status.LOST
    client.post(url, {"action": "reopen"})
    prospect.refresh_from_db()
    assert prospect.status == Prospect.Status.VIEWED


def test_hold_too_long_shows_the_error(client, owner, prospect, unit):
    login(client, owner)
    r = client.post(detail(prospect), {"action": "hold", "unit": unit.public_id,
                                       "until": (timezone.localdate() + datetime.timedelta(days=40)).isoformat()})
    assert r.status_code == 200 and "at most 30 days" in r.text


def test_archived_property_is_read_only(client, owner, prop, prospect):
    prop.archive(by=owner.user)
    login(client, owner)
    r = client.get(detail(prospect))
    assert r.status_code == 200 and "Book a viewing" not in r.text
    assert client.post(detail(prospect), {"action": "note", "text": "x"}).status_code == 404


# ---------------------------------------------------------------------------
# Becoming a tenant
# ---------------------------------------------------------------------------


def test_convert_goes_on_to_the_lease(client, owner, prospect, unit):
    login(client, owner)
    url = reverse("prospects:convert", args=[prospect.public_id])
    assert client.get(url).status_code == 200
    r = client.post(url, {"tenant": "new"})
    prospect.refresh_from_db()
    assert prospect.status == Prospect.Status.CONVERTED
    assert r["Location"] == (reverse("leases:create", args=[unit.public_id])
                             + f"?tenant={prospect.tenant.public_id}")
    assert client.get(r["Location"]).status_code == 200


def test_convert_offers_the_existing_tenant(client, owner, prop):
    lease = make_lease(owner, prop, code="A1")
    tenant = lease.lease_tenants.get().tenant
    p = services.create_prospect(owner, prop, name="Same person", phone=tenant.phone)
    login(client, owner)
    url = reverse("prospects:convert", args=[p.public_id])
    assert tenant.name in client.get(url).text
    r = client.post(url, {"tenant": tenant.public_id})
    p.refresh_from_db()
    assert p.tenant == tenant
    assert r["Location"] == reverse("tenants:detail", args=[tenant.public_id])


# ---------------------------------------------------------------------------
# The public vacancy page
# ---------------------------------------------------------------------------


@pytest.fixture
def shared(owner, unit):
    property_services.share_unit(owner, unit)
    unit.refresh_from_db()
    return reverse("vacancy", args=[unit.share_token])


def test_vacancy_enquiry(client, shared, unit, owner):
    assert "leave your number" in client.get(shared).text
    r = client.post(shared, {"name": "Otieno", "phone": "0722111222", "message": "Still free?", "website": ""})
    assert r.status_code == 302 and r["Location"].endswith("?sent=1")
    p = Prospect.objects.get()
    assert p.source == Prospect.Source.VACANCY_LINK and p.assigned_to == owner.user and p.unit == unit
    assert "Thank you" in client.get(r["Location"]).text


def test_vacancy_enquiry_errors_and_honeypot(client, shared):
    r = client.post(shared, {"name": "", "phone": "12"})
    assert r.status_code == 400
    r = client.post(shared, {"name": "Bot", "phone": "0722111222", "website": "http://spam"})
    assert r.status_code == 302 and not Prospect.objects.exists()


def test_vacancy_enquiry_rate_limit(client, shared):
    for i in range(5):
        client.post(shared, {"name": f"P{i}", "phone": f"07221112{i:02d}"})
    r = client.post(shared, {"name": "P9", "phone": "0722111299"})
    assert r.status_code == 429
    assert Prospect.objects.count() == 5


def test_no_enquiry_form_when_let(client, shared, owner, unit):
    property_services.set_unit_status(owner, unit, Unit.ManualStatus.INACTIVE)
    assert "leave your number" not in client.get(shared).text
    client.post(shared, {"name": "Otieno", "phone": "0722111222"})
    assert not Prospect.objects.exists()


# ---------------------------------------------------------------------------
# Home tasks
# ---------------------------------------------------------------------------


def test_home_tasks(owner, prospect, unit):
    today = timezone.localdate()
    services.book_viewing(owner, prospect, unit=unit,
                          scheduled_at=timezone.localtime().replace(hour=23, minute=0, second=0, microsecond=0))
    services.hold_unit(owner, prospect, unit=unit, until=today)
    other = services.create_prospect(owner, prospect.property, name="B", phone="0712000002")
    tasks = {t.key: t.count for t in home(owner, today + datetime.timedelta(days=1)).tasks}
    assert tasks["prospects_call"] == 1  # the new one; the first was reached when the viewing was booked
    assert tasks["holds"] == 1
    tasks = {t.key: t.count for t in home(owner, today).tasks}
    assert tasks["viewings"] == 1 and "holds" not in tasks
    assert other.status == Prospect.Status.NEW


# ---------------------------------------------------------------------------
# Charts (D-140)
# ---------------------------------------------------------------------------


def test_pipeline_counts_open_stages_and_recent_sources(owner, prospect):
    flow = services.pipeline(services.visible_prospects(owner))
    assert dict(flow["stages"])["New"] == 1 and sum(n for _label, n in flow["stages"]) == 1
    assert flow["sources"] == [(str(Prospect.Source(prospect.source).label), 1)] and flow["lost"] == []


def test_list_draws_the_pipeline_only_when_there_is_something_to_show(client, owner, prospect):
    login(client, owner)
    r = client.get(reverse("prospects:list"))
    assert 'data-chart="chart-stages"' in r.text and 'id="chart-sources"' in r.text
    assert "chart-lost" not in r.text  # nobody lost, so no empty chart


def test_list_has_no_empty_charts(client, owner):
    login(client, owner)
    r = client.get(reverse("prospects:list"))
    assert r.status_code == 200 and "data-chart" not in r.text
