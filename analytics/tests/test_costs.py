"""What each landlord cost us in a month (D-145): SMS, photo scans and assistant questions, priced."""

import datetime
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.tests.factories import make_org
from ai_assistant.models import Question
from analytics import insights
from audit.models import AuditEvent
from meters.models import Scan
from notifications.models import Message
from subscriptions.models import SmsWalletEntry

from .test_console import owner, seat_for, signed_in

pytestmark = pytest.mark.django_db


@pytest.fixture
def usage(settings):
    settings.AI_PRICE_INPUT_PER_M, settings.AI_PRICE_OUTPUT_PER_M, settings.USD_TO_KES = "2", "12", "130"
    big, small = make_org(), make_org()
    big.organization.name, small.organization.name = "Big Lets", "Small Lets"
    big.organization.save(update_fields=["name"])
    small.organization.save(update_fields=["name"])
    org = big.organization
    for status, cost in (("READ", "0.53"), ("UNCLEAR", "0.40"), ("FAILED", "0")):
        Scan.objects.create(organization=org, kind="NOTEBOOK", status=status, cost=Decimal(cost))
    Question.objects.create(organization=org, user=big.user, question="who owes", status="ANSWERED",
                            input_tokens=1_000_000, output_tokens=0)
    Question.objects.create(organization=org, user=big.user, question="again", status="LIMITED")
    message = Message.objects.create(organization=org, type="invoice_issued", channel="SMS", status="SENT",
                                     user=big.user, cost=Decimal("0.8000"))
    SmsWalletEntry.objects.create(organization=org, kind="MESSAGE", amount=Decimal("-1.00"),
                                  balance_after=Decimal("0"), segments=2, message=message)
    Scan.objects.create(organization=small.organization, kind="BILL", status="READ", cost=Decimal("0.60"))
    return big, small


def test_each_landlord_is_priced_and_the_dearest_comes_first(usage):
    data = insights.costs_by_organization(timezone.localdate())
    big, small = data["rows"]
    assert (big["org"].name, small["org"].name) == ("Big Lets", "Small Lets")
    assert (big["sms_parts"], big["sms_cost"], big["sms_billed"]) == (2, Decimal("0.8000"), Decimal("1.00"))
    # Two scans reached the model; the failed one is free and not counted.
    assert (big["scans"], big["scan_cost"]) == (2, Decimal("0.93"))
    # The question over the limit never reached the model. 1M tokens in at $2 × 130 = KES 260.
    assert (big["questions"], big["ai_cost"]) == (1, Decimal("260.0000"))
    assert big["total"] == Decimal("261.7300")
    assert data["totals"]["scans"] == 3 and data["totals"]["total"] == Decimal("262.3300")


def test_other_months_are_separate(usage):
    last_month = (timezone.localdate().replace(day=1) - datetime.timedelta(days=1))
    assert insights.costs_by_organization(last_month)["rows"] == []


def test_the_page_and_csv(client, usage):
    owner(client)
    url = reverse("console:costs")
    page = client.get(url).content.decode()
    assert "Big Lets" in page and "KES 260.00" in page and "Costs by landlord" in page
    month = timezone.localdate().strftime("%Y-%m")
    response = client.get(url, {"month": month, "format": "csv"})
    lines = response.content.decode().splitlines()
    assert lines[0].startswith("Organization,SMS parts")
    assert lines[1] == "Big Lets,2,0.8000,1.00,2,0.9300,1,1000000,0,260.0000,261.7300"
    assert AuditEvent.objects.filter(action="console.export").exists()


def test_the_page_needs_the_finance_capability(client, usage):
    signed_in(client, seat_for(capabilities=["channels"]).user)
    assert client.get(reverse("console:costs")).status_code == 403
    signed_in(client, make_org().user)
    assert client.get(reverse("console:costs")).status_code == 404
