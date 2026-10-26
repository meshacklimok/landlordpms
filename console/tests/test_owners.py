"""The owners page, its CSV and the email to owners (D-179). Superusers only."""

import pytest
from django.core import mail
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from audit.models import AuditEvent
from console.models import OwnerEmail
from properties.models import Unit

from .test_console import owner, seat_for, signed_in

pytestmark = pytest.mark.django_db


@pytest.fixture
def two_owners(settings):
    settings.MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}
    big, small = make_org("Big Lets"), make_org("Small Lets")
    big.user.email, big.user.full_name = "big@example.com", "Wanjiku Kamau"
    big.user.save(update_fields=["email", "full_name"])
    small.user.full_name = "Otieno Ouma"
    small.user.save(update_fields=["full_name"])
    prop = make_property(big.organization)
    for n in range(3):
        Unit.objects.create(organization=big.organization, property=prop, code=f"A{n}", payment_reference=f"REF{n}")
    add_member(big.organization, "caretaker")
    return big, small


def test_lists_owners_with_email_team_and_units(client, two_owners):
    owner(client)
    html = client.get(reverse("console:owners")).content.decode()
    assert "big@example.com" in html and "Wanjiku Kamau" in html and "Otieno Ouma" in html
    rows = client.get(reverse("console:owners")).context["page"]
    big = next(r for r in rows if r["org"].name == "Big Lets")
    assert (big["units"], big["team"]) == (3, 1)


def test_filters_narrow_the_list(client, two_owners):
    owner(client)
    page = client.get(reverse("console:owners"), {"email": "has"}).context["page"]
    assert [r["org"].name for r in page] == ["Big Lets"]
    page = client.get(reverse("console:owners"), {"q": "otieno"}).context["page"]
    assert [r["org"].name for r in page] == ["Small Lets"]


def test_csv_download_is_audited(client, two_owners):
    owner(client)
    r = client.get(reverse("console:owners_export"))
    assert r["Content-Type"].startswith("text/csv") and b"big@example.com" in r.content
    assert AuditEvent.objects.filter(action="console.export").exists()


@pytest.mark.parametrize("name", ["owners", "owner_email", "owner_emails", "owners_export"])
def test_console_team_without_superuser_cannot_open(client, name):
    seat = seat_for()
    signed_in(client, seat.user)
    assert client.get(reverse(f"console:{name}")).status_code == 403


def test_a_landlord_gets_404(client, two_owners):
    signed_in(client, two_owners[0].user)
    assert client.get(reverse("console:owners")).status_code == 404


def test_sends_one_email_each_and_keeps_the_record(client, two_owners):
    me = owner(client)
    big, small = two_owners
    small.user.email = "small@example.com"
    small.user.save(update_fields=["email"])
    r = client.post(reverse("console:owner_email"), {"subject": "Hello {name}", "body": "About {organization}.",
                                                       "checked": "on"})
    log = OwnerEmail.objects.get()
    assert r.status_code == 302 and (log.sent, log.failed, log.recipients) == (2, 0, 2)
    assert sorted(m.to[0] for m in mail.outbox) == ["big@example.com", "small@example.com"]
    big_mail = next(m for m in mail.outbox if m.to == ["big@example.com"])
    assert big_mail.subject == "Hello Wanjiku" and "About Big Lets." in big_mail.body
    assert AuditEvent.objects.filter(action="console.owner_email").exists()
    assert me.get(reverse("console:owner_email_detail", args=[log.pk])).status_code == 200


def test_sending_needs_the_tick_and_skips_owners_without_email(client, two_owners):
    owner(client)
    r = client.post(reverse("console:owner_email"), {"subject": "x", "body": "y"})
    assert r.status_code == 200 and not mail.outbox
    client.post(reverse("console:owner_email"), {"subject": "x", "body": "y", "checked": "on"})
    assert [m.to for m in mail.outbox] == [["big@example.com"]]


