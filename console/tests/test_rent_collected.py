"""Tenant rent collected across all organizations, split by how it reached the site."""

import pytest
from django.utils import timezone

from accounts.tests.factories import make_org, make_property
from billing.tests.test_invoicing import make_lease
from console import insights
from mpesa import services
from mpesa.models import MpesaTransaction
from payments.models import Payment

from .test_console import owner as console_owner  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def paid():
    landlord = make_org(name="Acme")
    lease = make_lease(landlord, make_property(landlord.organization))
    account = services.add_account(landlord, type="PAYBILL", number="600123", display_name="Rent Paybill")
    today = timezone.localdate()

    def pay(amount, method, status=Payment.Status.CONFIRMED):
        p = Payment.objects.create(organization=landlord.organization, lease=lease, amount=amount, method=method,
                                   paid_at=today)
        Payment.objects.filter(pk=p.pk).update(status=status)
        return p

    def via(payment, source, trans_id):
        MpesaTransaction.objects.create(organization=landlord.organization, payment_account=account, source=source,
                                        trans_id=trans_id, amount=payment.amount, paid_at=timezone.now(),
                                        payment=payment)

    via(pay(1000, "MPESA"), MpesaTransaction.Source.C2B, "SITE1")
    via(pay(2000, "MPESA"), MpesaTransaction.Source.STK, "SITE2")
    via(pay(400, "MPESA"), MpesaTransaction.Source.STATEMENT, "STM1")
    pay(300, "CASH")
    pay(9999, "CASH", Payment.Status.REVERSED)
    return landlord


def test_rent_is_split_by_how_it_arrived(paid):
    data = insights.rent_collected()
    by_channel = {r["channel"]: r for r in data["rows"]}
    assert by_channel["Through the site"]["ever"] == 3000
    assert by_channel["Statement imports"]["ever"] == 400
    assert by_channel["Recorded by hand"]["ever"] == 300
    # A reversed payment is not rent collected.
    assert data["ever_total"] == 3700 and data["month_total"] == 3700 and data["last_total"] == 3700
    assert data["site_share"] == round(3000 * 100 / 3700)


def test_nothing_collected_yet():
    data = insights.rent_collected()
    assert data["ever_total"] == 0 and data["site_share"] in (None, 0)


def test_overview_counts_tenants_and_all_time_rent(paid):
    from console import metrics
    f = metrics.overview()
    assert f["rent_all_time"] == 3700 and f["payments_all_time"] == 4
    assert f["tenants_served"] >= 1 and f["tenants_housed"] <= f["tenants_served"]


def test_messages_are_counted_by_category_and_channel(paid):
    from notifications.models import Message
    org = paid.organization
    Message.objects.create(organization=org, type="invoice_issued", channel="SMS", status="SENT", user=paid.user)
    Message.objects.create(organization=org, type="invoice_issued", channel="EMAIL", status="SENT", user=paid.user)
    Message.objects.create(organization=org, type="invoice_issued", channel="SMS", status="FAILED", user=paid.user)
    data = insights.message_categories()
    row = next(r for r in data["rows"] if "nvoice" in r["type"])
    assert row["month"] == 2 and row["ever"] == 2


def test_a_seat_without_fix_cannot_retry_but_can_read(rf):
    from console import access
    assert "fix" in access.ROLE_PRESETS["ENGINEER"] and "fix" not in access.ROLE_PRESETS["ANALYST"]
    assert {"support", "support_solve", "tools", "fix"} <= set(access.CAPABILITIES)
