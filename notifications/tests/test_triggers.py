"""Triggers: invoice issued, payment received, payment waiting for review and daily reminders (D-044 step 2)."""

import datetime

import pytest
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from billing import invoicing, jobs
from billing.tests.test_invoicing import FEB, bill, make_lease
from leases import services as lease_services
from notifications import catalog, triggers
from notifications.models import Message, OrganizationNotificationRule
from payments import services as payment_services
from payments.models import Payment
from properties import services as property_services
from tenants import services as tenant_services

pytestmark = pytest.mark.django_db

D = datetime.date
Status = Message.Status
ISSUE_DAY = D(2026, 1, 27)


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.SITE_URL = "https://pms.example.com"


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def org(owner):
    return owner.organization


@pytest.fixture
def prop(owner):
    return make_property(owner.organization)


@pytest.fixture
def lease(owner, prop):
    # Due on the 5th, overdue after the 8th.
    return make_lease(owner, prop, due_day=5, grace_days=3)


def messages(type_codename):
    return list(Message.objects.filter(type=type_codename).order_by("pk"))


def pay(actor, lease, amount, **kw):
    kw.setdefault("method", Payment.Method.CASH)
    kw.setdefault("paid_at", D(2026, 2, 3))
    return payment_services.record_payment(actor, lease, amount=amount, **kw)


def run_month(org, today=ISSUE_DAY):
    return invoicing.generate_month(org, FEB, today=today)


# ---------------------------------------------------------------------------
# Invoice issued
# ---------------------------------------------------------------------------


