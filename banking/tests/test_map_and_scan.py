"""Matching the columns of a statement by hand, and reading a statement from a photo (D-212)."""

import io

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from accounts.tests.factories import make_org, make_property
from accounts.tests.test_isolation import login
from ai_assistant.client import AssistantError, FakeClient
from banking import matching, parsing, services
from banking.models import BankTransaction, StatementFormat, StatementImport
from billing.tests.test_invoicing import FEB, bill, make_lease
from meters.models import Scan

pytestmark = pytest.mark.django_db

ODD = (b"Account statement\nOpened 2020\n\n"
       b"Txn Dt,Narration Text,Cr Amt Kes,Dr Amt Kes,Closing\n"
       b"03/02/2026,CASH DEP {ref},15000,,15000\n"
       b"04/02/2026,Bank charges,,50,14950\n")


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def owner():
    return make_org(name="Acme")


@pytest.fixture
def prop(owner):
    return make_property(owner.organization)


@pytest.fixture
def lease(owner, prop):
    lease = make_lease(owner, prop)
    bill(lease, FEB)
    return lease


@pytest.fixture
def account(owner, prop):
    return services.add_bank_account(owner, bank="Equity", number="0123456789", properties=[prop])


def odd(lease) -> SimpleUploadedFile:
    return SimpleUploadedFile("odd.csv", ODD.replace(b"{ref}", lease.unit.payment_reference.encode()))


# ---------------------------------------------------------------------------
# Column maps
# ---------------------------------------------------------------------------


def test_describe_finds_the_header_and_sample_rows():
    found = parsing.describe(ODD.replace(b"{ref}", b"X1"))
    assert found["headers"] == ["Txn Dt", "Narration Text", "Cr Amt Kes", "Dr Amt Kes", "Closing"]
    assert found["samples"][0][0] == "03/02/2026" and len(found["samples"]) == 2


def test_a_saved_mapping_is_tried_before_the_known_names():
    mapping = {"date": "Txn Dt", "description": "Narration Text", "credit": "Cr Amt Kes", "debit": "Dr Amt Kes"}
    parsed = parsing.read_bank(ODD.replace(b"{ref}", b"X1"), mapping)
    assert [(line.amount, line.description) for line in parsed.lines] == [(parsing.parse_amount("15000"),
                                                                           "CASH DEP X1")]
    with pytest.raises(parsing.NotRecognised):
        parsing.read_bank(ODD.replace(b"{ref}", b"X1"))


def test_unknown_columns_wait_for_the_landlord_then_are_remembered(owner, account, lease):
    batch = services.preview(owner, account, odd(lease))
    assert batch.status == StatementImport.Status.MAPPING and bytes(batch.raw).startswith(b"Account statement")
    page = services.mapping_page(owner, batch)
    assert "Txn Dt" in page["headers"]

    with pytest.raises(ValidationError) as e:
        services.map_columns(owner, batch, {"date": "Nope", "credit": "Cr Amt Kes"})
    assert "date" in e.value.message_dict
    with pytest.raises(ValidationError):
        services.map_columns(owner, batch, {"description": "Narration Text"})

    batch = services.map_columns(owner, batch, {"date": "Txn Dt", "credit": "Cr Amt Kes", "debit": "Dr Amt Kes",
                                                 "description": "Narration Text", "balance": "Closing"})
    assert (batch.status, batch.new_count, batch.skipped_count, batch.raw) == ("PREVIEW", 1, 1, None)
    assert StatementFormat.objects.get(payment_account=account).columns["date"] == "Txn Dt"

    services.apply(owner, batch)
    assert BankTransaction.objects.get().status == "MATCHED"

    # Next month's file from the same bank is read without asking.
    again = services.preview(owner, account, odd(lease))
    assert again.status == StatementImport.Status.PREVIEW and again.duplicate_count == 1


def test_a_waiting_statement_expires_and_is_emptied(owner, account, lease):
    batch = services.preview(owner, account, odd(lease))
    StatementImport.objects.filter(pk=batch.pk).update(created_at=batch.created_at - services.PREVIEW_TTL)
    assert services.purge_stale_previews() == 1
    batch.refresh_from_db()
    assert (batch.status, batch.raw) == ("DISCARDED", None)