def test_only_the_ticked_owners_get_it(client, two_owners):
    owner(client)
    big, small = two_owners
    small.user.email = "small@example.com"
    small.user.save(update_fields=["email"])
    client.post(reverse("console:owner_email"), {"subject": "x", "body": "y", "checked": "on",
                                                   "m": [str(small.public_id)]})
    assert [m.to for m in mail.outbox] == [["small@example.com"]]


def test_test_goes_only_to_the_sender(client, two_owners):
    me = make_org().user
    me.is_superuser, me.is_staff, me.email = True, True, "me@example.com"
    me.save(update_fields=["is_superuser", "is_staff", "email"])
    signed_in(client, me)
    client.post(reverse("console:owner_email"), {"subject": "x", "body": "y", "test": "1"})
    assert [m.to for m in mail.outbox] == [["me@example.com"]] and not OwnerEmail.objects.exists()


def test_segments_flag_owners_who_need_attention(client, two_owners):
    owner(client)
    ctx = client.get(reverse("console:owners")).context
    counts = {g["key"]: g["count"] for g in ctx["segments"]}
    assert counts["no_email"] == 1 and counts["near_limit"] == 0
    page = client.get(reverse("console:owners"), {"segment": "no_email"}).context["page"]
    assert [r["org"].name for r in page] == ["Small Lets"]
    # the strip counts everyone, even while one group is selected
    ctx = client.get(reverse("console:owners"), {"segment": "no_email"}).context
    assert {g["key"]: g["count"] for g in ctx["segments"]}["no_email"] == 1


def test_near_limit_uses_the_plan_unit_limit(client, two_owners):
    big, _small = two_owners
    sub = big.organization.subscription
    sub.plan.unit_limit = 3
    sub.plan.save()
    owner(client)
    page = client.get(reverse("console:owners"), {"segment": "near_limit"}).context["page"]
    assert [r["org"].name for r in page] == ["Big Lets"]
    assert page.object_list[0]["limit"] == 3


def test_page_shows_the_joined_trend_and_value(client, two_owners):
    owner(client)
    r = client.get(reverse("console:owners"), {"sort": "value"})
    assert r.status_code == 200 and "joined" in r.context["specs"]
    assert sum(j for _l, j, _a in r.context["trend"]["table"]) == 2
    assert r.context["data"]["value_total"] >= 0


def _pk(org_name):
    from accounts.models import Membership

    return Membership.objects.get(organization__name=org_name, role__is_owner_role=True).public_id


def test_platform_owner_adds_an_owner(client):
    from accounts.models import Membership, User

    owner(client)
    assert client.get(reverse("console:owner_new")).status_code == 200
    r = client.post(reverse("console:owner_new"), {
        "full_name": "Achieng Odhiambo", "phone": "0712345678", "email": "a@example.com",
        "organization": "Achieng Homes", "org_type": "COMPANY"})
    assert r.status_code == 302
    user = User.objects.get(phone="+254712345678")
    assert user.phone_verified and not user.has_usable_password()
    assert Membership.objects.filter(user=user, role__is_owner_role=True, organization__name="Achieng Homes").exists()
    assert AuditEvent.objects.filter(action="console.owner_create").exists()
    # the same phone again is refused
    again = client.post(reverse("console:owner_new"), {
        "full_name": "X", "phone": "0712345678", "organization": "Other", "org_type": "COMPANY"})
    assert again.status_code == 200 and b"already exists" in again.content


def test_owner_page_edits_details(client, two_owners):
    owner(client)
    url = reverse("console:owner", args=[_pk("Small Lets")])
    assert b"Otieno Ouma" in client.get(url).content
    r = client.post(url, {"action": "save", "full_name": "Otieno O.", "phone": "0722000111",
                          "email": "otieno@example.com", "organization": "Small Lets Ltd"})
    assert r.status_code == 302
    m = _pk("Small Lets Ltd")
    assert m
    from accounts.models import User

    u = User.objects.get(phone="+254722000111")
    assert u.full_name == "Otieno O." and u.email == "otieno@example.com"
    assert AuditEvent.objects.filter(action="console.owner_update").exists()