def test_monthly_run_queues_one_sms_per_invoice_without_sending_inline(org, lease, outbox,
                                                                        django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        [invoice] = run_month(org).invoices
    [m] = messages("invoice_issued")
    tenant = lease.primary_tenant
    assert (m.status, m.channel, m.tenant, m.invoice, m.lease) == (Status.QUEUED, catalog.SMS, tenant, invoice, lease)
    assert m.body == (f"Dear {tenant.name}, invoice {invoice.number} for A1 of KES 15,000.00 is due on 05/02/2026. "
                      f"Balance: KES 15,000.00. Pay with reference {lease.unit.payment_reference}. {org.name}")
    assert outbox == []  # left for send_due_messages
    assert run_month(org).invoices == [] and len(messages("invoice_issued")) == 1


def test_billing_a_month_again_after_a_correction_tells_nobody(lease):
    bill(lease, FEB)
    assert messages("invoice_issued") == []


def test_co_tenants_get_it_only_when_the_rule_says_so(owner, org, prop):
    unit = property_services.create_unit(owner, prop, code="B2")
    a = tenant_services.create_tenant(owner, name="Akinyi", phone="0712000001")
    b = tenant_services.create_tenant(owner, name="Baraka", phone="0712000002", language=catalog.SW)
    lease_services.activate_lease(owner, lease_services.create_lease(
        owner, unit=unit, tenants=[a, b], start_date=D(2026, 1, 1), end_date=None, rent=10000))
    OrganizationNotificationRule.objects.create(organization=org, type="invoice_issued", include_co_tenants=True)
    run_month(org)
    first, second = messages("invoice_issued")
    assert (first.tenant, second.tenant) == (a, b)
    assert second.language == catalog.SW and second.body.startswith("Mpendwa Baraka, ankara")


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------


def test_confirmed_payment_sends_the_payer_a_receipt_link_after_commit(owner, lease, outbox,
                                                                       django_capture_on_commit_callbacks):
    bill(lease, FEB)
    with django_capture_on_commit_callbacks(execute=True):
        payment = pay(owner, lease, "10000")
    [m] = messages("payment_received")
    link = "https://pms.example.com" + reverse("receipt_link", args=[payment.receipt.share_token])
    assert m.status == Status.SENT and m.payment == payment and m.created_by == owner.user
    assert f"Receipt {payment.receipt.number}. Balance: KES 5,000.00. {link}" in m.body
    assert [body for _to, body in outbox] == [m.body]


def test_pending_payment_tells_confirmers_in_app_and_the_tenant_only_once_confirmed(owner, org, prop, lease):
    accountant = add_member(org, "accountant", all_properties=True)
    payment = pay(accountant, lease, "5000")
    [m] = messages("payment_pending_review")
    assert (m.user, m.channel, m.status) == (owner.user, catalog.IN_APP, Status.DELIVERED)
    assert m.body == f"{accountant.user} recorded KES 5,000.00 from {lease.primary_tenant.name} (A1). " \
                     "It needs review."
    assert messages("payment_received") == []
    payment_services.confirm_payment(owner, payment)
    [received] = messages("payment_received")
    assert received.created_by == owner.user


def test_receipt_link_serves_the_pdf_until_the_payment_is_reversed(client, owner, lease):
    payment = pay(owner, lease, "10000")
    url = reverse("receipt_link", args=[payment.receipt.share_token])
    response = client.get(url)
    assert response.status_code == 200 and response["Content-Type"] == "application/pdf"
    assert response["X-Robots-Tag"] == "noindex, nofollow"
    assert client.get(reverse("receipt_link", args=["nope"])).status_code == 404
    payment_services.reverse_payment(owner, payment, reason="Bounced")
    assert client.get(url).status_code == 404


# ---------------------------------------------------------------------------
# Daily reminders
# ---------------------------------------------------------------------------


def issued(lease, today=ISSUE_DAY):
    return bill(lease, FEB, today=today)


@pytest.mark.parametrize("today, sent", [
    (D(2026, 2, 1), 0),  # four days before: too early
    (D(2026, 2, 2), 1),  # three days before
    (D(2026, 2, 5), 1),  # the job missed a few days: still before or on the due date
    (D(2026, 2, 6), 0),  # past due: that is the overdue reminder's job
])
def test_rent_due_soon_three_days_before(lease, today, sent):
    issued(lease)
    assert triggers.send_reminders(today)["rent_due_soon"] == sent


def test_due_soon_is_sent_once_and_names_what_is_still_owed(owner, lease):
    invoice = issued(lease)
    pay(owner, lease, "4000", paid_at=D(2026, 1, 30))
    triggers.send_reminders(D(2026, 2, 2))
    triggers.send_reminders(D(2026, 2, 3))
    [m] = messages("rent_due_soon")
    assert m.invoice == invoice and m.status == Status.QUEUED
    assert "a reminder that KES 11,000.00 for A1 is due on 05/02/2026" in m.body


def test_no_due_soon_when_the_invoice_went_out_inside_the_window(lease):
    issued(lease, today=D(2026, 2, 3))
    assert triggers.send_reminders(D(2026, 2, 3))["rent_due_soon"] == 0


def test_paid_invoice_gets_no_reminders(owner, lease):
    issued(lease)
    pay(owner, lease, "15000", paid_at=D(2026, 1, 30))
    assert triggers.send_reminders(D(2026, 2, 2)) == {"rent_due_soon": 0, "rent_overdue": 0}
    assert triggers.send_reminders(D(2026, 2, 10)) == {"rent_due_soon": 0, "rent_overdue": 0}


@pytest.mark.parametrize("today, sent", [
    (D(2026, 2, 9), 0),   # first overdue day; the default reminder is on the second
    (D(2026, 2, 10), 1),
    (D(2026, 2, 12), 1),  # two days late: caught up
    (D(2026, 2, 13), 0),  # three days late: dropped rather than sent stale
])
def test_rent_overdue_two_days_after_grace(lease, today, sent):
    issued(lease)
    assert triggers.send_reminders(today)["rent_overdue"] == sent


def test_overdue_reminders_follow_the_rule_offsets_once_each(org, lease):
    OrganizationNotificationRule.objects.create(organization=org, type="rent_overdue", offsets=[1, 7])
    issued(lease)
    for day in range(9, 20):
        triggers.send_reminders(D(2026, 2, day))
    keys = [m.dedupe_key.split(":")[2] for m in messages("rent_overdue")]
    assert keys == ["1", "7"]
    assert "Total balance: KES 15,000.00" in messages("rent_overdue")[0].body


def test_switched_off_reminders_write_nothing(org, lease):
    OrganizationNotificationRule.objects.create(organization=org, type="rent_due_soon", enabled=False)
    issued(lease)
    assert triggers.send_reminders(D(2026, 2, 2))["rent_due_soon"] == 0
    assert messages("rent_due_soon") == []


def test_daily_job_bills_then_reminds(org, lease):
    counts = jobs.run_daily(D(2026, 2, 2))
    assert counts["invoices"] == 1 and "rent_due_soon" in counts and "rent_overdue" in counts
    assert len(messages("invoice_issued")) == 1
