"""Public pages, demo requests, referrals and sign-up sources (D-133)."""

import datetime

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from accounts.models import Organization
from accounts.tests.factories import PASSWORD, add_member, make_org, make_user
from accounts.tests.test_isolation import login
from audit.models import AuditEvent
from growth import content, referrals
from growth.models import DemoRequest, Referral, ReferralCode, ReferralCredit, ReferralPolicy, SignupSource
from subscriptions import services as subscription_services
from subscriptions.models import Plan, Subscription

pytestmark = pytest.mark.django_db


def demo_post(**over):
    data = {"full_name": "Wanjiru Kamau", "phone": "0712345678", "email": "w@example.com",
            "organization_name": "Kamau Flats", "kind": "LANDLORD", "size": "11-30", "channel": "CALL",
            "preferred_date": (timezone.localdate() + datetime.timedelta(days=3)).isoformat(),
            "preferred_slot": "MORNING", "message": "Water bills", "website": ""}
    data.update(over)
    return data


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------


def test_the_public_pages_need_no_login(client):
    for name in ("growth:pricing", "growth:faq", "growth:tutorials", "growth:demo", "growth:demo_thanks"):
        assert client.get(reverse(name)).status_code == 200, name
    for tutorial in content.TUTORIALS:
        assert client.get(reverse("growth:tutorial", args=[tutorial["slug"]])).status_code == 200
    assert client.get(reverse("growth:tutorial", args=["nope"])).status_code == 404


def test_a_visitor_sees_the_front_page_and_a_member_sees_home(client):
    page = client.get("/")
    assert page.status_code == 200
    body = page.content.decode()
    assert "Start your 30-day free trial" in body and reverse("accounts:register") in body
    owner = make_org()
    login(client, owner)
    assert "Start your 30-day free trial" not in client.get("/").content.decode()


def test_pricing_shows_the_live_plans(client):
    Plan.objects.filter(key="lite").update(monthly_price=777)
    body = client.get(reverse("growth:pricing")).content.decode()
    assert "Lite" in body and "777" in body


def test_the_faq_marks_up_every_question_and_cannot_be_broken_out_of(client):
    body = client.get(reverse("growth:faq")).content.decode()
    assert 'type="application/ld+json"' in body and "FAQPage" in body
    assert body.count("data-faq") == len(content.faq_pairs())
    assert "Do you hold our rent money?" in body


def test_robots_and_sitemap(client, settings):
    settings.SITE_URL = "https://app.example.com"
    robots = client.get(reverse("growth:robots")).content.decode()
    assert "Sitemap: https://app.example.com/sitemap.xml" in robots and "Disallow: /my/" in robots
    sitemap = client.get(reverse("growth:sitemap")).content.decode()
    assert "https://app.example.com/pricing/" in sitemap and "/tutorials/first-invoice/" in sitemap


def test_signed_in_users_do_not_get_the_public_menu(client):
    assert "Start free trial" in client.get(reverse("growth:faq")).content.decode()
    owner = make_org()
    login(client, owner)
    assert "Start free trial" not in client.get(reverse("growth:faq")).content.decode()


# ---------------------------------------------------------------------------
# Demo requests
# ---------------------------------------------------------------------------


def test_a_demo_request_is_saved_and_the_team_and_visitor_are_emailed(client, settings):
    settings.OPS_ALERT_EMAILS = "ops@example.com"
    response = client.post(reverse("growth:demo"), demo_post())
    assert response.status_code == 302 and response.url == reverse("growth:demo_thanks")
    item = DemoRequest.objects.get()
    assert item.phone == "+254712345678" and item.status == "NEW"
    assert {tuple(m.to) for m in mail.outbox} == {("ops@example.com",), ("w@example.com",)}
    assert "Wanjiru" in client.get(reverse("growth:demo_thanks")).content.decode()


def test_asking_twice_in_a_day_updates_the_open_request(client):
    client.post(reverse("growth:demo"), demo_post())
    client.post(reverse("growth:demo"), demo_post(message="Actually, electricity too"))
    assert DemoRequest.objects.count() == 1
    assert DemoRequest.objects.get().message == "Actually, electricity too"