def test_edit_refuses_a_phone_used_by_another(client, two_owners):
    big, _small = two_owners
    owner(client)
    url = reverse("console:owner", args=[_pk("Small Lets")])
    r = client.post(url, {"action": "save", "full_name": "Otieno", "phone": big.user.phone,
                          "organization": "Small Lets"})
    assert r.status_code == 200 and b"Another account uses this phone" in r.content


def test_notes_create_edit_finish_and_delete(client, two_owners):
    from console.models import OwnerNote

    owner(client)
    url = reverse("console:owner", args=[_pk("Big Lets")])
    client.post(url, {"action": "note_add", "body": "Called, wants Paybill help", "follow_up_on": "2020-01-01"})
    note = OwnerNote.objects.get()
    assert note.follow_up_on and note.author
    # an overdue open note puts the owner in the follow-up group
    page = client.get(reverse("console:owners"), {"segment": "follow_up"}).context["page"]
    assert [r["org"].name for r in page] == ["Big Lets"]
    client.post(url, {"action": "note_save", "note": note.pk, "body": "Called twice", "follow_up_on": ""})
    note.refresh_from_db()
    assert note.body == "Called twice" and note.follow_up_on is None
    client.post(url, {"action": "note_done", "note": note.pk})
    note.refresh_from_db()
    assert note.done_at
    client.post(url, {"action": "note_delete", "note": note.pk})
    assert not OwnerNote.objects.exists()


def test_a_note_of_another_organization_is_a_404(client, two_owners):
    from console.models import OwnerNote

    _big, small = two_owners
    other = OwnerNote.objects.create(organization=small.organization, body="private")
    owner(client)
    r = client.post(reverse("console:owner", args=[_pk("Big Lets")]), {"action": "note_delete", "note": other.pk})
    assert r.status_code == 404 and OwnerNote.objects.filter(pk=other.pk).exists()


@pytest.mark.parametrize("route", ["owner_new"])
def test_new_owner_page_is_superuser_only(client, route):
    seat = seat_for()
    signed_in(client, seat.user)
    assert client.get(reverse(f"console:{route}")).status_code == 403


# --- the queue: long lists go out from the job, not the page -------------------------------------------------

def rows_for(n):
    """n owners with email, in the shape `owners.send` takes."""
    out = []
    for i in range(n):
        m = make_org(f"Pilot {i}")
        m.user.email = f"pilot{i}@example.com"
        m.user.save(update_fields=["email"])
        out.append({"user": m.user, "org": m.organization, "email": m.user.email})
    return out


def test_a_long_list_is_queued_and_the_job_sends_it(two_owners):
    from console import owners

    actor = make_org("Platform").user
    log = owners.send(actor, "Hi {name}", "About {organization}", rows_for(owners.INSTANT_MAX + 5), "all owners")
    assert not log.is_done and not mail.outbox and log.recipients == owners.INSTANT_MAX + 5
    assert owners.run_due() == {"tried": owners.INSTANT_MAX + 5, "interrupted": 0}
    log.refresh_from_db()
    assert log.is_done and (log.sent, log.failed) == (owners.INSTANT_MAX + 5, 0)
    assert len(mail.outbox) == owners.INSTANT_MAX + 5 and all(len(m.to) == 1 for m in mail.outbox)
    assert owners.run_due()["tried"] == 0 and len(mail.outbox) == owners.INSTANT_MAX + 5  # nothing is sent twice


def test_the_job_stops_at_its_time_and_carries_on_next_run(two_owners):
    import datetime

    from console import owners

    actor = make_org("Platform").user
    log = owners.send(actor, "x", "y", rows_for(owners.INSTANT_MAX + 2), "all owners")
    assert owners.run_due(budget=datetime.timedelta(0))["tried"] == 0 and not log.deliveries.exclude(state="pending")
    assert owners.run_due()["tried"] == owners.INSTANT_MAX + 2


