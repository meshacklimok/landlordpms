"""Announcements: who receives them, the preview, sending once, and the pages (D-044 item 15)."""

import datetime
import uuid
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from audit.models import AuditEvent
from billing.tests.test_invoicing import FEB, JAN, bill
from leases import services as lease_services
from leases.models import Lease
from notifications import announcements, catalog, sms_hooks
from notifications.announcements import Audience
from notifications.models import Announcement, Message
from properties import services as property_services
from tenants import services as tenant_services

pytestmark = pytest.mark.django_db

D = datetime.date
EAT = ZoneInfo("Africa/Nairobi")
TODAY = D(2026, 2, 20)
Status = Message.Status
_phones = iter(range(10, 99))


@pytest.fixture
def owner():
    return make_org(name="Acme")


@pytest.fixture
def org(owner):
    return owner.organization


@pytest.fixture
def prop(org):
    return make_property(org, "Riverside")


def tenant(owner, name):
    return tenant_services.create_tenant(owner, name=name, phone=f"07000000{next(_phones)}")


def lease_on(owner, prop, code, *names, building=None, activate=True, start=JAN, **kw):
    unit = property_services.create_unit(owner, prop, code=code, building=building)
    people = [tenant(owner, n) for n in names]
    lease = lease_services.create_lease(owner, unit=unit, tenants=people, start_date=start, rent=10000,
                                        due_day=5, grace_days=3, **kw)
    return lease_services.activate_lease(owner, lease) if activate else lease


def names(recipients):
    return [r.tenant.name for r in recipients]


# ---------------------------------------------------------------------------
# Audience
# ---------------------------------------------------------------------------


def test_everyone_living_in_a_unit_today(owner, prop):
    lease_on(owner, prop, "A1", "Amina", "Baraka")
    lease_on(owner, prop, "A2", "Chege", activate=False)  # draft
    lease_on(owner, prop, "A3", "Dama", start=D(2026, 6, 1))  # not moved in yet
    everyone = announcements.recipients(owner, Audience(), TODAY)
    assert names(everyone) == ["Amina", "Baraka"]
    assert names(announcements.recipients(owner, Audience(include_co_tenants=False), TODAY)) == ["Amina"]


def test_one_message_per_tenant_on_two_leases(owner, prop):
    first = lease_on(owner, prop, "A1", "Amina")
    unit = property_services.create_unit(owner, prop, code="A2")
    second = lease_services.create_lease(owner, unit=unit, tenants=[first.primary_tenant], start_date=JAN,
                                         rent=5000)
    lease_services.activate_lease(owner, second)
    assert names(announcements.recipients(owner, Audience(), TODAY)) == ["Amina"]


def test_filter_by_property_and_building(owner, org, prop):
    block = property_services.create_building(owner, prop, name="Block B")
    other = make_property(org, "Hillview")
    lease_on(owner, prop, "A1", "Amina")
    lease_on(owner, prop, "B1", "Baraka", building=block)
    lease_on(owner, other, "H1", "Chege")
    assert names(announcements.recipients(owner, Audience(properties=(other,)), TODAY)) == ["Chege"]
    assert names(announcements.recipients(owner, Audience(buildings=(block,)), TODAY)) == ["Baraka"]


def test_filter_by_money_owed(owner, prop):
    owing = lease_on(owner, prop, "A1", "Amina")
    paid_up = lease_on(owner, prop, "A2", "Baraka")
    bill(owing, FEB, today=D(2026, 1, 27))  # due 5 Feb
    assert names(announcements.recipients(owner, Audience(owing_days=1), TODAY)) == ["Amina"]
    assert names(announcements.recipients(owner, Audience(owing_days=16), TODAY)) == []
    assert names(announcements.recipients(owner, Audience(owing_days=1), D(2026, 2, 4))) == []  # not due yet
    assert paid_up


def test_filter_by_lease_ending(owner, prop):
    ending = lease_on(owner, prop, "A1", "Amina")
    lease_on(owner, prop, "A2", "Baraka")
    Lease.objects.filter(pk=ending.pk).update(end_date=TODAY + datetime.timedelta(days=30))
    assert names(announcements.recipients(owner, Audience(ending_within=30), TODAY)) == ["Amina"]
    assert names(announcements.recipients(owner, Audience(ending_within=29), TODAY)) == []