def test_a_bad_phone_or_date_is_refused(client):
    for bad in (demo_post(phone="12"), demo_post(preferred_date="2020-01-01"),
                demo_post(preferred_date=(timezone.localdate() + datetime.timedelta(days=90)).isoformat())):
        assert client.post(reverse("growth:demo"), bad).status_code == 200
    assert DemoRequest.objects.count() == 0


def test_the_hidden_field_catches_scripts(client):
    response = client.post(reverse("growth:demo"), demo_post(website="http://spam.example"))
    assert response.status_code == 302
    assert DemoRequest.objects.count() == 0


def test_one_connection_cannot_flood_the_form(client):
    for n in range(services_limit()):
        assert client.post(reverse("growth:demo"), demo_post(phone=f"07123456{n:02d}")).status_code == 302
    flooded = client.post(reverse("growth:demo"), demo_post(phone="0799999999"))
    assert flooded.status_code == 200 and "Too many requests" in flooded.content.decode()
    assert DemoRequest.objects.count() == services_limit()


def services_limit():
    from growth import services

    return services.PER_IP_HOUR


# ---------------------------------------------------------------------------
# Sign-up source and referrals
# ---------------------------------------------------------------------------


def signed_up(client, query="", *, phone="0722000111"):
    """A visitor follows a link, registers, verifies the phone and makes a workspace. Returns the organization."""
    client.get("/register/" + query)
    user = make_user(phone=phone, verified=True, email=f"owner{phone}@example.com")  # the owner of a workspace needs an email (D-151)
    assert client.login(username=user.phone, password=PASSWORD)
    client.post(reverse("accounts:onboarding"), {"name": f"New Co {phone}", "org_type": Organization.Type.INDIVIDUAL})
    return Organization.objects.get(name=f"New Co {phone}")


def test_a_campaign_link_is_remembered_until_the_workspace_is_made(client):
    org = signed_up(client, "?utm_source=Facebook&utm_medium=ad&utm_campaign=Oct-Landlords")
    source = SignupSource.objects.get(organization=org)
    assert (source.source, source.medium, source.campaign) == ("facebook", "ad", "oct-landlords")


def test_a_direct_visit_leaves_no_source(client):
    org = signed_up(client)
    assert not SignupSource.objects.filter(organization=org).exists()


def test_the_refer_page_makes_one_stable_code_for_owners_only(client):
    owner = make_org()
    login(client, owner)
    first = client.get(reverse("growth:refer"))
    assert first.status_code == 200
    code = ReferralCode.objects.get(organization=owner.organization).code
    assert code in first.content.decode() and f"/register/?ref={code}" in first.content.decode()
    client.get(reverse("growth:refer"))
    assert ReferralCode.objects.filter(organization=owner.organization).count() == 1
    caretaker = add_member(owner.organization, "caretaker")
    login(client, caretaker)
    assert client.get(reverse("growth:refer")).status_code == 403


def test_a_valid_code_shows_the_invitation_on_the_sign_up_page(client):
    referrer = make_org()
    code = referrals.code_for(referrer.organization)
    assert "data-referral-applied" in client.get(f"/register/?ref={code}").content.decode()
    assert "data-referral-applied" not in client.get("/register/?ref=NOSUCHCODE").content.decode()


def _register_post(code, phone="0733111222"):
    return {"full_name": "New Landlord", "phone": phone, "email": f"n{phone}@example.com",
            "password": "Zx9!kQ-long-pass-47", "accept_terms": "on", "referral_code": code}


def test_the_sign_up_page_has_a_hidden_referral_code_box(client):
    page = client.get("/register/").content.decode()
    assert "data-referral-box" in page and "Have a referral code?" in page
    referrer = make_org()
    code = referrals.code_for(referrer.organization)
    assert "data-referral-box" not in client.get(f"/register/?ref={code}").content.decode()  # already applied


def test_a_typed_code_attaches_the_referral_like_the_link_does(client):
    referrer = make_org()
    code = referrals.code_for(referrer.organization)
    response = client.post("/register/", _register_post(code.lower()))
    assert response.status_code == 302
    from accounts.models import User
    User.objects.filter(phone="+254733111222").update(phone_verified_at=timezone.now())
    client.post(reverse("accounts:onboarding"), {"name": "Typed Co", "org_type": Organization.Type.INDIVIDUAL})
    assert Referral.objects.get(referred=Organization.objects.get(name="Typed Co")).referrer == referrer.organization


