import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from accounts import mfa
from accounts.tests.factories import make_org, make_user
from accounts.tests.test_mfa import enable
from audit.models import AuditEvent
from console import access, services
from console.models import PlatformStaff

pytestmark = pytest.mark.django_db

PAGES = ["overview", "revenue", "behaviour", "usage", "organizations", "health", "team"]


def seat_for(role=PlatformStaff.Role.ANALYST, capabilities=None, **user_kw):
    user = make_user(**user_kw)
    return PlatformStaff.objects.create(user=user, role=role,
                                        capabilities=capabilities or access.ROLE_PRESETS[role])


def signed_in(client, user, *, two_step=True):
    if two_step:
        enable(user)
    client.force_login(user)
    if two_step:
        session = client.session
        session[mfa.VERIFIED] = True
        session.save()
    return client


def owner(client):
    return signed_in(client, make_user(is_superuser=True, is_staff=True))


def test_no_seat_gets_a_404(client):
    signed_in(client, make_user())
    assert client.get(reverse("console:overview")).status_code == 404


def test_a_landlord_owner_is_not_on_the_team(client):
    membership = make_org()
    signed_in(client, membership.user)
    assert client.get(reverse("console:team")).status_code == 404


def test_signed_out_goes_to_login(client):
    assert client.get(reverse("console:overview")).status_code in (302, 404)


def test_seat_without_two_step_is_sent_to_set_it_up(client):
    seat = seat_for()
    signed_in(client, seat.user, two_step=False)
    r = client.get(reverse("console:overview"))
    assert r.status_code == 302 and r.url == reverse("accounts:security")


def test_seat_with_two_step_but_unverified_session_is_asked_for_the_code(client):
    seat = seat_for()
    enable(seat.user)
    client.force_login(seat.user)
    r = client.get(reverse("console:overview"))
    assert r.status_code == 302 and r.url.startswith(reverse("accounts:login_mfa"))


def test_only_the_chosen_pages_open(client):
    seat = seat_for(capabilities=["overview"])
    signed_in(client, seat.user)
    assert client.get(reverse("console:overview")).status_code == 200
    assert client.get(reverse("console:revenue")).status_code == 403
    assert client.get(reverse("console:team")).status_code == 403


def test_a_switched_off_seat_loses_access(client):
    seat = seat_for()
    seat.is_active = False
    seat.save()
    signed_in(client, seat.user)
    assert client.get(reverse("console:overview")).status_code == 404


@pytest.mark.parametrize("page", PAGES)
def test_every_page_opens_on_an_empty_platform(client, page):
    owner(client)
    assert client.get(reverse(f"console:{page}")).status_code == 200


@pytest.mark.parametrize("page", PAGES)
def test_every_page_opens_with_an_organization(client, page):
    make_org("Acme Homes")
    owner(client)
    assert client.get(reverse(f"console:{page}")).status_code == 200


def test_organization_page_and_export(client):
    membership = make_org("Acme Homes")
    owner(client)
    r = client.get(reverse("console:organization", args=[membership.organization.public_id]))
    assert r.status_code == 200 and b"Acme Homes" in r.content
    r = client.get(reverse("console:organizations_export"))
    assert r.status_code == 200 and r["Content-Type"].startswith("text/csv") and b"Acme Homes" in r.content
    assert AuditEvent.objects.filter(action="console.export").exists()


def test_nav_link_shows_only_for_seats(client):
    signed_in(client, make_user(), two_step=False)
    assert b"/console/" not in client.get(reverse("accounts:security")).content
    owner_client = owner(client)
    assert b"/console/" in owner_client.get(reverse("accounts:security")).content


def test_adding_a_person_by_phone():
    boss = make_user(is_superuser=True)
    other = make_user()
    seat = services.add(boss, phone=other.phone, role=PlatformStaff.Role.FINANCE, capabilities=[])
    assert set(seat.capabilities) == set(access.ROLE_PRESETS[PlatformStaff.Role.FINANCE])
    assert AuditEvent.objects.filter(action="console.staff_added").exists()


def test_unknown_phone_and_duplicates_are_refused():
    boss = make_user(is_superuser=True)
    with pytest.raises(ValidationError):
        services.add(boss, phone="+254700000000", role=PlatformStaff.Role.ANALYST, capabilities=[])
    seat = seat_for()
    with pytest.raises(ValidationError):
        services.add(boss, phone=seat.user.phone, role=PlatformStaff.Role.ANALYST, capabilities=[])


