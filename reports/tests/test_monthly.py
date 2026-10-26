"""The monthly property report email (D-194)."""

import datetime

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from accounts.tests.factories import make_org, make_property
from reports import monthly
from reports.models import MonthlyReportSent
from reports.tests.test_income import bill, make_lease, pay

pytestmark = pytest.mark.django_db

D = datetime.date
FIRST = D(2025, 4, 1)  # the report is for March


@pytest.fixture
def owner():
    m = make_org(name="Acme")
    m.user.email = "owner@example.com"
    m.user.email_verified_at = timezone.now()
    m.user.save()
    return m


@pytest.fixture
def prop(owner):
    return make_property(owner.organization, name="Acacia Court")


def test_report_separates_facts_observations_and_suggestions(owner, prop):
    a = make_lease(owner, prop, code="A1")
    make_lease(owner, prop, code="A2")
    bill(a, D(2025, 3, 1))
    pay(owner, a, 5000, D(2025, 3, 6))
    report = monthly.build(owner, D(2025, 3, 1), FIRST)
    assert report.facts and any("Rent billed" in f for f in report.facts)
    assert any("Follow up" in x for x in report.actions)
    text = monthly.body(report, "Amina", "https://x/dash", "https://x/stop")
    assert text.index("What the records show") < text.index("Suggested next steps")
    assert "suggestions, not facts" in text and "https://x/stop" in text


def test_send_all_emails_once_per_member_per_month(owner, prop):
    make_lease(owner, prop, code="A1")
    assert monthly.send_all(FIRST)["sent"] == 1
    assert len(mail.outbox) == 1 and mail.outbox[0].to == ["owner@example.com"]
    assert MonthlyReportSent.objects.filter(membership=owner, month=D(2025, 3, 1)).exists()
    again = monthly.send_all(FIRST)
    assert again["sent"] == 0 and len(mail.outbox) == 1


def test_an_unconfirmed_address_gets_no_report(owner, prop):
    make_lease(owner, prop, code="A1")
    owner.user.email_verified_at = None
    owner.user.save()
    assert monthly.send_all(FIRST)["sent"] == 0 and not mail.outbox
    assert not MonthlyReportSent.objects.exists()


def test_no_properties_no_email(owner):
    assert monthly.send_all(FIRST)["sent"] == 0 and not mail.outbox
    assert not MonthlyReportSent.objects.exists()


def test_stop_link_turns_the_report_off(client, owner, prop):
    url = reverse("monthly_report_stop", args=[monthly.stop_token(owner)])
    assert client.get(url).status_code == 200
    owner.refresh_from_db()
    assert owner.monthly_report
    assert client.post(url).status_code == 200
    owner.refresh_from_db()
    assert not owner.monthly_report
    make_lease(owner, prop, code="A1")
    assert monthly.send_all(FIRST)["sent"] == 0
    assert client.get(reverse("monthly_report_stop", args=["bad"])).status_code == 404