def test_a_wrong_typed_code_stops_sign_up_with_a_message(client):
    response = client.post("/register/", _register_post("NOSUCHCD"))
    assert response.status_code == 200 and "referral code was not found" in response.content.decode()
    assert client.post("/register/", _register_post("")).status_code == 302  # empty box: nothing to check


@pytest.fixture
def days_policy(db):
    """The first rule (D-133): free days for both sides when the new workspace first pays, nothing to set up."""
    ReferralPolicy.objects.update_or_create(pk=1, defaults={
        "reward_kind": ReferralPolicy.RewardKind.DAYS, "reward_days": 30, "require_payment": True,
        "reward_referred_too": True, "referred_reward_kind": ReferralPolicy.RewardKind.DAYS,
        "referred_reward_days": 30, "require_referrer_paid": False, "min_properties": 0, "min_units": 0,
        "min_tenants": 0})


@pytest.fixture
def classic_policy(db):
    """Before D-133 grew a second reward: only the referrer is rewarded, and need not have paid."""
    ReferralPolicy.objects.update_or_create(pk=1, defaults={"reward_referred_too": False,
                                                            "require_referrer_paid": False})


def pay_first_invoice(org):
    from accounts.tests.factories import make_user as _mk

    admin = _mk(is_staff=True, is_superuser=True)
    membership = org.memberships.first()
    plan = Plan.objects.get(key="lite")
    invoice = subscription_services.choose_plan(membership, plan, "MONTHLY")
    return subscription_services.record_payment(
        admin, invoice, amount=invoice.total, method="MPESA", reference="QRF1234567", paid_on=timezone.localdate())


def test_both_sides_are_rewarded_when_the_new_workspace_first_pays(client, days_policy):
    referrer = make_org()
    subscription_services.subscription_for(referrer.organization)
    before = Subscription.objects.get(organization=referrer.organization).trial_ends_on
    code = referrals.code_for(referrer.organization)
    new_org = signed_up(client, f"?ref={code}")
    referral = Referral.objects.get(referred=new_org)
    assert referral.referrer == referrer.organization and referral.status == Referral.Status.SIGNED_UP
    assert Subscription.objects.get(organization=referrer.organization).trial_ends_on == before  # nothing yet

    pay_first_invoice(new_org)
    referral.refresh_from_db()
    assert referral.status == Referral.Status.REWARDED and referral.reward_days == 30
    assert Subscription.objects.get(organization=referrer.organization).trial_ends_on == before + datetime.timedelta(days=30)
    paid = Subscription.objects.get(organization=new_org)
    assert paid.period_end > subscription_services.period_end(timezone.localdate(), "MONTHLY")
    assert AuditEvent.objects.filter(action="referral.reward", organization=referrer.organization).exists()


def test_a_second_payment_does_not_reward_again(client, days_policy):
    referrer = make_org()
    code = referrals.code_for(referrer.organization)
    new_org = signed_up(client, f"?ref={code}")
    pay_first_invoice(new_org)
    after_first = Subscription.objects.get(organization=referrer.organization).trial_ends_on
    referrals.on_payment(new_org)
    assert Subscription.objects.get(organization=referrer.organization).trial_ends_on == after_first


def test_nobody_refers_themselves(client):
    owner = make_org()
    code = referrals.code_for(owner.organization)
    client.get(f"/register/?ref={code}")
    login(client, owner)
    client.post(reverse("accounts:onboarding"), {"name": "My second workspace", "org_type": "INDIVIDUAL"})
    assert not Referral.objects.exists()


def test_a_lapsed_referrer_is_marked_and_the_new_customer_is_still_rewarded(client, days_policy):
    referrer = make_org()
    code = referrals.code_for(referrer.organization)
    Subscription.objects.filter(organization=referrer.organization).update(status="LAPSED", trial_ends_on=None)
    subscription_services.subscription_for(referrer.organization)
    new_org = signed_up(client, f"?ref={code}")
    pay_first_invoice(new_org)
    referral = Referral.objects.get(referred=new_org)
    assert referral.status == Referral.Status.REFERRER_LAPSED and referral.reward_days == 0