def test_a_team_manager_cannot_grant_more_than_they_hold_or_make_owners():
    manager = seat_for(role=PlatformStaff.Role.ANALYST, capabilities=["overview", "staff"])
    target = make_user()
    with pytest.raises(ValidationError):
        services.add(manager.user, phone=target.phone, role=PlatformStaff.Role.ANALYST, capabilities=["revenue"])
    with pytest.raises(ValidationError):
        services.add(manager.user, phone=target.phone, role=PlatformStaff.Role.OWNER, capabilities=["overview"])


def test_without_the_staff_page_you_cannot_add_anyone():
    analyst = seat_for(capabilities=["overview"])
    with pytest.raises(PermissionDenied):
        services.add(analyst.user, phone=make_user().phone, role=PlatformStaff.Role.ANALYST, capabilities=["overview"])


def test_nobody_edits_their_own_seat():
    manager = seat_for(capabilities=["overview", "staff"])
    with pytest.raises(ValidationError):
        services.update(manager.user, manager, role=manager.role, capabilities=["overview"], is_active=False)


def test_updating_another_seat_is_audited():
    boss = make_user(is_superuser=True)
    seat = seat_for()
    services.update(boss, seat, role=PlatformStaff.Role.SUPPORT, capabilities=["health"], is_active=False)
    seat.refresh_from_db()
    assert seat.role == PlatformStaff.Role.SUPPORT and seat.capabilities == ["health"] and not seat.is_active
    assert AuditEvent.objects.filter(action="console.staff_changed").exists()


def test_team_pages_add_and_change_through_the_forms(client):
    owner(client)
    other = make_user()
    r = client.post(reverse("console:team_add"), {"phone": other.phone, "role": "SUPPORT", "capabilities": ["health"]})
    assert r.status_code == 302
    seat = PlatformStaff.objects.get(user=other)
    assert client.get(reverse("console:team_edit", args=[seat.pk])).status_code == 200
    assert client.get(reverse("console:team")).status_code == 200


def test_pages_with_real_payments(client):
    import datetime

    from accounts.tests.factories import make_property
    from billing import invoicing
    from leases import services as lease_services
    from payments import services as payment_services
    from properties import services as property_services
    from tenants import services as tenant_services

    membership = make_org("Busy Homes")
    prop = make_property(membership.organization, name="Acacia Court")
    today = datetime.date.today()
    start = (today.replace(day=1) - datetime.timedelta(days=70)).replace(day=1)
    unit = property_services.create_unit(membership, prop, code="A1")
    tenant = tenant_services.create_tenant(membership, name="Hidden Tenant", phone="0712000001")
    lease = lease_services.create_lease(membership, unit=unit, tenants=[tenant], start_date=start, end_date=None,
                                        rent=20000, due_day=5, grace_days=3)
    lease = lease_services.activate_lease(membership, lease)
    invoicing.generate_lease_period(lease, start, today=start - datetime.timedelta(days=5))
    payment_services.record_payment(membership, lease, amount=20000, method="CASH", paid_at=start + datetime.timedelta(days=9))
    owner(client)
    for page in PAGES:
        assert client.get(reverse(f"console:{page}")).status_code == 200, page
    r = client.get(reverse("console:organization", args=[membership.organization.public_id]))
    assert r.status_code == 200 and b"Hidden Tenant" not in r.content
    assert b"Hidden Tenant" not in client.get(reverse("console:organizations_export")).content


def test_watchlist_flags_a_silent_organization(client):
    from console import metrics

    membership = make_org("Quiet Homes")
    org = membership.organization
    org.subscription.status = "PAST_DUE"
    org.subscription.save()
    rows = metrics.watchlist()
    row = next(r for r in rows if r["org"].pk == org.pk)
    assert row["risk"] >= 3 and any("due" in t for t in row["reasons"])
    owner(client)
    r = client.get(reverse("console:watchlist"))
    assert r.status_code == 200 and b"Quiet Homes" in r.content
    seat = seat_for(capabilities=["overview"])
    signed_in(client, seat.user)
    assert client.get(reverse("console:watchlist")).status_code == 403


def test_opening_a_page_records_when_the_seat_was_last_seen(client):
    seat = seat_for()
    signed_in(client, seat.user)
    assert client.get(reverse("console:overview")).status_code == 200
    seat.refresh_from_db()
    assert seat.last_seen_at is not None