def test_one_bad_address_does_not_stop_the_rest(two_owners, monkeypatch):
    from django.core.mail.backends.locmem import EmailBackend

    from console import owners

    real = EmailBackend.send_messages

    def flaky(self, messages):
        if messages[0].to == ["pilot1@example.com"]:
            raise OSError("mailbox full")
        return real(self, messages)

    monkeypatch.setattr(EmailBackend, "send_messages", flaky)
    actor = make_org("Platform").user
    log = owners.send(actor, "x", "y", rows_for(3), "all owners")  # short, so sent at once
    log.refresh_from_db()
    assert log.is_done and (log.sent, log.failed) == (2, 1)
    assert log.deliveries.get(state="failed").error == "mailbox full"


def test_an_address_stuck_sending_is_failed_not_sent_again(two_owners):
    import datetime

    from django.utils import timezone

    from console import owners

    actor = make_org("Platform").user
    log = owners.send(actor, "x", "y", rows_for(owners.INSTANT_MAX + 1), "all owners")
    stuck = log.deliveries.first()
    long_ago = timezone.now() - owners.STUCK_AFTER - datetime.timedelta(minutes=1)
    log.deliveries.filter(pk=stuck.pk).update(state="sending", claimed_at=long_ago)
    assert owners.run_due()["interrupted"] == 1
    log.refresh_from_db()
    assert log.is_done and (log.sent, log.failed) == (owners.INSTANT_MAX, 1) and len(mail.outbox) == owners.INSTANT_MAX


def test_follow_ups_page_groups_and_finishes_notes(client, two_owners):
    import datetime

    from django.utils import timezone

    from console.models import OwnerNote

    big, small = two_owners
    today = timezone.localdate()
    day = datetime.timedelta(days=1)
    old = OwnerNote.objects.create(organization=big.organization, body="old", follow_up_on=today - 3 * day)
    OwnerNote.objects.create(organization=small.organization, body="later", follow_up_on=today + 5 * day)
    OwnerNote.objects.create(organization=small.organization, body="whenever")
    owner(client)
    r = client.get(reverse("console:owner_follow_ups"))
    data = r.context["data"]
    assert [n.body for n in data["overdue"]] == ["old"] and len(data["later"]) == 1 and len(data["undated"]) == 1
    assert data["due"] == 1 and data["overdue"][0].owner_pk
    assert b"1 owner follow-up is due" in client.get(reverse("console:overview")).content
    client.post(reverse("console:owner_follow_ups"), {"note": old.pk, "action": "done"})
    old.refresh_from_db()
    assert old.done_at and client.get(reverse("console:owner_follow_ups")).context["data"]["due"] == 0


def test_owner_history_joins_activity_notes_and_emails(client, two_owners):
    from console.models import OwnerNote

    big, _small = two_owners
    owner(client)
    OwnerNote.objects.create(organization=big.organization, body="Spoke on the phone")
    client.post(reverse("console:owner_email"), {"m": [str(_pk("Big Lets"))], "subject": "Hello", "body": "Hi {name}",
                                                 "checked": "on", "send": "1"})
    kinds = {e["kind"] for e in client.get(reverse("console:owner", args=[_pk("Big Lets")])).context["timeline"]}
    assert {"note", "activity"} <= kinds


def test_failures_queue_lists_and_retries(client, monkeypatch):
    from notifications import delivery
    from notifications.models import Message

    big = make_org("Big Lets")
    bad = Message.objects.create(organization=big.organization, type="invoice_issued", channel="SMS",
                                 status="FAILED", to="+254700000000", error="Provider said no", attempts=3)
    owner(client)
    r = client.get(reverse("console:problems"))
    assert r.status_code == 200 and b"Provider said no" in r.content and r.context["total"] == 1
    monkeypatch.setattr(delivery, "send_one", lambda pk, now=None: "SENT")
    client.post(reverse("console:problems"), {"what": "message", "id": bad.pk})
    bad.refresh_from_db()
    assert bad.status == "QUEUED" and bad.attempts == 0
    assert AuditEvent.objects.filter(action="console.retry").exists()
    # a message that is no longer failed is not touched again
    assert client.post(reverse("console:problems"), {"what": "message", "id": bad.pk}).status_code == 302


def test_failures_queue_needs_the_health_capability(client):
    seat = seat_for(capabilities=["overview"])
    signed_in(client, seat.user)
    assert client.get(reverse("console:problems")).status_code == 403


