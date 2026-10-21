"""What stops while a payment is overdue, after how many days, and the owner's per-landlord overrides (D-198)."""

import datetime

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from accounts.tests.factories import make_org, make_property, make_user
from audit.models import AuditEvent
from console.tests.test_console import signed_in
from meters import ocr
from notifications import delivery
from subscriptions import services
from subscriptions.models import BillingPolicy, Subscription, SubscriptionReminder

pytestmark = pytest.mark.django_db

Status = Subscription.Status
TODAY = datetime.date(2026, 3, 10)


def day(n):
    return TODAY + datetime.timedelta(days=n)


def overdue(org, days, **extra):
    """Puts the organization `days` past its due date."""
    Subscription.objects.filter(organization=org).update(
        status=Status.PAST_DUE, past_due_since=day(-days), **extra)


def test_nothing_stops_before_the_days_and_everything_follows_the_platform_after():
    boss = make_org()
    org = boss.organization
    pol = BillingPolicy.get()
    pol.stop_sms_days, pol.stop_email_days, pol.stop_scans_days = 7, 14, None
    pol.save()
    assert not services.blocked(org, "sms", TODAY)  # a trial is not overdue
    overdue(org, 6)
    assert not services.blocked(org, "sms", TODAY)
    overdue(org, 7)
    assert services.blocked(org, "sms", TODAY) and not services.blocked(org, "email", TODAY)
    overdue(org, 14)
    assert services.blocked(org, "email", TODAY)
    overdue(org, 200)
    assert not services.blocked(org, "scans", TODAY)  # empty = never
    assert services.blocked_features(org, TODAY)[:1] == ["SMS"]


def test_one_landlord_can_have_their_own_days_or_never_and_then_back_to_default():
    owner = make_user(is_staff=True, is_superuser=True)
    a, b = make_org().organization, make_org().organization
    overdue(a, 5)
    overdue(b, 5)
    services.set_late_overrides(owner, a, {"sms": 3, "email": None, "scans": 0}, "Pilot landlord, short leash")
    assert services.blocked(a, "sms", TODAY) and services.blocked(a, "scans", TODAY)
    assert not services.blocked(b, "sms", TODAY)  # the platform's 7 days, others unchanged
    overdue(a, 400)
    assert not services.blocked(a, "email", TODAY)  # never, even long overdue
    services.set_late_overrides(owner, a, {"sms": "default"}, "Back to normal")
    overdue(a, 5)
    assert not services.blocked(a, "sms", TODAY)
    audit = AuditEvent.objects.filter(action="console.late_rules", organization=a)
    assert audit.count() == 2 and "Pilot landlord" in str(audit.order_by("created_at").first().changes)


def test_only_an_owner_with_a_reason_and_sensible_numbers_can_change_the_rules():
    org = make_org().organization
    staff = make_user(is_staff=True)
    owner = make_user(is_staff=True, is_superuser=True)
    with pytest.raises(PermissionDenied):
        services.set_late_overrides(staff, org, {"sms": 1}, "a good reason")
    with pytest.raises(ValidationError):
        services.set_late_overrides(owner, org, {"sms": 1}, "no")
    with pytest.raises(ValidationError):
        services.set_late_overrides(owner, org, {"sms": 366}, "a good reason")
    with pytest.raises(ValidationError):
        services.set_late_overrides(owner, org, {"fax": 1}, "a good reason")
    assert Subscription.objects.get(organization=org).late_overrides == {}


def test_an_extension_or_a_dispute_holds_everything_off():
    boss = make_org()
    org = boss.organization
    overdue(org, 20, grace_until=day(3))
    assert not services.blocked(org, "sms", TODAY)
    Subscription.objects.filter(organization=org).update(grace_until=day(-1))
    assert services.blocked(org, "sms", TODAY)


def test_sms_whatsapp_and_email_stop_in_the_delivery_engine_and_start_again_when_paid():
    boss = make_org()
    org = boss.organization
    overdue(org, 30)
    assert not delivery.sms_available(org) and not delivery.whatsapp_available(org)
    assert not delivery.email_available(org)
    Subscription.objects.filter(organization=org).update(status=Status.ACTIVE, past_due_since=None)
    assert delivery.sms_available(org) and delivery.email_available(org)


def test_scans_and_the_assistant_say_why(settings):
    from ai_assistant import services as assistant

    boss = make_org()
    org = boss.organization
    overdue(org, 30)
    assert not ocr.available(org)
    with pytest.raises(ValidationError, match="overdue"):
        assistant.ask(boss, "How much rent did we collect?")
    with pytest.raises(ValidationError, match="overdue"):
        ocr.read_photo(boss, "bill", make_property(org), None, "", {})