def test_the_yearly_cap_stops_rewards(client, days_policy):
    ReferralPolicy.objects.update(max_rewards_per_year=0)
    referrer = make_org()
    code = referrals.code_for(referrer.organization)
    before = Subscription.objects.get(organization=referrer.organization).trial_ends_on
    new_org = signed_up(client, f"?ref={code}")
    pay_first_invoice(new_org)
    assert Referral.objects.get(referred=new_org).status == Referral.Status.REFERRER_LAPSED
    assert Subscription.objects.get(organization=referrer.organization).trial_ends_on == before


def set_up(org, units=11, tenants=11):
    from properties import services as property_services
    from tenants.models import Tenant

    owner = org.memberships.first()
    n = Tenant.objects.filter(organization=org).count() + org.properties.count()
    prop = property_services.create_property(owner, name=f"Block {n}", code=f"B{n}", area="Kilimani",
                                             county="NAIROBI")
    for i in range(units):
        property_services.create_unit(owner, prop, code=f"A{i}", list_rent=10000)
    for i in range(tenants):
        Tenant.objects.create(organization=org, name=f"T{n}-{i}", phone=f"+2547110{n:02d}{i:03d}")


def test_the_referrer_gets_a_discount_once_the_new_workspace_is_set_up(client, classic_policy):
    referrer = make_org()
    code = referrals.code_for(referrer.organization)
    new_org = signed_up(client, f"?ref={code}")
    assert referrals.check_pending() == 0  # signed up, nothing added yet
    set_up(new_org, units=10)
    assert referrals.check_pending() == 0  # one unit short of the default 11
    set_up(new_org, units=1, tenants=0)
    assert referrals.check_pending() == 1
    referral = Referral.objects.get(referred=new_org)
    assert referral.status == Referral.Status.REWARDED and referral.reward_percent == 50
    assert list(ReferralCredit.objects.values_list("organization", "percent")) == [(referrer.organization.pk, 50)]
    assert referrals.check_pending() == 0  # once only


def test_a_referral_credit_comes_off_the_next_plan_invoice_once(client, classic_policy):
    referrer = make_org()
    code = referrals.code_for(referrer.organization)
    new_org = signed_up(client, f"?ref={code}")
    set_up(new_org)
    referrals.check_pending()
    membership = referrer.organization.memberships.first()
    lite = Plan.objects.get(key="lite")
    invoice = subscription_services.choose_plan(membership, lite, "MONTHLY")
    assert invoice.discount == invoice.price / 2 and invoice.net_amount == invoice.price - invoice.discount
    assert ReferralCredit.objects.get().invoice == invoice
    admin = make_user(is_staff=True, is_superuser=True)
    subscription_services.void_invoice(admin, invoice, "Wrong plan")
    assert ReferralCredit.objects.get().used_at is None  # given back
    again = subscription_services.choose_plan(membership, lite, "MONTHLY")
    assert again.discount == again.price / 2
    later = subscription_services.issue_invoice(again.subscription, lite, "MONTHLY", reason="RENEWAL",
                                                today=timezone.localdate())
    assert later.discount == 0


def test_a_policy_that_needs_payment_waits_for_it_and_can_reward_both(client):
    ReferralPolicy.objects.update_or_create(pk=1, defaults={
        "require_payment": True, "reward_referred_too": True, "require_referrer_paid": False,
        "referred_reward_kind": ReferralPolicy.RewardKind.DISCOUNT, "min_units": 0, "min_tenants": 0})
    referrer = make_org()
    code = referrals.code_for(referrer.organization)
    new_org = signed_up(client, f"?ref={code}")
    set_up(new_org, units=0, tenants=0)
    assert referrals.check_pending() == 0
    pay_first_invoice(new_org)
    assert Referral.objects.get(referred=new_org).status == Referral.Status.REWARDED
    assert set(ReferralCredit.objects.values_list("organization", flat=True)) == {referrer.organization.pk,
                                                                                 new_org.pk}


def test_the_invite_banner_follows_the_policy(client, classic_policy):
    referrer = make_org()
    code = referrals.code_for(referrer.organization)
    page = client.get(f"/register/?ref={code}").content.decode()
    assert "You were invited by a NyumbaPMS customer." in page and "% off" not in page
    ReferralPolicy.objects.update(reward_referred_too=True)
    assert "30 free days" in client.get("/register/").content.decode()
    ReferralPolicy.objects.update(referred_reward_kind=ReferralPolicy.RewardKind.DISCOUNT)
    assert "50% off a plan invoice" in client.get("/register/").content.decode()


