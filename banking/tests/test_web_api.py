"""The JSON the Next.js bank screens read and post (D-215)."""

import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from banking import services
from banking.models import BankTransaction, StatementImport
from banking.tests.test_map_and_scan import ODD
from billing.tests.test_invoicing import FEB, bill, make_lease

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def owner():
    return make_org(name="Acme")


@pytest.fixture
def lease(owner):
    lease = make_lease(owner, make_property(owner.organization))
    bill(lease, FEB)
    return lease


@pytest.fixture
def account(owner, lease):
    return services.add_bank_account(owner, bank="Equity", number="0123456789", properties=[lease.unit.property])


def post_json(client, name, payload, *args):
    return client.post(reverse(name, args=args), json.dumps(payload), content_type="application/json")


def upload(client, account, lease):
    data = ODD.replace(b"{ref}", lease.unit.payment_reference.encode())
    file = SimpleUploadedFile("a.csv", data)
    return client.post(reverse("banking:web_import"), {"account": account.pk, "file": file})


def test_a_visitor_and_a_member_without_the_capability_are_refused(client, owner):
    assert client.get(reverse("banking:web_inbox")).status_code == 401
    other = add_member(owner.organization, "caretaker", all_properties=True)
    login(client, other)
    for name in ("banking:web_import", "banking:web_inbox"):
        assert client.get(reverse(name)).status_code == 403


def test_the_import_screen_lists_accounts_and_takes_a_file(client, owner, lease, account):
    login(client, owner)
    data = client.get(reverse("banking:web_import")).json()
    assert [a["id"] for a in data["accounts"]] == [account.pk] and data["imports"] == []
    response = client.post(reverse("banking:web_import"), {"account": account.pk})
    assert response.status_code == 422 and "file" in response.json()["fields"]
    response = upload(client, account, lease)
    batch = StatementImport.objects.get()
    assert response.json() == {"id": str(batch.public_id), "status": "MAPPING", "map": True,
                               "url": reverse("banking:import_map", args=[batch.public_id])}


def test_matching_the_columns_then_applying(client, owner, lease, account):
    login(client, owner)
    upload(client, account, lease)
    batch = StatementImport.objects.get()
    page = client.get(reverse("banking:web_import_map", args=[batch.public_id])).json()
    assert "Txn Dt" in page["headers"] and page["fields"][0]["key"] == "date"
    bad = post_json(client, "banking:web_import_map", {"date": "Txn Dt"}, batch.public_id)
    assert bad.status_code == 422 and "credit" in bad.json()["fields"]
    ok = post_json(client, "banking:web_import_map",
                   {"date": "Txn Dt", "credit": "Cr Amt Kes", "description": "Narration Text"}, batch.public_id)
    assert ok.json()["redirect"] == reverse("banking:import_detail", args=[batch.public_id])

    detail = client.get(reverse("banking:web_import_detail", args=[batch.public_id])).json()
    assert detail["status"] == "PREVIEW" and len(detail["rows"]) == 1 and detail["rows"][0]["amount"] == "KES 15,000.00"
    done = post_json(client, "banking:web_import_detail", {"action": "apply"}, batch.public_id)
    assert "1 matched" in done.json()["message"]
    assert BankTransaction.objects.get().status == "MATCHED"


def test_discarding(client, owner, lease, account):
    login(client, owner)
    upload(client, account, lease)
    batch = StatementImport.objects.get()
    post_json(client, "banking:web_import_map", {"date": "Txn Dt", "credit": "Cr Amt Kes"}, batch.public_id)
    assert post_json(client, "banking:web_import_detail", {"action": "discard"}, batch.public_id).status_code == 200
    batch.refresh_from_db()
    assert batch.status == "DISCARDED"


def test_the_inbox_accepts_matches_and_ignores(client, owner, lease, account):
    login(client, owner)
    statement = StatementImport.objects.create(organization=owner.organization, payment_account=account, kind="BANK",
                                               created_by=owner.user)

    def line(n):
        return BankTransaction.objects.create(
            organization=owner.organization, payment_account=account, statement=statement, posted_on="2026-02-03",
            description=f"DEP {n}", amount="1000", fingerprint=f"f{n}", suggested_lease=lease if n == 1 else None)

    first, second, third = line(1), line(2), line(3)
    rows = client.get(reverse("banking:web_inbox")).json()
    assert rows["count"] == 3 and rows["rows"][0]["suggested"]["lease"] == lease.number
    assert post_json(client, "banking:web_inbox", {"line": str(first.public_id), "action": "accept"}).status_code == 200
    assert post_json(client, "banking:web_inbox", {"line": str(second.public_id), "action": "match",
                                                   "lease": str(lease.public_id)}).status_code == 200
    bad = post_json(client, "banking:web_inbox", {"line": str(third.public_id), "action": "ignore"})
    assert bad.status_code == 422
    assert post_json(client, "banking:web_inbox", {"line": str(third.public_id), "action": "ignore",
                                                   "reason": "Not rent"}).status_code == 200
    assert client.get(reverse("banking:web_inbox")).json()["count"] == 0
    assert post_json(client, "banking:web_inbox", {"line": "nope", "action": "accept"}).status_code == 404