def test_the_banner_names_what_is_paused():
    boss = make_org()
    overdue(boss.organization, 8)
    text = str(services.banner(boss.organization, TODAY)["text"])
    assert "Paused until you pay" in text and "SMS" in text and "Email" not in text


def test_the_owner_edits_the_platform_days_and_one_landlords_days_in_the_console(client):
    boss = make_org()
    org = boss.organization
    signed_in(client, make_user(is_staff=True, is_superuser=True))
    assert client.get(reverse("console:late_policy")).status_code == 200
    r = client.post(reverse("console:late_policy"), {"stop_sms_days": "3", "stop_whatsapp_days": "",
                                                     "stop_email_days": "30", "stop_scans_days": "7",
                                                     "stop_assistant_days": "7", "dunning_email": "on"})
    assert r.status_code == 302
    pol = BillingPolicy.get()
    assert (pol.stop_sms_days, pol.stop_whatsapp_days, pol.stop_email_days, pol.dunning_sms) == (3, None, 30, False)
    page = client.get(reverse("console:organization", args=[org.public_id]))
    assert b"If this landlord pays late" in page.content
    r = client.post(reverse("console:organization_late_rules", args=[org.public_id]),
                    {"sms": "1", "whatsapp": "never", "email": "", "scans": "", "assistant": "",
                     "dunning_email": "", "dunning_sms": "off", "reason": "Asked to be strict"})
    assert r.status_code == 302
    assert Subscription.objects.get(organization=org).late_overrides == {
        "sms": 1, "whatsapp": None, "dunning_sms": False}
    bad = client.post(reverse("console:organization_late_rules", args=[org.public_id]),
                      {"sms": "soon", "reason": "Asked to be strict"})
    assert bad.status_code == 302 and Subscription.objects.get(organization=org).late_overrides["sms"] == 1


def test_reminders_go_by_email_and_sms_once_each_and_each_can_be_switched_off_per_landlord():
    boss = make_org(owner=make_user(email="boss@example.co.ke", phone="0722000111"))
    org = boss.organization
    overdue(org, 1)
    mail.outbox.clear()
    assert services.send_reminders(TODAY) == 2
    assert {r.channel for r in SubscriptionReminder.objects.filter(subscription__organization=org)} == {"EMAIL", "SMS"}
    assert services.send_reminders(TODAY) == 0  # once per channel
    other = make_org(owner=make_user(email="b2@example.co.ke", phone="0722000112")).organization
    overdue(other, 1)
    Subscription.objects.filter(organization=other).update(late_overrides={"dunning_sms": False})
    assert services.send_reminders(TODAY) == 1
    assert not SubscriptionReminder.objects.filter(subscription__organization=other, channel="SMS").exists()
    # the overdue email now says what will stop and when
    assert "these stop on their own" in mail.outbox[-1].body


def test_paused_sections_refuse_new_records_but_keep_viewing_and_come_back_when_paid(monkeypatch):
    monkeypatch.setattr(services.timezone, "localdate", lambda: TODAY)
    from accounts.permissions import can

    boss = make_org()
    org = boss.organization
    pol = BillingPolicy.get()
    pol.stop_expenses_days, pol.stop_invoices_days = 10, None
    pol.save()
    overdue(org, 9)
    assert can(boss, "expenses.submit")
    org = type(org).objects.get(pk=org.pk)
    boss.organization = org
    overdue(org, 11)
    org = type(org).objects.get(pk=org.pk)
    boss.organization = org
    assert not can(boss, "expenses.submit")  # paused
    assert can(boss, "expenses.view")  # viewing stays
    assert can(boss, "invoices.generate")  # empty = never
    Subscription.objects.filter(organization=org).update(status=Status.ACTIVE, past_due_since=None)
    boss.organization = type(org).objects.get(pk=org.pk)
    assert can(boss, "expenses.submit")


def test_a_landlord_can_have_their_own_days_for_a_section(monkeypatch):
    monkeypatch.setattr(services.timezone, "localdate", lambda: TODAY)
    owner = make_user(is_staff=True, is_superuser=True)
    org = make_org().organization
    overdue(org, 3)
    assert "expenses" not in services.blocked_modules(type(org).objects.get(pk=org.pk))
    services.set_late_overrides(owner, org, {"expenses": 2}, "Short leash for this one")
    assert "expenses" in services.blocked_modules(type(org).objects.get(pk=org.pk))