def test_failed_addresses_can_be_tried_again_and_only_those(client, two_owners, monkeypatch):
    from django.core.mail.backends.locmem import EmailBackend

    from console import owners

    real, broken = EmailBackend.send_messages, {"on": True}

    def flaky(self, messages):
        if broken["on"] and messages[0].to == ["pilot1@example.com"]:
            raise OSError("mailbox full")
        return real(self, messages)

    monkeypatch.setattr(EmailBackend, "send_messages", flaky)
    me = owner(client)
    log = owners.send(make_org("Platform").user, "x", "y", rows_for(3), "all owners")
    assert (log.sent, log.failed, len(mail.outbox)) == (2, 1, 2)
    assert "Try the 1 failed again" in me.get(reverse("console:owner_email_detail", args=[log.pk])).content.decode()
    broken["on"] = False
    me.post(reverse("console:owner_email_resend", args=[log.pk]))
    log.refresh_from_db()
    assert not log.is_done
    owners.run_due()
    log.refresh_from_db()
    assert log.is_done and (log.sent, log.failed, len(mail.outbox)) == (3, 0, 3)
    assert AuditEvent.objects.filter(action="console.owner_email_resend").exists()
    assert me.post(reverse("console:owner_email_resend", args=[log.pk])).status_code == 302 and len(mail.outbox) == 3


def test_setup_steps_show_where_an_owner_is_stuck(client, two_owners):
    owner(client)
    rows = {r["org"].name: r for r in client.get(reverse("console:owners")).context["page"]}
    big, small = rows["Big Lets"], rows["Small Lets"]
    assert big["stuck_at"] == "tenant" and big["steps_done"] == 2  # a property and units, no tenant yet
    assert small["stuck_at"] == "property" and small["steps_done"] == 0
    page = client.get(reverse("console:owners"), {"step": "tenant"}).context["page"]
    assert [r["org"].name for r in page] == ["Big Lets"]
    assert b"stuck here" in client.get(reverse("console:owner", args=[_pk("Big Lets")])).content


def test_health_score_ranks_and_bands(client, two_owners):
    from console import owners as o

    owner(client)
    page = client.get(reverse("console:owners"), {"sort": "health"}).context["page"]
    scores = [r["score"] for r in page]
    assert scores == sorted(scores) and all(0 <= s <= 100 for s in scores)
    assert all(sum(p for _l, p, _m in r["score_parts"]) == r["score"] for r in page)
    assert o.band(85) == "healthy" and o.band(55) == "watch" and o.band(10) == "risk"
    assert o.health_score(idle=None, steps_done=0, sub=None, units=0, limit=None, team=0, has_mfa=False)[0] == 0


def test_bulk_note_goes_on_every_ticked_owner(client, two_owners):
    from console.models import OwnerNote

    owner(client)
    both = [str(_pk("Big Lets")), str(_pk("Small Lets"))]
    r = client.post(reverse("console:owner_bulk"), {
        "m": both, "body": "Invite to webinar", "follow_up_on": "2030-01-01", "next": "/console/owners/"})
    assert r.status_code == 302 and r["Location"] == "/console/owners/"
    assert OwnerNote.objects.filter(body="Invite to webinar").count() == 2
    # nothing ticked, an empty note and an outside redirect are all handled
    assert client.post(reverse("console:owner_bulk"), {"body": "x", "next": "https://evil.example"}).status_code == 302
    assert OwnerNote.objects.count() == 2
    r = client.post(reverse("console:owner_bulk"), {"m": both, "body": "", "next": "https://evil.example"})
    assert r["Location"] == reverse("console:owners") and OwnerNote.objects.count() == 2


def test_emailing_the_ticked_needs_a_tick(client, two_owners):
    owner(client)
    r = client.post(reverse("console:owner_email"), {})
    assert r.status_code == 302 and r["Location"] == reverse("console:owners")
    r = client.post(reverse("console:owner_email"), {"m": [str(_pk("Big Lets"))]})
    assert r.status_code == 200 and len(r.context["targets"]) == 1