def test_by_default_the_referrer_must_have_paid_and_the_invited_get_a_month(client):
    referrer = make_org()
    code = referrals.code_for(referrer.organization)
    new_org = signed_up(client, f"?ref={code}")
    set_up(new_org)
    trial_before = Subscription.objects.get(organization=new_org).trial_ends_on
    assert referrals.check_pending() == 0  # set up, but the referrer has never paid: no referrer reward yet
    trial_before += datetime.timedelta(days=30)  # the invited landlord was not kept waiting
    assert Subscription.objects.get(organization=new_org).trial_ends_on == trial_before
    assert referrals.check_pending() == 0
    assert Subscription.objects.get(organization=new_org).trial_ends_on == trial_before  # and only once
    from notifications.models import Message as _Message

    waiting = _Message.objects.filter(type="referral_waiting", organization=referrer.organization)
    assert waiting.count() == 1 and "50% off your next plan invoice" in waiting.get().body  # told once, not daily
    login(client, referrer)
    assert "Waiting for you to pay for a plan" in client.get(reverse("growth:refer")).content.decode()

    pay_first_invoice(referrer.organization)  # the referrer's first payment releases the waiting referral
    referral = Referral.objects.get(referred=new_org)
    assert referral.status == Referral.Status.REWARDED and referral.reward_percent == 50
    assert list(ReferralCredit.objects.values_list("organization", "percent")) == [(referrer.organization.pk, 50)]
    assert Subscription.objects.get(organization=new_org).trial_ends_on == trial_before  # not given twice
    from notifications.models import Message

    mine = Message.objects.filter(type="referral_rewarded", organization=referrer.organization)
    assert mine.count() == 1 and "50% off your next plan invoice" in mine.get().body
    assert new_org.name not in mine.get().body
    welcome = Message.objects.filter(type="referral_welcome", organization=new_org)
    assert welcome.count() == 1 and "30 free days" in welcome.get().body


def test_the_referrer_never_sees_the_referred_business_name(client):
    referrer = make_org()
    code = referrals.code_for(referrer.organization)
    new_org = signed_up(client, f"?ref={code}", phone="0733000222")
    login(client, referrer)
    page = client.get(reverse("growth:refer")).content.decode()
    assert new_org.name not in page
    assert not AuditEvent.objects.filter(organization=referrer.organization, changes__icontains=new_org.name).exists()


# --- About, How it works, articles, advanced FAQ, delete account (D-135) ---

def test_public_info_pages_open_without_signing_in(client):
    from growth import articles
    names = [reverse(n) for n in ("growth:about", "growth:how_it_works", "growth:articles", "support:delete_account")]
    names += [reverse("growth:article", args=[a.slug]) for a in articles.published()]
    for path in names:
        assert client.get(path).status_code == 200, path
    assert client.get(reverse("growth:article", args=["nope"])).status_code == 404


def test_faq_includes_the_advanced_questions_and_the_sitemap_lists_new_pages(client):
    body = client.get(reverse("growth:faq")).content.decode()
    assert "How do I delete my account?" in body
    sitemap = client.get(reverse("growth:sitemap")).content.decode()
    assert "/about/" in sitemap and "/articles/" in sitemap and "/delete-account/" in sitemap


def test_deleting_an_account_is_asked_for_not_done_on_the_spot(client):
    from support.models import SupportRequest
    owner = make_org()
    login(client, owner)
    path = reverse("support:delete_account")
    assert client.post(path, {"confirm": "no"}).status_code == 200
    assert SupportRequest.objects.count() == 0
    assert client.post(path, {"confirm": "delete", "reason": "Selling"}).status_code == 302
    item = SupportRequest.objects.get()
    assert item.kind == "DELETION" and item.organization == owner.organization
    owner.user.refresh_from_db()
    assert owner.user.is_active


def test_signed_out_visitors_cannot_post_a_deletion_request(client):
    response = client.post(reverse("support:delete_account"), {"confirm": "DELETE"})
    assert response.status_code == 302 and "login" in response["Location"].lower()