def test_a_repeat_signup_with_the_same_phone_or_email_gets_no_new_free_trial():
    first = make_org(owner=make_user(email="same@example.co.ke", phone="0722555111"))
    assert Subscription.objects.get(organization=first.organization).trial_ends_on > datetime.date.today()
    again = make_org(owner=make_user(email="other@example.co.ke", phone="0722555222"))
    again.organization.billing_email = "SAME@example.co.ke"
    again.organization.save()
    Subscription.objects.filter(organization=again.organization).delete()
    assert services.subscription_for(again.organization).trial_ends_on <= datetime.date.today()
    fresh = make_org(owner=make_user(email="new@example.co.ke", phone="0722555333"))
    assert Subscription.objects.get(organization=fresh.organization).trial_ends_on > datetime.date.today()


def test_invoicing_jobs_skip_a_landlord_whose_invoicing_is_paused(monkeypatch):
    from billing import invoicing, late_fees

    boss = make_org()
    org = boss.organization
    pol = BillingPolicy.get()
    pol.stop_invoices_days = 5
    pol.save()
    seen = []
    monkeypatch.setattr(invoicing, "missed_months", lambda o, t: seen.append(o.pk) or [])
    monkeypatch.setattr(invoicing, "months_to_bill", lambda o, t: [])
    monkeypatch.setattr(invoicing, "bill_meter_charges", lambda o, today: invoicing.GenerationResult([], []) if hasattr(invoicing, "GenerationResult") else type("R", (), {"invoices": [], "errors": []})())
    invoicing.run_scheduled(datetime.date.today())
    assert org.pk in seen
    seen.clear()
    Subscription.objects.filter(organization=org).update(status=Status.PAST_DUE,
                                                         past_due_since=datetime.date.today() - datetime.timedelta(days=6))
    invoicing.run_scheduled(datetime.date.today())
    assert org.pk not in seen
    assert late_fees.run_daily(datetime.date.today())["late_fees_proposed"] == 0


def test_an_overdue_landlord_cannot_wipe_the_debt_by_choosing_the_free_plan():
    from subscriptions.models import Plan

    boss = make_org()
    free = Plan.objects.filter(key="free").first()
    if free is None:
        pytest.skip("no free plan seeded")
    Plan.objects.filter(pk=free.pk).update(is_active=True, self_serve=True)
    free.refresh_from_db()
    org = boss.organization
    overdue(org, 3)
    sub = Subscription.objects.get(organization=org)
    paid = Plan.objects.filter(monthly_price__gt=0, is_active=True).first()
    services.issue_invoice(sub, paid, "MONTHLY", reason="test", today=datetime.date.today())
    assert services.open_invoice(sub) is not None
    with pytest.raises(ValidationError, match="Pay the overdue"):
        services.choose_plan(boss, free, "MONTHLY")


def test_the_same_browser_or_a_busy_network_gets_no_new_free_trial():
    from accounts.models import Organization

    today = datetime.date.today()
    first = make_org(owner=make_user(email="a1@example.co.ke", phone="0722666111")).organization
    Organization.objects.filter(pk=first.pk).update(signup_device="d" * 32, signup_ip_hash="h" * 64)
    # a new phone and email but the same browser: no trial
    same_browser = make_org(owner=make_user(email="a2@example.co.ke", phone="0722666222")).organization
    Organization.objects.filter(pk=same_browser.pk).update(signup_device="d" * 32)
    Subscription.objects.filter(organization=same_browser).delete()
    assert services.subscription_for(Organization.objects.get(pk=same_browser.pk)).trial_ends_on <= today
    # one other organization on the network is fine, the third is not
    second = make_org(owner=make_user(email="a3@example.co.ke", phone="0722666333")).organization
    Organization.objects.filter(pk=second.pk).update(signup_ip_hash="h" * 64, signup_device="e" * 32)
    Subscription.objects.filter(organization=second).delete()
    assert services.subscription_for(Organization.objects.get(pk=second.pk)).trial_ends_on > today
    third = make_org(owner=make_user(email="a4@example.co.ke", phone="0722666444")).organization
    Organization.objects.filter(pk=third.pk).update(signup_ip_hash="h" * 64, signup_device="f" * 32)
    Subscription.objects.filter(organization=third).delete()
    assert services.subscription_for(Organization.objects.get(pk=third.pk)).trial_ends_on <= today
