"""Platform billing: plans, trial, invoices, payments, limits and the SMS wallet (D-060)."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from accounts import services as account_services
from accounts.models import Organization
from accounts.tests.factories import PASSWORD, add_member, fresh, make_org, make_property, make_user, role
from accounts.tests.test_isolation import login
from audit.models import AuditEvent
from notifications import catalog, delivery
from notifications.models import Message
from notifications.providers.sms import SmsResult
from properties import services as property_services
from subscriptions import entitlements, services
from subscriptions.models import (
    Plan,
    SmsWallet,
    SmsWalletEntry,
    Subscription,
    SubscriptionInvoice,
    SubscriptionPayment,
)
from tenants import services as tenant_services

pytestmark = pytest.mark.django_db

Status = Subscription.Status
TODAY = datetime.date(2026, 3, 10)


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def admin():
    return make_user(is_staff=True, is_superuser=True)


def plan(key):
    return Plan.objects.get(key=key)


@pytest.fixture
def free_on():
    """Free is off until a Platform Admin switches it on (D-132)."""
    Plan.objects.filter(key="free").update(is_active=True)


def pay(admin, invoice, *, amount=None, reference="QAB1", on=TODAY):
    return services.record_payment(admin, invoice, amount=amount or invoice.total, method="MPESA",
                                   reference=reference, paid_on=on)


def units(owner, n, prop=None):
    prop = prop or make_property(owner.organization)
    for i in range(n):
        property_services.create_unit(owner, prop, code=f"U{prop.pk}-{i}")
    return prop


# ---------------------------------------------------------------------------
# Plans, dates and numbers
# ---------------------------------------------------------------------------


def test_plans_are_seeded():
    keys = list(Plan.objects.values_list("key", flat=True))
    assert keys == ["free", "lite", "starter", "growth", "business", "professional", "enterprise"]
    assert plan("free").is_free and plan("business").unit_limit == 100 and plan("enterprise").unit_limit is None
    assert not plan("enterprise").self_serve
    # Free is off until it is switched on. One-off plans are not offered (D-146).
    assert not plan("free").is_active
    assert [p.key for p in services.self_serve_plans()] == ["lite", "starter", "growth", "business"]
    assert not [p for p in Plan.objects.all() if p.has_one_off]


def test_a_plan_shows_its_offers_with_the_saving():
    assert [interval for interval, _price, _saved in plan("starter").offers()] == ["MONTHLY", "YEARLY"]


@pytest.fixture
def one_off_on(monkeypatch):
    monkeypatch.setattr("subscriptions.models.ONE_OFF_OFFERED", True)


def test_a_plan_shows_its_one_off_offer_when_switched_on(one_off_on):
    offers = {interval: (price, saved) for interval, price, saved in plan("starter").offers()}
    assert offers["MONTHLY"] == (Decimal("799"), 0)
    assert offers["YEARLY"][1] == 16 and offers["ONCE"] == (Decimal("19990"), 58)
    assert [interval for interval, _price, _saved in plan("business").offers()] == ["MONTHLY", "YEARLY"]


def test_a_one_off_plan_lasts_its_years_then_renews_yearly(owner, admin, one_off_on):
    invoice = services.choose_plan(owner, plan("starter"), "ONCE", today=TODAY)
    assert invoice.total == Decimal("19990.00") and invoice.interval == "ONCE"
    pay(admin, invoice, on=TODAY)
    sub = Subscription.objects.get(organization=owner.organization)
    assert (sub.status, sub.interval, sub.period_end) == (Status.ACTIVE, "ONCE", datetime.date(2031, 3, 9))
    assert services.daily(datetime.date(2030, 3, 9))["renewals"] == 0  # nothing for five years
    assert services.daily(datetime.date(2031, 3, 3))["renewals"] == 1
    renewal = services.open_invoice(sub)
    assert renewal.interval == "YEARLY" and renewal.total == Decimal("7990.00")
    assert renewal.period_start == datetime.date(2031, 3, 10)


def test_the_years_of_a_one_off_follow_the_plan_in_admin(owner, admin, one_off_on):
    Plan.objects.filter(key="starter").update(one_off_years=3)
    pay(admin, services.choose_plan(owner, plan("starter"), "ONCE", today=TODAY), on=TODAY)
    assert Subscription.objects.get(organization=owner.organization).period_end == datetime.date(2029, 3, 9)


def test_upgrading_from_a_one_off_credits_the_unused_time(owner, admin, one_off_on):
    pay(admin, services.choose_plan(owner, plan("starter"), "ONCE", today=TODAY), on=TODAY)
    invoice = services.choose_plan(owner, plan("growth"), "YEARLY", today=TODAY + datetime.timedelta(days=365))
    assert Decimal("0") < invoice.credit < Decimal("19990") and invoice.total == Decimal("12990") - invoice.credit \
        or invoice.credit == Decimal("12990.00") and invoice.total == 0


def test_a_plan_without_a_one_off_price_refuses_one(owner):
    with pytest.raises(ValidationError, match="no one-off price"):
        services.choose_plan(owner, plan("business"), "ONCE", today=TODAY)


def test_free_is_not_offered_until_it_is_switched_on(owner):
    with pytest.raises(ValidationError, match="not available"):
        services.choose_plan(owner, plan("free"), "MONTHLY", today=TODAY)


def test_a_trial_ending_without_free_is_invoiced_for_the_band_that_fits(owner):
    units(owner, 6)
    _trial_ends(owner, TODAY - datetime.timedelta(days=1))
    assert services.daily(TODAY)["trial_ended"] == 1
    sub = Subscription.objects.get(organization=owner.organization)
    assert sub.status == Status.PAST_DUE and sub.plan.key == "lite"
    assert services.open_invoice(sub).plan_name == "Lite"


def test_a_small_trial_without_free_must_pay_too(owner):
    _trial_ends(owner, TODAY - datetime.timedelta(days=1))
    assert services.daily(TODAY) == {"trial_to_free": 0, "trial_ended": 1, "renewals": 0, "past_due": 0, "lapsed": 0}


def test_period_end():
    assert services.period_end(datetime.date(2026, 1, 31), "MONTHLY") == datetime.date(2026, 2, 27)
    assert services.period_end(datetime.date(2026, 3, 10), "MONTHLY") == datetime.date(2026, 4, 9)
    assert services.period_end(datetime.date(2026, 3, 10), "YEARLY") == datetime.date(2027, 3, 9)


def test_a_new_organization_starts_a_business_trial(owner):
    sub = owner.organization.subscription
    assert sub.status == Status.TRIAL and sub.plan.key == "business"
    assert sub.trial_ends_on == timezone.localdate() + datetime.timedelta(days=30)


# ---------------------------------------------------------------------------
# Choosing a plan and paying
# ---------------------------------------------------------------------------


def test_choosing_a_plan_issues_an_invoice_and_payment_starts_it(owner, admin, settings):
    settings.PLATFORM_VAT_REGISTERED = False
    invoice = services.choose_plan(owner, plan("starter"), "MONTHLY", today=TODAY)
    assert invoice.number == f"LPM-INV-{TODAY.year}-000001"
    assert invoice.total == Decimal("799.00") and invoice.vat_amount == 0 and invoice.period_start is None
    assert invoice.customer_name == owner.organization.name
    payment = pay(admin, invoice, on=TODAY)
    invoice.refresh_from_db()
    sub = Subscription.objects.get(organization=owner.organization)
    assert invoice.status == SubscriptionInvoice.Status.PAID and payment.receipt.number.startswith("LPM-RCT-")
    assert sub.status == Status.ACTIVE and sub.plan.key == "starter" and sub.trial_ends_on is None
    assert (sub.period_start, sub.period_end) == (TODAY, datetime.date(2026, 4, 9))
    assert AuditEvent.objects.filter(action="subscription.activate", organization=owner.organization).exists()


def test_vat_is_added_when_registered(owner, settings):
    settings.PLATFORM_VAT_REGISTERED = True
    settings.PLATFORM_VAT_RATE = "16"
    invoice = services.choose_plan(owner, plan("business"), "YEARLY", today=TODAY)
    assert (invoice.net_amount, invoice.vat_amount, invoice.total) == (
        Decimal("19990.00"), Decimal("3198.40"), Decimal("23188.40"))


def test_part_payment_leaves_the_invoice_open(owner, admin):
    invoice = services.choose_plan(owner, plan("starter"), "MONTHLY", today=TODAY)
    pay(admin, invoice, amount=Decimal("500"), reference="P1")
    invoice.refresh_from_db()
    assert invoice.status == SubscriptionInvoice.Status.OPEN and invoice.outstanding == Decimal("299.00")
    pay(admin, invoice, amount=Decimal("299"), reference="P2")
    invoice.refresh_from_db()
    assert invoice.status == SubscriptionInvoice.Status.PAID


def test_payment_checks(owner, admin):
    invoice = services.choose_plan(owner, plan("starter"), "MONTHLY", today=TODAY)
    with pytest.raises(PermissionDenied):
        pay(owner.user, invoice)
    pay(admin, invoice, reference="dup1")
    other = services.choose_plan(owner, plan("business"), "MONTHLY", today=TODAY)
    with pytest.raises(ValidationError):
        pay(admin, other, reference="DUP1")  # references are unique, whatever the case
    with pytest.raises(ValidationError):
        pay(admin, invoice, reference="X2")  # already paid


def test_upgrading_credits_unused_days(owner, admin):
    first = services.choose_plan(owner, plan("starter"), "MONTHLY", today=TODAY)
    pay(admin, first, on=TODAY)  # 10 Mar to 9 Apr, 31 days
    later = TODAY + datetime.timedelta(days=10)
    invoice = services.choose_plan(owner, plan("business"), "MONTHLY", today=later)
    # 20 of 31 days left of 799
    assert invoice.credit == Decimal("515.48") and invoice.total == Decimal("1999.00") - Decimal("515.48")


def test_choosing_again_voids_the_unpaid_invoice(owner):
    first = services.choose_plan(owner, plan("starter"), "MONTHLY", today=TODAY)
    services.choose_plan(owner, plan("business"), "MONTHLY", today=TODAY)
    first.refresh_from_db()
    assert first.status == SubscriptionInvoice.Status.VOID


def test_downgrading_below_usage_is_refused(free_on, owner):
    units(owner, 6)
    assert entitlements.over_limits(owner.organization, plan("free"))
    with pytest.raises(ValidationError, match="Remove some first"):
        services.choose_plan(owner, plan("free"), "MONTHLY", today=TODAY)
    assert services.choose_plan(owner, plan("starter"), "MONTHLY", today=TODAY) is not None


def test_free_applies_at_once(free_on, owner):
    assert services.choose_plan(owner, plan("free"), "MONTHLY", today=TODAY) is None
    sub = Subscription.objects.get(organization=owner.organization)
    assert sub.status == Status.ACTIVE and sub.plan.key == "free"


def test_enterprise_and_managers_without_the_capability_cannot_choose(owner):
    with pytest.raises(ValidationError):
        services.choose_plan(owner, plan("enterprise"), "MONTHLY", today=TODAY)
    viewer = add_member(owner.organization, "viewer", all_properties=True)
    with pytest.raises(PermissionDenied):
        services.choose_plan(viewer, plan("starter"), "MONTHLY", today=TODAY)


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------


def test_units_are_limited_by_the_plan(free_on, owner):
    services.choose_plan(owner, plan("free"), "MONTHLY", today=TODAY)
    prop = units(owner, 5)
    with pytest.raises(ValidationError, match="allows 5 units"):
        property_services.create_unit(owner, prop, code="EXTRA")


def test_seats_count_invitations_but_not_viewers(free_on, owner):
    org = owner.organization
    services.choose_plan(owner, plan("free"), "MONTHLY", today=TODAY)  # 2 seats; the owner is one
    add_member(org, "viewer")
    account_services.invite_staff(owner, phone="0799000111", role=role(org, "viewer"))
    account_services.invite_staff(owner, phone="0799000112", role=role(org, "accountant"))
    assert entitlements.usage(org)["seats"] == 2
    with pytest.raises(ValidationError, match="team members"):
        account_services.invite_staff(owner, phone="0799000113", role=role(org, "accountant"))
    account_services.invite_staff(owner, phone="0799000114", role=role(org, "viewer"))  # viewers are free


# ---------------------------------------------------------------------------
# The daily job
# ---------------------------------------------------------------------------


def _trial_ends(owner, day):
    Subscription.objects.filter(organization=owner.organization).update(trial_ends_on=day)


def test_a_small_trial_moves_to_free(free_on, owner):
    _trial_ends(owner, TODAY - datetime.timedelta(days=1))
    assert services.daily(TODAY)["trial_to_free"] == 1
    sub = Subscription.objects.get(organization=owner.organization)
    assert sub.plan.key == "free" and sub.status == Status.ACTIVE


def test_a_bigger_trial_must_pay_then_lapses_then_payment_restores(free_on, owner, admin):
    units(owner, 6)
    _trial_ends(owner, TODAY - datetime.timedelta(days=1))
    assert services.daily(TODAY)["trial_ended"] == 1
    sub = Subscription.objects.get(organization=owner.organization)
    invoice = services.open_invoice(sub)
    assert sub.status == Status.PAST_DUE and invoice.reason == "TRIAL_END" and invoice.plan_name == "Lite"
    assert services.banner(owner.organization, TODAY)["level"] == "warning"

    assert services.daily(TODAY + datetime.timedelta(days=13))["lapsed"] == 0
    assert services.daily(TODAY + datetime.timedelta(days=14))["lapsed"] == 1
    org = Organization.objects.get(pk=owner.organization_id)
    assert org.status == Organization.Status.READ_ONLY
    with pytest.raises(PermissionDenied):
        property_services.create_unit(fresh(owner), make_property(org), code="NEW")

    pay(admin, invoice, on=TODAY + datetime.timedelta(days=15))
    org.refresh_from_db()
    sub.refresh_from_db()
    assert org.status == Organization.Status.ACTIVE and sub.status == Status.ACTIVE
    assert sub.period_start == TODAY + datetime.timedelta(days=15)


def test_a_frozen_organization_stays_frozen_after_payment(owner, admin):
    invoice = services.choose_plan(owner, plan("starter"), "MONTHLY", today=TODAY)
    Organization.objects.filter(pk=owner.organization_id).update(status=Organization.Status.FROZEN)
    pay(admin, invoice)
    assert Organization.objects.get(pk=owner.organization_id).status == Organization.Status.FROZEN


def test_renewal_is_invoiced_seven_days_ahead_and_overdue_goes_past_due(owner, admin):
    pay(admin, services.choose_plan(owner, plan("starter"), "MONTHLY", today=TODAY), on=TODAY)  # ends 9 Apr
    assert services.daily(datetime.date(2026, 4, 2))["renewals"] == 0
    assert services.daily(datetime.date(2026, 4, 3))["renewals"] == 1
    assert services.daily(datetime.date(2026, 4, 4))["renewals"] == 0  # once
    sub = Subscription.objects.get(organization=owner.organization)
    renewal = services.open_invoice(sub)
    assert renewal.period_start == datetime.date(2026, 4, 10) and renewal.due_on == datetime.date(2026, 4, 9)
    assert services.daily(datetime.date(2026, 4, 10))["past_due"] == 1
    pay(admin, renewal, reference="R2", on=datetime.date(2026, 4, 12))
    sub.refresh_from_db()
    # Paid late, the renewal keeps its own dates.
    assert (sub.status, sub.period_start, sub.period_end) == (
        Status.ACTIVE, datetime.date(2026, 4, 10), datetime.date(2026, 5, 9))


def test_the_trial_banner_shows_in_the_last_week(owner):
    _trial_ends(owner, TODAY + datetime.timedelta(days=10))
    assert services.banner(owner.organization, TODAY) is None
    _trial_ends(owner, TODAY + datetime.timedelta(days=3))
    assert "3 days" in services.banner(owner.organization, TODAY)["text"]


# ---------------------------------------------------------------------------
# SMS wallet
# ---------------------------------------------------------------------------


class OkSender:
    def send(self, to, body, sender_id=""):
        return SmsResult(ok=True, provider="test", provider_id="id1", cost=None)


def test_sms_is_charged_per_part_and_stops_at_zero(owner, admin, settings, monkeypatch):
    settings.SMS_WALLET_ENFORCED = True
    settings.SMS_PRICE = "1.00"
    org = owner.organization
    SmsWallet.objects.update_or_create(organization=org, defaults={"mode": SmsWallet.Mode.PREPAID})
    tenant = tenant_services.create_tenant(owner, name="Wanjiku", phone="0712345678")
    assert not delivery.sms_available(org)
    services.top_up_sms(admin, org, amount=Decimal("2"), method="MPESA", reference="TOP1", paid_on=TODAY)
    assert delivery.sms_available(org)
    monkeypatch.setattr(delivery, "get_sms_sender", OkSender)
    m = Message.objects.create(organization=org, type="announcement", tenant=tenant, channel=catalog.SMS,
                               to=tenant.phone, body="x" * 200, status=Message.Status.QUEUED)
    assert delivery.send_one(m.pk) == Message.Status.SENT
    entry = SmsWalletEntry.objects.get(message=m)
    assert entry.amount == Decimal("-2.00") and entry.balance_after == 0  # 200 characters is two parts
    assert not delivery.sms_available(org)
    assert SubscriptionPayment.objects.get(reference="TOP1").receipt.number.startswith("LPM-RCT-")


def _sms(org, tenant, body="x" * 200):
    m = Message.objects.create(organization=org, type="announcement", tenant=tenant, channel=catalog.SMS,
                               to=tenant.phone, body=body, status=Message.Status.QUEUED)
    return m, delivery.send_one(m.pk)


def test_sponsored_sms_is_counted_free_and_stops_at_the_cap(owner, settings, monkeypatch):
    settings.SMS_WALLET_ENFORCED = True
    org = owner.organization
    SmsWallet.objects.update_or_create(organization=org, defaults={"mode": SmsWallet.Mode.SPONSORED,
                                                                   "monthly_cap": 3})
    tenant = tenant_services.create_tenant(owner, name="Wanjiku", phone="0712345678")
    monkeypatch.setattr(delivery, "get_sms_sender", OkSender)
    m, status = _sms(org, tenant)  # two parts
    assert status == Message.Status.SENT
    entry = SmsWalletEntry.objects.get(message=m)
    assert entry.amount == 0 and entry.segments == 2 and services.sms_parts_used(org) == 2
    assert delivery.sms_available(org)
    _sms(org, tenant)  # two more: past the cap of 3
    assert not delivery.sms_available(org)
    m3, status = _sms(org, tenant)
    m3.refresh_from_db()
    assert status == Message.Status.SKIPPED and m3.skip_reason == Message.SkipReason.CHANNEL_UNAVAILABLE
    assert services.sms_summary(org)["left"] == 0 and services.sms_balance(org) == 0


def test_a_sponsored_cap_left_empty_means_no_cap(owner, settings):
    settings.SMS_WALLET_ENFORCED = True
    SmsWallet.objects.update_or_create(organization=owner.organization,
                                       defaults={"mode": SmsWallet.Mode.SPONSORED, "monthly_cap": None})
    assert delivery.sms_available(owner.organization)


def test_bundled_sms_uses_the_allowance_then_the_wallet(owner, admin, settings, monkeypatch):
    settings.SMS_WALLET_ENFORCED = True
    settings.SMS_PRICE = "1.00"
    org = owner.organization
    SmsWallet.objects.update_or_create(organization=org, defaults={"mode": SmsWallet.Mode.BUNDLED,
                                                                   "monthly_cap": 3})
    tenant = tenant_services.create_tenant(owner, name="Wanjiku", phone="0712345678")
    monkeypatch.setattr(delivery, "get_sms_sender", OkSender)
    _sms(org, tenant)  # 2 of 3 included parts
    m2, _status = _sms(org, tenant)  # 1 included, 1 from the wallet
    assert SmsWalletEntry.objects.get(message=m2).amount == Decimal("-1.00")  # empty wallet: goes negative
    assert services.sms_balance(org) == Decimal("-1.00")
    assert not delivery.sms_available(org)
    services.top_up_sms(admin, org, amount=Decimal("5"), method="MPESA", reference="TOP2", paid_on=TODAY)
    assert delivery.sms_available(org)


def test_the_admin_list_totals_sms_per_organization(client, owner, admin, settings, monkeypatch):
    org = owner.organization
    tenant = tenant_services.create_tenant(owner, name="Wanjiku", phone="0712345678")
    monkeypatch.setattr(delivery, "get_sms_sender", OkSender)
    _sms(org, tenant)
    from accounts import mfa

    monkeypatch.setattr(mfa, "is_enabled", lambda user: True)
    monkeypatch.setattr(mfa, "session_verified", lambda request: True)
    admin.is_superuser = True
    admin.save()
    client.login(username=admin.phone, password=PASSWORD)
    page = client.get(reverse("admin:subscriptions_smswallet_changelist"))
    assert page.status_code == 200
    assert org.name in page.content.decode() and "SMS this month: 2 parts" in page.content.decode()


def test_sms_is_free_when_the_wallet_is_not_enforced(owner):
    assert delivery.sms_available(owner.organization)
    assert services.segments("x" * 160) == 1 and services.segments("x" * 161) == 2 and services.segments("") == 0


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def test_the_subscription_page(client, owner, admin):
    login(client, owner)
    page = client.get(reverse("subscriptions:page")).content.decode()
    assert "Business" in page and "Choose a plan" in page
    response = client.post(reverse("subscriptions:page"), {"plan": "starter", "interval": "MONTHLY"}, follow=True)
    assert "Invoice LPM-INV-" in response.content.decode()
    invoice = SubscriptionInvoice.objects.get(organization=owner.organization)
    pdf = client.get(reverse("subscriptions:invoice_pdf", args=[invoice.public_id]))
    assert pdf["Content-Type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    payment = pay(admin, invoice)
    pdf = client.get(reverse("subscriptions:receipt_pdf", args=[payment.receipt.public_id]))
    assert pdf.content.startswith(b"%PDF")


def test_other_organizations_cannot_see_our_documents(client, owner):
    invoice = services.choose_plan(owner, plan("starter"), "MONTHLY", today=TODAY)
    other = make_org()
    login(client, other)
    assert client.get(reverse("subscriptions:invoice_pdf", args=[invoice.public_id])).status_code == 404
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    client.logout()
    client.login(username=caretaker.user.phone, password=PASSWORD)
    assert client.get(reverse("subscriptions:page")).status_code == 403


def test_admin_records_a_payment(client, owner, admin, monkeypatch):
    from accounts import mfa

    monkeypatch.setattr(mfa, "is_enabled", lambda user: True)
    monkeypatch.setattr(mfa, "session_verified", lambda request: True)
    invoice = services.choose_plan(owner, plan("starter"), "MONTHLY", today=TODAY)
    client.login(username=admin.phone, password=PASSWORD)
    response = client.post(reverse("admin:subscriptions_subscriptionpayment_add"), {
        "invoice": invoice.pk, "amount": "799", "method": "MPESA", "reference": "qab123", "paid_on": "2026-03-10"})
    assert response.status_code == 302, response.content.decode()[:2000]
    invoice.refresh_from_db()
    assert invoice.status == SubscriptionInvoice.Status.PAID
    assert SubscriptionPayment.objects.get().reference == "QAB123"
    assert client.get(reverse("admin:subscriptions_invoice_pdf", args=[invoice.pk])).content.startswith(b"%PDF")


def test_the_page_shows_photo_scans_and_questions_this_month(owner, client, settings):
    from ai_assistant.models import Question
    from meters.models import Scan

    settings.SCAN_MONTHLY_LIMIT = 100
    org = owner.organization
    for kind, status in (("NOTEBOOK", "READ"), ("NOTEBOOK", "UNCLEAR"), ("BILL", "READ"), ("BILL", "FAILED")):
        Scan.objects.create(organization=org, kind=kind, status=status)
    Question.objects.create(organization=org, user=owner.user, question="who owes", status="ANSWERED")
    Question.objects.create(organization=org, question="my balance", status="ANSWERED", channel="WHATSAPP")
    Question.objects.create(organization=org, user=owner.user, question="again", status="LIMITED")
    login(client, owner)
    page = client.get(reverse("subscriptions:page")).content.decode()
    assert "3 photo scans this month" in page and "97 left" in page
    assert "Meter notebooks: 2 · utility bills: 1" in page
    assert "2 questions asked of the assistant this month" in page and "(staff 1, tenants 1)" in page


def test_a_plan_can_carry_a_tag_shown_on_its_card(owner, client):
    Plan.objects.filter(key="starter").update(badge="Most popular")
    login(client, owner)
    assert b"Most popular" in client.get(reverse("subscriptions:page")).content


def test_sms_warnings_go_once_each_to_the_landlord_and_us(owner, settings, monkeypatch,
                                                          django_capture_on_commit_callbacks):
    from django.core import mail

    settings.SMS_WALLET_ENFORCED = True
    settings.OPS_ALERT_EMAILS = "ops@example.com"
    org = owner.organization
    org.billing_email = "pay@acme.example"
    org.save()
    SmsWallet.objects.update_or_create(organization=org, defaults={"mode": SmsWallet.Mode.SPONSORED,
                                                                   "monthly_cap": 5})
    tenant = tenant_services.create_tenant(owner, name="Wanjiku", phone="0712345678")
    monkeypatch.setattr(delivery, "get_sms_sender", OkSender)
    with django_capture_on_commit_callbacks(execute=True):
        _sms(org, tenant, "x" * 100)
        _sms(org, tenant, "x" * 100)
        _sms(org, tenant, "x" * 100)  # 3 of 5: no warning yet
    assert not mail.outbox
    with django_capture_on_commit_callbacks(execute=True):
        _sms(org, tenant, "x" * 100)  # 4 of 5: 80%
    assert [m.to for m in mail.outbox] == [["pay@acme.example"]] and "4 of your 5" in mail.outbox[0].body
    with django_capture_on_commit_callbacks(execute=True):
        _sms(org, tenant, "x" * 100)  # 5 of 5: used up, and we hear about it
        _sms(org, tenant, "x" * 100)  # skipped, no new warning
    assert [m.to for m in mail.outbox[1:]] == [["pay@acme.example"], ["ops@example.com"]]
    assert "reached its SMS cap" in mail.outbox[2].subject


def test_prepaid_low_credit_warns_and_a_top_up_rearms_it(owner, admin, settings, monkeypatch,
                                                        django_capture_on_commit_callbacks):
    from django.core import mail

    settings.SMS_WALLET_ENFORCED = True
    settings.SMS_PRICE = "1.00"
    org = owner.organization
    org.billing_email = "pay@acme.example"
    org.save()
    SmsWallet.objects.update_or_create(organization=org, defaults={"mode": SmsWallet.Mode.PREPAID})
    services.top_up_sms(admin, org, amount=Decimal("21"), method="MPESA", reference="TOP3", paid_on=TODAY)
    tenant = tenant_services.create_tenant(owner, name="Wanjiku", phone="0712345678")
    monkeypatch.setattr(delivery, "get_sms_sender", OkSender)
    with django_capture_on_commit_callbacks(execute=True):
        _sms(org, tenant)  # 19 left: below 20 parts
        _sms(org, tenant)
    assert len(mail.outbox) == 1 and "credit is low" in mail.outbox[0].body
    services.top_up_sms(admin, org, amount=Decimal("2"), method="MPESA", reference="TOP4", paid_on=TODAY)
    with django_capture_on_commit_callbacks(execute=True):
        _sms(org, tenant)
    assert len(mail.outbox) == 2
