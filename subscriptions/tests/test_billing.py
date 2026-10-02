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
from core.sms import SmsResult
from notifications import catalog, delivery
from notifications.models import Message
from properties import services as property_services
from subscriptions import entitlements, services
from subscriptions.models import (
    Plan,
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
    assert keys == ["free", "starter", "business", "professional", "enterprise"]
    assert plan("free").is_free and plan("business").unit_limit == 100 and plan("enterprise").unit_limit is None
    assert not plan("enterprise").self_serve


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


def test_downgrading_below_usage_is_refused(owner):
    units(owner, 6)
    assert entitlements.over_limits(owner.organization, plan("free"))
    with pytest.raises(ValidationError, match="Remove some first"):
        services.choose_plan(owner, plan("free"), "MONTHLY", today=TODAY)
    assert services.choose_plan(owner, plan("starter"), "MONTHLY", today=TODAY) is not None


def test_free_applies_at_once(owner):
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


def test_units_are_limited_by_the_plan(owner):
    services.choose_plan(owner, plan("free"), "MONTHLY", today=TODAY)
    prop = units(owner, 5)
    with pytest.raises(ValidationError, match="allows 5 units"):
        property_services.create_unit(owner, prop, code="EXTRA")


def test_seats_count_invitations_but_not_viewers(owner):
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


def test_a_small_trial_moves_to_free(owner):
    _trial_ends(owner, TODAY - datetime.timedelta(days=1))
    assert services.daily(TODAY)["trial_to_free"] == 1
    sub = Subscription.objects.get(organization=owner.organization)
    assert sub.plan.key == "free" and sub.status == Status.ACTIVE


def test_a_bigger_trial_must_pay_then_lapses_then_payment_restores(owner, admin):
    units(owner, 6)
    _trial_ends(owner, TODAY - datetime.timedelta(days=1))
    assert services.daily(TODAY)["trial_ended"] == 1
    sub = Subscription.objects.get(organization=owner.organization)
    invoice = services.open_invoice(sub)
    assert sub.status == Status.PAST_DUE and invoice.reason == "TRIAL_END" and invoice.plan_name == "Business"
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
    def send(self, to, body):
        return SmsResult(ok=True, provider="test", provider_id="id1", cost=None)


def test_sms_is_charged_per_part_and_stops_at_zero(owner, admin, settings, monkeypatch):
    settings.SMS_WALLET_ENFORCED = True
    settings.SMS_PRICE = "1.00"
    org = owner.organization
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