def test_owner_controls_change_status_and_trial_and_are_audited(client):
    membership = make_org("Shaky Homes")
    org = membership.organization
    owner(client)
    url = reverse("console:organization_control", args=[org.public_id])
    client.post(url, {"action": "status", "status": "FROZEN", "reason": "Chargeback fraud"})
    org.refresh_from_db()
    assert org.status == "FROZEN"
    assert AuditEvent.objects.filter(action="console.organization_status", organization=org).exists()
    before = org.subscription.trial_ends_on
    client.post(url, {"action": "trial", "days": "14", "reason": "Pilot is slow to start"})
    org.subscription.refresh_from_db()
    assert org.subscription.trial_ends_on != before
    assert AuditEvent.objects.filter(action="console.trial_extended", organization=org).exists()
    client.post(url, {"action": "status", "status": "ACTIVE", "reason": "x"})  # reason too short
    org.refresh_from_db()
    assert org.status == "FROZEN"


def test_controls_are_for_superusers_only(client):
    membership = make_org("Safe Homes")
    org = membership.organization
    seat = seat_for(role=PlatformStaff.Role.OWNER)
    signed_in(client, seat.user)
    assert client.post(reverse("console:organization_control", args=[org.public_id]),
                       {"action": "status", "status": "FROZEN", "reason": "No right"}).status_code == 403
    assert client.get(reverse("console:audit")).status_code == 403
    org.refresh_from_db()
    assert org.status == "ACTIVE"
    assert b"Owner controls" not in client.get(reverse("console:organization", args=[org.public_id])).content


def test_an_announcement_shows_to_signed_in_people_until_switched_off(client):
    owner(client)
    client.post(reverse("console:announcements"), {"message": "Maintenance tonight at 11pm", "level": "warning"})
    from console.models import PlatformAnnouncement

    item = PlatformAnnouncement.objects.get()
    other = signed_in(client.__class__(), make_user(), two_step=False)
    assert b"Maintenance tonight" in other.get(reverse("accounts:security")).content
    client.post(reverse("console:announcements"), {"toggle": item.pk})
    assert b"Maintenance tonight" not in other.get(reverse("accounts:security")).content


def test_notices_need_the_notices_page(client):
    signed_in(client, seat_for(role=PlatformStaff.Role.ANALYST).user)
    assert client.get(reverse("console:announcements")).status_code == 403
    assert client.post(reverse("console:announcements"), {"message": "x", "level": "info"}).status_code == 403


def test_a_notice_lasts_a_month_can_be_edited_and_goes_to_chosen_organizations(client):
    import datetime

    from django.utils import timezone

    from console.models import PlatformAnnouncement

    org = make_org("Chosen Homes").organization
    owner(client)
    r = client.post(reverse("console:announcements"),
                    {"message": "Hello Chosen", "level": "info", "organizations": [org.pk]})
    assert r.status_code == 302, r.context["form"].errors
    item = PlatformAnnouncement.objects.get()
    assert list(item.organizations.all()) == [org]
    assert abs(item.ends_at - (timezone.now() + datetime.timedelta(days=30))) < datetime.timedelta(minutes=5)
    assert b"Hello Chosen" in client.get(reverse("console:announcements") + f"?edit={item.pk}").content
    client.post(reverse("console:announcements"),
                {"edit": item.pk, "message": "Hello again", "level": "warning"})
    item.refresh_from_db()
    assert item.message == "Hello again" and item.level == "warning" and item.organizations.count() == 0


def test_audit_log_filters(client):
    make_org("Logged Homes")
    owner(client)
    r = client.get(reverse("console:audit"), {"action": "organization.create"})
    assert r.status_code == 200 and b"organization.create" in r.content


NEW_PAGES = ["growth", "channels", "money"]


def test_new_pages_open_when_empty(client):
    owner(client)
    for name in NEW_PAGES + ["behaviour"]:
        assert client.get(reverse(f"console:{name}")).status_code == 200, name


def test_new_pages_open_with_an_organization(client):
    make_org()
    owner(client)
    for name in NEW_PAGES:
        assert client.get(reverse(f"console:{name}")).status_code == 200, name


def _expense_post(**extra):
    data = {"spent_on": "2026-10-01", "category": "HOSTING", "vendor": "DigitalOcean",
            "amount": "1500", "repeat": "MONTHLY", "note": "droplet"}
    data.update(extra)
    return data


def test_expense_add_change_delete_are_audited(client):
    from console.models import PlatformExpense
    owner(client)
    r = client.post(reverse("console:money"), _expense_post())
    assert r.status_code == 302
    e = PlatformExpense.objects.get()
    assert AuditEvent.objects.filter(action="console.expense_added").exists()
    r = client.post(reverse("console:expense", args=[e.pk]), _expense_post(amount="2000"))
    assert r.status_code == 302
    e.refresh_from_db()
    assert e.amount == 2000
    assert AuditEvent.objects.filter(action="console.expense_changed").exists()
    assert client.get(reverse("console:expense", args=[e.pk])).status_code == 200
    r = client.post(reverse("console:expense", args=[e.pk]), {"delete": "1"})
    assert r.status_code == 302
    assert not PlatformExpense.objects.exists()
    assert AuditEvent.objects.filter(action="console.expense_deleted").exists()