def test_the_column_pages(client, owner, account, lease):
    login(client, owner)
    response = client.post(reverse("banking:import"), {"account": account.pk, "file": odd(lease)})
    batch = StatementImport.objects.get()
    page = reverse("banking:import_map", args=[batch.public_id])
    assert response.url == page
    assert b"Narration Text" in client.get(page).content
    assert client.get(reverse("banking:import_detail", args=[batch.public_id])).url == page
    assert client.post(page, {"date": "Txn Dt"}).status_code == 400
    response = client.post(page, {"date": "Txn Dt", "credit": "Cr Amt Kes", "description": "Narration Text"})
    assert response.url == reverse("banking:import_detail", args=[batch.public_id])


# ---------------------------------------------------------------------------
# Reconciling a running balance
# ---------------------------------------------------------------------------


def test_unreconciled_lines_are_the_ones_the_balance_disagrees_with():
    from decimal import Decimal as D

    # Newest first: 15,000 in, a 50 fee, then the opening line.
    ok = [(D("15000"), D("25000")), (D("-50"), D("10000")), (D("10050"), D("10050"))]
    assert parsing.unreconciled(ok) == set()
    assert parsing.unreconciled([(D("15500"), D("25000")), *ok[1:]]) == {0}
    assert parsing.unreconciled([(D("5"), None), (D("7"), None)]) == set()


# ---------------------------------------------------------------------------
# Photos and scans
# ---------------------------------------------------------------------------


def picture(name="shot.png") -> SimpleUploadedFile:
    out = io.BytesIO()
    Image.new("RGB", (60, 120), "white").save(out, "PNG")
    return SimpleUploadedFile(name, out.getvalue(), "image/png")


def rows(*lines, legible=True):
    keys = ("date", "description", "reference", "money_in", "money_out", "balance")
    return FakeClient.image({"legible": legible, "rows": [dict(zip(keys, line, strict=True), unclear=False)
                                                         for line in lines]})


def test_a_picture_without_the_tick_is_told_what_to_do(owner, account):
    with pytest.raises(ValidationError, match="photo or scan"):
        services.preview(owner, account, picture())


def test_a_photo_is_read_checked_against_the_balance_and_never_auto_confirmed(owner, account, lease, settings):
    ref = lease.unit.payment_reference
    FakeClient.script = [rows(
        ("2026-02-05", "MISREAD", "FT2", "9999", None, "15650"),
        ("2026-02-04", "Bank charges", None, None, "50", "14950"),
        ("2026-02-03", f"CASH DEP {ref} JANE", "FT1", "15000", None, "15000"))]
    batch = services.preview(owner, account, picture(), scanned=True)
    assert batch.source == "SCAN" and batch.status == "PREVIEW"
    assert [r["amount"] for r in batch.rows] == ["15000.00"]
    assert batch.skipped_count == 1
    assert len(batch.errors) == 1 and "running balance" in batch.errors[0][1]
    call = FakeClient.calls[0]
    assert call["image"][:3] == b"\xff\xd8\xff" and "bank statement" in call["instructions"]
    scan = Scan.objects.get()
    assert (scan.kind, scan.status, scan.found) == ("STATEMENT", "READ", 3)

    services.apply(owner, batch)
    lines = BankTransaction.objects.order_by("amount")
    suggested = lines.get()
    assert suggested.status == "UNMATCHED" and suggested.suggested_lease == lease and suggested.payment is None


def test_a_photo_that_is_not_a_statement_or_fails_is_not_imported(owner, account):
    FakeClient.script = [rows(legible=False)]
    with pytest.raises(ValidationError, match="No statement"):
        services.preview(owner, account, picture(), scanned=True)
    assert Scan.objects.get().status == "UNCLEAR"
    FakeClient.script = [AssistantError("down")]
    with pytest.raises(ValidationError, match="could not be read"):
        services.preview(owner, account, picture(), scanned=True)
    assert not StatementImport.objects.exists()


def test_the_scan_limit_applies(owner, account):
    owner.organization.scan_limit = 0
    owner.organization.save()
    with pytest.raises(ValidationError):
        services.preview(owner, account, picture(), scanned=True)


def test_matching_only_suggests_when_confirm_is_off(owner, account, lease):
    line = BankTransaction.objects.create(
        organization=owner.organization, payment_account=account,
        statement=StatementImport.objects.create(organization=owner.organization, payment_account=account,
                                                 kind="BANK", created_by=owner.user),
        posted_on="2026-02-03", description=f"DEP {lease.unit.payment_reference}", amount="15000", fingerprint="x")
    line = matching.process(line, confirm=False)
    assert (line.status, line.suggested_lease_id) == ("UNMATCHED", lease.pk)