def test_scoped_member_reaches_only_their_properties(owner, org, prop):
    other = make_property(org, "Hillview")
    lease_on(owner, prop, "A1", "Amina")
    lease_on(owner, other, "H1", "Chege")
    manager = add_member(org, "manager", properties=[prop])
    assert names(announcements.recipients(manager, Audience(), TODAY)) == ["Amina"]
    with pytest.raises(ValidationError):
        announcements.preview(manager, "Hi", Audience(properties=(other,)), TODAY)


def test_needs_send_bulk(org, prop, owner):
    caretaker = add_member(org, "caretaker", all_properties=True)
    with pytest.raises(PermissionDenied):
        announcements.preview(caretaker, "Hi", Audience(), TODAY)


def test_summary_describes_the_audience(org, prop):
    block = type("B", (), {"name": "Block B", "pk": 1})()
    audience = Audience(properties=(prop,), buildings=(block,), owing_days=30, ending_within=60,
                        include_co_tenants=False)
    assert audience.summary() == (
        "Riverside · Block B · owing 30+ days · lease ending within 60 days · main tenants only")
    assert Audience().summary() == "All properties"


# ---------------------------------------------------------------------------
# Text and preview
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text, error", [
    ("", "Write the message"), ("Hi {balance}", "Unknown fields"), ("x" * 459, "Keep it under"),
    ("Hi {tenant_name.__class__}", "not a valid field"),
])
def test_text_is_checked(org, text, error):
    with pytest.raises(ValidationError, match=error):
        announcements.validate_text(org, text)


def test_preview_personalises_counts_and_skips(owner, prop, settings):
    settings.SMS_PRICE_ESTIMATE = "1.00"
    lease_on(owner, prop, "A1", "Amina")
    stopped = lease_on(owner, prop, "A2", "Baraka")
    sms_hooks.inbound(stopped.primary_tenant.phone, "STOP")
    result = announcements.preview(owner, "Hello {tenant_name} of {unit}, water is off.", Audience(), TODAY)
    assert result.sending == 1 and result.parts == 1 and result.cost == Decimal("1.00")
    amina, baraka = result.recipients
    assert amina.body == "Hello Amina of A1, water is off. Acme"
    assert baraka.skip_reason == Message.SkipReason.NO_CONSENT and baraka.body == ""
    assert result.skipped == {Message.SkipReason.NO_CONSENT: 1}
    assert not Message.objects.exists()


def test_long_text_counts_more_parts(owner, prop):
    lease_on(owner, prop, "A1", "Amina")
    result = announcements.preview(owner, "x" * 200, Audience(), TODAY)
    assert result.parts == 2


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def test_send_writes_one_message_each_and_sends_small_batches(owner, org, prop, outbox,
                                                              django_capture_on_commit_callbacks):
    lease_on(owner, prop, "A1", "Amina", "Baraka")
    with django_capture_on_commit_callbacks(execute=True):
        a, created = announcements.send(owner, "Water off {unit}.", Audience(), today=TODAY,
                                        now=datetime.datetime(2026, 2, 20, 12, tzinfo=EAT))
    assert created and a.recipient_count == 2 and a.summary == "All properties"
    msgs = list(a.messages.order_by("tenant__name"))
    assert [m.body for m in msgs] == ["Water off A1. Acme", "Water off A1. Acme"]
    assert all(m.type == "announcement" and m.status == Status.SENT and m.lease for m in msgs)
    assert len(outbox) == 2
    assert AuditEvent.objects.filter(action="announcement.sent", organization=org).exists()


def test_same_form_twice_sends_once(owner, prop):
    lease_on(owner, prop, "A1", "Amina")
    nonce = uuid.uuid4()
    first, created = announcements.send(owner, "Hi", Audience(), nonce=nonce, today=TODAY)
    again, created_again = announcements.send(owner, "Hi", Audience(), nonce=nonce, today=TODAY)
    assert created and not created_again and first == again
    assert Message.objects.count() == 1


def test_another_orgs_nonce_is_refused(owner, prop):
    lease_on(owner, prop, "A1", "Amina")
    nonce = uuid.uuid4()
    announcements.send(owner, "Hi", Audience(), nonce=nonce, today=TODAY)
    stranger = make_org()
    with pytest.raises(ValidationError):
        announcements.send(stranger, "Hi", Audience(), nonce=nonce, today=TODAY)


def test_nobody_matches(owner, prop):
    with pytest.raises(ValidationError, match="No tenants match"):
        announcements.send(owner, "Hi", Audience(), today=TODAY)
    assert not Announcement.objects.exists()