def test_money_needs_the_finance_capability(client):
    seat = seat_for(capabilities=["overview"])
    signed_in(client, seat.user)
    assert client.get(reverse("console:money")).status_code == 403
    assert client.post(reverse("console:money"), _expense_post()).status_code == 403


def test_expense_repeat_rules():
    import datetime

    from console.models import PlatformExpense
    d = datetime.date
    once = PlatformExpense(spent_on=d(2026, 3, 5), amount=100, repeat="ONCE")
    assert once.applies_in(d(2026, 3, 1)) and not once.applies_in(d(2026, 4, 1))
    monthly = PlatformExpense(spent_on=d(2026, 3, 5), amount=100, repeat="MONTHLY", ends_on=d(2026, 6, 1))
    assert not monthly.applies_in(d(2026, 2, 1))
    assert monthly.applies_in(d(2026, 5, 1)) and not monthly.applies_in(d(2026, 7, 1))
    yearly = PlatformExpense(spent_on=d(2025, 3, 5), amount=100, repeat="YEARLY")
    assert yearly.applies_in(d(2026, 3, 1)) and not yearly.applies_in(d(2026, 4, 1))


def test_money_net_is_received_minus_costs():
    import datetime

    from console import insights
    from console.models import PlatformExpense
    PlatformExpense.objects.create(spent_on=datetime.date.today(), amount=700, repeat="ONCE", category="HOSTING")
    data = insights.money()
    assert data["costs_total"] == 700
    assert data["net_total"] == data["received_total"] - 700
    assert data["this_month"]["costs"] == 700


def test_channel_limit_projection(client, settings):
    from console import insights
    settings.MESSAGE_MONTHLY_LIMITS = {"EMAIL": 1, "SMS": 0, "WHATSAPP": 0}
    data = insights.channels()
    email = next(c for c in data["channels"] if c["channel"] == "EMAIL")
    assert email["limit"] == 1


def test_owner_swaps_the_email_accounts_from_the_console(client):
    from core.models import EmailRouting

    owner = make_user(is_superuser=True, is_staff=True)
    signed_in(client, owner)
    page = client.get(reverse("console:email"))
    assert page.status_code == 200
    response = client.post(reverse("console:email"), {"system_account": "messages", "messages_account": "default"})
    assert response.status_code == 302
    assert EmailRouting.get().system_account == "messages"
    assert AuditEvent.objects.filter(action="platform.email_routing").exists()


def test_owner_sets_what_a_referral_earns_from_the_console(client):
    from growth.models import ReferralPolicy

    owner(client)
    page = client.get(reverse("console:referrals"))
    assert page.status_code == 200 and "referrer must have paid first" in page.content.decode().lower()
    form = page.context["form"]
    data = {name: form.initial.get(name, "") for name in form.fields}
    data.update(discount_percent=40, referred_reward_days=14, require_referrer_paid="")
    data = {k: ("on" if v is True else v) for k, v in data.items() if v is not False}
    response = client.post(reverse("console:referrals"), data)
    assert response.status_code == 302
    rule = ReferralPolicy.get()
    assert (rule.discount_percent, rule.referred_reward_days, rule.require_referrer_paid) == (40, 14, False)
    assert AuditEvent.objects.filter(action="platform.referral_policy").exists()


def test_an_analyst_cannot_open_the_referral_policy(client):
    signed_in(client, seat_for().user)
    assert client.get(reverse("console:referrals")).status_code == 403


def test_email_previews_open_and_a_test_goes_to_the_signed_in_person(client, mailoutbox):
    owner_user = make_user(is_superuser=True, is_staff=True, email="boss@example.com")
    signed_in(client, owner_user)
    assert client.get(reverse("console:email_previews")).status_code == 200
    page = client.get(reverse("console:email_preview", args=["receipt"]))
    assert page.status_code == 200 and b"Receipt" in page.content
    assert client.get(reverse("console:email_preview", args=["nope"])).status_code == 404
    assert client.post(reverse("console:email_preview_send", args=["sign-in"])).status_code == 302
    assert [m.to for m in mailoutbox] == [["boss@example.com"]] and mailoutbox[0].subject.startswith("[Test]")
    assert mailoutbox[0].alternatives


def test_email_previews_need_a_seat(client):
    signed_in(client, make_user())
    assert client.get(reverse("console:email_previews")).status_code == 404
    assert client.post(reverse("console:email_preview_send", args=["sign-in"])).status_code == 404


