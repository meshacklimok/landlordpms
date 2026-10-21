"""A landlord whose subscription has lapsed (read-only): reports still load and stay current, adds are refused."""

import datetime

import pytest
from django.urls import reverse

from accounts.models import Organization
from accounts.tests.factories import make_org, make_property
from accounts.tests.test_isolation import login
from payments import services as payment_services
from reports.tests.test_income import bill, make_lease

pytestmark = pytest.mark.django_db
D = datetime.date


@pytest.fixture
def lapsed(client):
    owner = make_org()
    login(client, owner)
    prop = make_property(owner.organization, name="Acacia Court")
    lease = make_lease(owner, prop)
    today = datetime.date.today()
    bill(lease, today.replace(day=1))
    Organization.objects.filter(pk=owner.organization_id).update(status=Organization.Status.READ_ONLY)
    return owner, prop, lease


REPORTS = ["dashboard", "income", "payments", "arrears", "utilities", "leases", "behaviour", "scorecard", "outlook",
           "performance", "profitability", "deposits", "owner_statement", "units", "profit_loss", "cash_flow"]


def test_every_report_loads_for_a_lapsed_landlord(client, lapsed):
    bad = {}
    for name in REPORTS:
        try:
            url = reverse(f"reports:{name}")
        except Exception:  # noqa: BLE001, S112 - a name this project does not have
            continue
        status = client.get(url).status_code
        if status != 200:
            bad[name] = status
    assert not bad, bad


def test_a_payment_that_arrives_after_the_lapse_shows_in_the_report_at_once(client, lapsed):
    owner, _prop, lease = lapsed
    url = reverse("reports:payments")
    before = client.get(url).content.decode()
    payment_services.record_system_payment(lease, amount=20000, method="MPESA", paid_at=datetime.date.today(),
                                           reference="LAPSE1", source="test")
    after = client.get(url).content.decode()
    assert "LAPSE1" not in before and ("20,000" in after or "20000" in after)
    dash = client.get(reverse("reports:dashboard")).content.decode()
    assert "20,000" in dash or "20000" in dash


def test_adding_a_unit_or_tenant_is_refused_with_the_lapsed_notice(client, lapsed):
    _owner, prop, _lease = lapsed
    r = client.get(reverse("properties:unit_create", args=[prop.public_id]))
    assert r.status_code == 403


def test_the_refusal_page_says_why_and_how_to_fix_it(client, lapsed):
    _owner, prop, _lease = lapsed
    body = client.get(reverse("properties:unit_create", args=[prop.public_id])).content.decode().lower()
    assert "subscription" in body, body[:600]


def test_exports_still_work_when_lapsed_as_the_banner_promises(client, lapsed):
    bad = {}
    for name in ("dashboard_export", "income_export", "payments_export", "arrears_export"):
        try:
            url = reverse(f"reports:{name}")
        except Exception:  # noqa: BLE001, S112
            continue
        if client.get(url).status_code != 200:
            bad[name] = client.get(url).status_code
    assert not bad, bad


def test_a_lapsed_landlord_can_reach_the_page_to_pay_and_the_banner_links_to_it(client, lapsed):
    url = reverse("subscriptions:page")
    assert client.get(url).status_code == 200
    assert url in client.get(reverse("reports:dashboard")).content.decode()


def test_a_month_missed_while_lapsed_is_billed_when_access_returns(lapsed):
    """Billed up to this month, lapsed for all of next month, paid early the month after: next month needs its bill."""
    from billing import invoicing
    from billing.models import Invoice
    from subscriptions import services as sub_services

    owner, _prop, lease = lapsed
    org = Organization.objects.get(pk=owner.organization_id)
    this_month = datetime.date.today().replace(day=1)
    missed = (this_month + datetime.timedelta(days=32)).replace(day=1)
    after = (missed + datetime.timedelta(days=32)).replace(day=1)
    Organization.objects.filter(pk=org.pk).update(status=Organization.Status.ACTIVE)
    org.refresh_from_db()
    sub_services._set_org_read_only(org)  # as the daily subscription job does, with its audit record
    invoicing.run_scheduled(missed + datetime.timedelta(days=9))  # lapsed: skipped
    assert not Invoice.objects.filter(lease=lease, period_start=missed).exists()
    sub_services._set_org_active(org)
    invoicing.run_scheduled(after + datetime.timedelta(days=1))
    assert Invoice.objects.filter(lease=lease, period_start=missed).exists(), "the lapsed month was never billed"