def test_quiet_hours_wait_unless_urgent(owner, prop):
    lease_on(owner, prop, "A1", "Amina")
    night = datetime.datetime(2026, 2, 20, 22, 30, tzinfo=EAT)
    a, _ = announcements.send(owner, "Hi", Audience(), today=TODAY, now=night)
    assert a.messages.get().send_after == datetime.datetime(2026, 2, 21, 7, tzinfo=EAT)
    b, _ = announcements.send(owner, "Burst pipe, water off now", Audience(), urgent=True, today=TODAY, now=night)
    assert b.urgent and b.messages.get().send_after is None


def test_large_batches_wait_for_the_job(owner, prop, monkeypatch, outbox, django_capture_on_commit_callbacks):
    monkeypatch.setattr(announcements, "BULK_INLINE", 1)
    lease_on(owner, prop, "A1", "Amina", "Baraka")
    with django_capture_on_commit_callbacks(execute=True):
        a, _ = announcements.send(owner, "Hi", Audience(), today=TODAY)
    assert not outbox and set(a.messages.values_list("status", flat=True)) == {Status.QUEUED}


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def form_data(**kw):
    data = {"text": "Water off on Friday.", "include_co_tenants": "on", "nonce": str(uuid.uuid4()),
            "ending_within": "60", "owing_days": "1"}
    data.update(kw)
    return data


def test_pages_render_and_preview_then_send(client, owner, prop):
    lease_on(owner, prop, "A1", "Amina")
    login(client, owner)
    new = reverse("notifications:announcement_new")
    assert client.get(reverse("notifications:announcements")).status_code == 200
    assert client.get(new).status_code == 200

    r = client.post(new, form_data(action="preview"))
    assert r.status_code == 200 and r.context["preview"].sending == 1
    assert "Water off on Friday. Acme" in r.content.decode()
    assert not Announcement.objects.exists()

    data = form_data(action="send")
    r = client.post(new, data)
    a = Announcement.objects.get()
    assert r.status_code == 302 and r.url == reverse("notifications:announcement", args=[a.public_id])
    assert client.post(new, data).status_code == 302  # double submit
    assert Announcement.objects.count() == 1
    r = client.get(r.url)
    assert r.status_code == 200 and r.context["counts"]["all"] == 1
    assert client.get(r.wsgi_request.path + "?status=waiting").status_code == 200


def test_invalid_form_and_no_match_show_errors(client, owner, prop):
    login(client, owner)
    new = reverse("notifications:announcement_new")
    assert client.post(new, form_data(text="", action="preview")).status_code == 400
    r = client.post(new, form_data(action="send"))
    assert r.status_code == 400 and "No tenants match" in r.content.decode()
    r = client.post(new, form_data(text="Hi {balance}", action="preview"))
    assert r.status_code == 400 and "Unknown fields" in r.content.decode()


def test_owing_filter_needs_invoices_view(client, owner, prop, monkeypatch):
    login(client, owner)
    assert "owing" in client.get(reverse("notifications:announcement_new")).context["form"].fields
    monkeypatch.setattr(announcements, "can_target_owing", lambda m: False)
    assert "owing" not in client.get(reverse("notifications:announcement_new")).context["form"].fields
    with pytest.raises(ValidationError):
        announcements.preview(owner, "Hi", Audience(owing_days=1), TODAY)


def test_scoped_members_see_their_own_announcements(client, owner, org, prop):
    lease_on(owner, prop, "A1", "Amina")
    mine, _ = announcements.send(owner, "Owner notice", Audience(), today=TODAY)
    manager = add_member(org, "manager", properties=[prop])
    theirs, _ = announcements.send(manager, "Manager notice", Audience(), today=TODAY)
    login(client, manager)
    listed = list(client.get(reverse("notifications:announcements")).context["page"])
    assert listed == [theirs]
    assert client.get(reverse("notifications:announcement", args=[mine.public_id])).status_code == 404
    assert client.get(reverse("notifications:announcement", args=[theirs.public_id])).status_code == 200


def test_members_without_send_bulk_are_kept_out(client, org):
    caretaker = add_member(org, "caretaker", all_properties=True)
    login(client, caretaker)
    for name in ("notifications:announcements", "notifications:announcement_new"):
        assert client.get(reverse(name)).status_code in (403, 404)


def test_announcement_type_catalog_fields_cover_the_filled_context():
    assert set(announcements.FIELDS) <= set(catalog.get("announcement").placeholders)