def test_locations_count_organizations_by_county_and_town(client, two_owners):
    big, small = two_owners
    from properties.models import Property

    Property.objects.filter(organization=big.organization).update(county="NAIROBI", area="kilimani")
    p2 = Property.objects.create(organization=small.organization, name="Lodge", code="L1", county="NAIROBI",
                                 area="Kilimani")
    assert p2.pk
    owner(client)
    places = client.get(reverse("console:owners")).context["places"]
    nairobi = next(c for c in places["counties"] if c["key"] == "NAIROBI")
    assert nairobi["orgs"] == 2 and nairobi["units"] == 3
    assert places["towns"][0]["name"] == "Kilimani" and places["towns"][0]["orgs"] == 2
    page = client.get(reverse("console:owners"), {"county": "NAIROBI"}).context["page"]
    assert len(page) == 2
    assert client.get(reverse("console:owners"), {"county": "MOMBASA"}).context["count"] == 0


def test_the_owner_changes_a_landlords_lost_phone_and_email_and_they_are_signed_out(client):
    from django.contrib.sessions.models import Session
    from django.core import mail
    from django.core.exceptions import PermissionDenied, ValidationError

    from accounts.models import User
    from accounts.tests.factories import make_org, make_user
    from audit.models import AuditEvent
    from console import services as console_services

    boss = make_org(owner=make_user(email="old@example.co.ke", phone="0722000901"))
    org, user = boss.organization, boss.user
    other = make_user(phone="0722000902")
    owner = make_user(is_staff=True, is_superuser=True)
    mail.outbox.clear()
    with pytest.raises(PermissionDenied):
        console_services.change_login_contact(make_user(is_staff=True), org, user, phone="0722000903", reason="checked ID")
    with pytest.raises(ValidationError):
        console_services.change_login_contact(owner, org, user, phone="0722000903", reason="no")
    with pytest.raises(ValidationError):
        console_services.change_login_contact(owner, org, user, phone=other.phone, reason="checked ID card")
    with pytest.raises(ValidationError):
        console_services.change_login_contact(owner, org, other, phone="0722000903", reason="checked ID card")
    client.force_login(user)
    assert Session.objects.count() >= 1
    console_services.change_login_contact(owner, org, user, phone="0722000903", email="new@example.co.ke",
                                          reason="Video call and ID card")
    user = User.objects.get(pk=user.pk)
    assert user.phone.endswith("722000903") and user.phone_verified_at is None
    assert user.email == "new@example.co.ke" and user.email_verified_at is None
    assert not any(str(s.get_decoded().get("_auth_user_id")) == str(user.pk) for s in Session.objects.all())
    assert AuditEvent.objects.filter(action="console.login_contact_changed", organization=org).exists()


def test_the_owner_can_reset_two_step_login_for_a_landlord_who_lost_the_authenticator_too():
    from django.utils import timezone

    from accounts import mfa
    from accounts.models import TOTPDevice
    from accounts.tests.factories import make_org, make_user
    from console import services as console_services

    boss = make_org()
    TOTPDevice.objects.create(user=boss.user, secret="x", confirmed_at=timezone.now())
    assert mfa.is_enabled(boss.user)
    owner = make_user(is_staff=True, is_superuser=True)
    console_services.change_login_contact(owner, boss.organization, boss.user, reset_mfa=True, reason="Video call and ID")
    assert not mfa.is_enabled(boss.user)
    assert AuditEvent.objects.filter(action="mfa.admin_reset", organization=None).exists() or AuditEvent.objects.filter(
        action="console.login_contact_changed", organization=boss.organization).exists()


def test_an_archived_organization_is_not_listed(client, two_owners):
    """Its console page is not there (the live manager hides it), so a link to it would only be a 404."""
    from django.utils import timezone

    big, small = two_owners
    small.organization.archived_at = timezone.now()
    small.organization.save(update_fields=["archived_at"])
    owner(client)
    rows = client.get(reverse("console:owners")).context["page"]
    assert [r["org"].name for r in rows] == ["Big Lets"]
