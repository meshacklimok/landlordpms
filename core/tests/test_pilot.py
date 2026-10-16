"""The pilot report: each organization against doc 02's success measures (D-101)."""

import datetime
import io

import pytest
from django.core.management import CommandError, call_command

from accounts.tests.factories import make_org, make_property
from billing.tests.test_invoicing import FEB, bill, make_lease
from core import pilot
from mpesa import c2b, services

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def _pay(creds, code, ref):
    c2b.receive(creds, {"TransactionType": "Pay Bill", "TransID": code, "TransTime": "20260203101500",
                        "TransAmount": "15000.00", "BusinessShortCode": "600123", "BillRefNumber": ref,
                        "MSISDN": "254799000111", "FirstName": "Jane", "MiddleName": "", "LastName": "Doe"})


def test_scores_count_the_month_and_name_what_is_missed():
    owner = make_org(name="Acme")
    lease = make_lease(owner, make_property(owner.organization))
    bill(lease, FEB)
    account = services.add_account(owner, type="PAYBILL", number="600123", display_name="Rent")
    creds = services.save_credentials(owner, account, environment="SANDBOX", consumer_key="k", consumer_secret="s")
    _pay(creds, "QAB12CD34E", lease.unit.payment_reference)
    _pay(creds, "QAB12CD34F", "")
    make_org(name="Quiet Lets")

    acme, quiet = pilot.scores(datetime.date(2026, 2, 1))
    assert (acme.organization, acme.invoices, acme.payments, acme.collected) == ("Acme", 1, 1, 15000)
    assert (acme.auto, acme.by_hand, acme.waiting, acme.auto_share) == (1, 0, 1, 50)
    assert acme.setup_minutes is not None and acme.setup_minutes <= pilot.SETUP_MINUTES
    assert acme.misses == ["matching"]
    assert (quiet.setup_minutes, quiet.auto_share, quiet.misses) == (None, None, ["set-up", "rent cycle", "matching"])
    assert pilot.scores(datetime.date(2026, 3, 1))[0].payments == 0


def test_the_command_writes_a_table_or_csv():
    owner = make_org(name="Acme")
    out = io.StringIO()
    call_command("pilot_report", "--month", "2026-02", stdout=out)
    assert "Pilot measures for February 2026" in out.getvalue() and "Acme: set-up not yet" in out.getvalue()
    out = io.StringIO()
    call_command("pilot_report", "--month", "2026-02", "--csv", "--org", str(owner.organization.public_id), stdout=out)
    header, row = out.getvalue().splitlines()
    assert header.startswith("organization,setup_minutes") and row.startswith("Acme,")
    with pytest.raises(CommandError):
        call_command("pilot_report", "--month", "Feb")