# --- Articles (D-178) ---

def article_post(**kw):
    data = {"title": "How to read a meter", "slug": "", "category": "UTILITIES", "summary": "Read it right.",
            "body": "## Step\n\nLook at it.", "author": "NyumbaPMS team"}
    data.update(kw)
    return data


def test_the_articles_page_needs_its_capability(client):
    signed_in(client, seat_for(PlatformStaff.Role.SUPPORT).user)
    assert client.get(reverse("console:articles")).status_code == 403
    assert client.get(reverse("console:article_new")).status_code == 403
    assert client.get(reverse("console:article_preview", args=[1])).status_code == 403


def test_an_owner_writes_previews_publishes_and_unpublishes_an_article(client):
    from growth.models import Article
    owner(client)
    r = client.post(reverse("console:article_new"), article_post())
    item = Article.objects.get(title="How to read a meter")
    assert r.status_code == 302 and item.slug == "how-to-read-a-meter" and not item.is_published
    assert client.get(reverse("growth:article", args=[item.slug])).status_code == 404
    assert "Look at it." in client.get(reverse("console:article_preview", args=[item.pk])).content.decode()
    client.post(reverse("console:article", args=[item.pk]), {**article_post(slug=item.slug), "publish": "1"})
    item.refresh_from_db()
    assert item.is_published and item.published_at
    assert client.get(reverse("growth:article", args=[item.slug])).status_code == 200
    assert AuditEvent.objects.filter(action="console.article_published").exists()
    client.post(reverse("console:article", args=[item.pk]), {**article_post(slug=item.slug), "unpublish": "1"})
    item.refresh_from_db()
    assert not item.is_published
    client.post(reverse("console:article", args=[item.pk]), {"delete": "1"})
    assert not Article.objects.filter(pk=item.pk).exists()


def test_a_published_article_cannot_be_deleted_and_a_taken_slug_is_refused(client):
    from growth.models import Article
    owner(client)
    live = Article.objects.get(slug="paybill-or-till")
    client.post(reverse("console:article", args=[live.pk]), {"delete": "1"})
    assert Article.objects.filter(pk=live.pk).exists()
    r = client.post(reverse("console:article_new"), article_post(slug="paybill-or-till"))
    assert r.status_code == 200 and Article.objects.filter(title="How to read a meter").count() == 0
    client.post(reverse("console:article_new"), article_post(title="Paybill or till"))
    assert Article.objects.filter(slug="paybill-or-till-2").exists()


def test_owner_sets_subscription_grace_signout_and_seat_by_hand(client):
    from accounts.tests.factories import add_member
    from subscriptions.models import Plan

    membership = make_org("Hand Homes")
    org = membership.organization
    staff = add_member(org, "caretaker")
    owner(client)
    url = reverse("console:organization_access", args=[org.public_id])
    plan = Plan.objects.exclude(pk=org.subscription.plan_id).first()
    if plan is not None:
        client.post(url, {"action": "subscription", "plan": plan.pk, "status": "ACTIVE", "interval": "YEARLY",
                          "period_end": "2030-01-01", "reason": "Partner free year"})
        org.subscription.refresh_from_db()
        assert (org.subscription.plan_id, org.subscription.status, str(org.subscription.period_end)) == (
            plan.pk, "ACTIVE", "2030-01-01")
        assert AuditEvent.objects.filter(action="console.subscription_changed", organization=org).exists()
    client.post(url, {"action": "grace", "days": "20", "reason": "Promised to pay"})
    org.subscription.refresh_from_db()
    assert org.subscription.grace_until is not None
    client.post(url, {"action": "signout", "reason": "Suspected break-in"})
    assert AuditEvent.objects.filter(action="console.organization_signed_out", organization=org).exists()
    client.post(url, {"action": "member", "user": staff.user_id, "reason": "Left the company"})
    staff.refresh_from_db()
    assert staff.is_active is False
    client.post(url, {"action": "member", "user": staff.user_id, "active": "on", "reason": "Back at work"})
    staff.refresh_from_db()
    assert staff.is_active is True
    client.post(url, {"action": "grace", "days": "20", "reason": "x"})  # reason too short
    assert AuditEvent.objects.filter(action="console.grace_extended", organization=org).count() == 1


def test_access_controls_are_for_superusers_only(client):
    org = make_org("Locked Homes").organization
    signed_in(client, seat_for(role=PlatformStaff.Role.OWNER).user)
    assert client.post(reverse("console:organization_access", args=[org.public_id]),
                       {"action": "signout", "reason": "No right"}).status_code == 403
