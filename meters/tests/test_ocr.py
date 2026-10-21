"""Reading photos (D-144): a notebook page fills the sheet, a utility bill fills the amount to divide. Nothing is
saved from a scan; each one is logged with its cost, and an organization has a monthly limit."""

import datetime
import json
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from accounts.tests.factories import add_member, make_org
from accounts.tests.test_isolation import login
from ai_assistant import client as ai_client
from ai_assistant.client import AssistantError, FakeClient
from meters import ocr, services
from meters.models import MeterReading, Scan
from properties import services as property_services

from .conftest import jpeg, let, make_meter, read

pytestmark = pytest.mark.django_db
D = datetime.date
DEC31, JAN31 = D(2025, 12, 31), D(2026, 1, 31)


@pytest.fixture
def block(owner, prop):
    """A1 and A2 on their own water meters, and B1 on a meter nobody lives at."""
    meters = {}
    for code in ("A1", "A2"):
        _lease, unit = let(owner, prop, code=code)
        meters[code] = make_meter(owner, prop, [unit], label=f"{code} water")
    b1 = property_services.create_unit(owner, prop, code="B1")
    meters["B1"] = make_meter(owner, prop, [b1], label="B1 water")
    return meters


def notebook(*lines, legible=True, tokens=(1500, 90)):
    return FakeClient.image({"legible": legible, "lines": [{"room": r, "number": n, "unclear": n is None}
                                                          for r, n in lines]}, tokens=tokens)


def import_url(prop):
    return reverse("meters:round_import", args=[prop.public_id])


def test_a_notebook_photo_fills_the_rows_it_matches(client, owner, prop, block, settings):
    settings.AI_PRICE_INPUT_PER_M, settings.AI_PRICE_OUTPUT_PER_M, settings.USD_TO_KES = "2", "12", "130"
    FakeClient.script = [notebook(("A1", "134.5"), ("a 2", "98"), ("C9", "5"), ("B1", None))]
    login(client, owner)
    response = client.post(import_url(prop), {"photo": jpeg(), "mode": "reading"})
    body = response.json()
    assert response.status_code == 200
    assert {(m["room"], m["value"]) for m in body["matches"]} == {("A1", "134.5"), ("a 2", "98")}
    assert [s["line"] for s in body["skipped"]] == ["B1 ?", "C9 5"]
    assert "could not be read" in body["skipped"][0]["reason"]
    # The photo went as a small JPEG with the notebook instructions and schema; nothing was recorded.
    call = FakeClient.calls[0]
    assert call["image"][:3] == b"\xff\xd8\xff" and "notebook" in call["instructions"]
    assert call["schema"]["required"] == ["legible", "lines"]
    assert not MeterReading.objects.filter(read_on__gt=DEC31).exists()
    scan = Scan.objects.get()
    assert (scan.kind, scan.status, scan.found, scan.user, scan.property) == ("NOTEBOOK", "READ", 2, owner.user, prop)
    # 1,500 in at $2/M + 90 out at $12/M = $0.00408 → KES 0.5304.
    assert scan.cost == Decimal("0.5304")
    assert body["left"] == 99


def test_units_used_and_one_utility_carry_through(client, owner, prop, block):
    FakeClient.script = [notebook(("A1", "12"))]
    login(client, owner)
    body = client.post(import_url(prop), {"photo": jpeg(), "mode": "units", "utility": "WATER"}).json()
    assert body["matches"][0]["mode"] == "units"


def test_a_photo_with_nothing_to_read_counts_but_fills_nothing(client, owner, prop, block):
    FakeClient.script = [notebook(legible=False)]
    login(client, owner)
    response = client.post(import_url(prop), {"photo": jpeg(), "mode": "reading"})
    assert response.status_code == 400 and "No readings could be read" in response.json()["error"]
    assert Scan.objects.get().status == "UNCLEAR" and ocr.used(owner.organization) == 1


def test_a_failed_call_is_free(client, owner, prop, block):
    FakeClient.script = [AssistantError("timeout")]
    login(client, owner)
    response = client.post(import_url(prop), {"photo": jpeg(), "mode": "reading"})
    assert response.status_code == 400 and "could not be read just now" in response.json()["error"]
    assert Scan.objects.get().status == "FAILED" and ocr.used(owner.organization) == 0


def test_a_file_that_is_not_a_photo_never_reaches_the_model(client, owner, prop, block):
    login(client, owner)
    fake = SimpleUploadedFile("x.jpg", b"%PDF-1.4 not a photo", content_type="image/jpeg")
    response = client.post(import_url(prop), {"photo": fake, "mode": "reading"})
    assert response.status_code == 400 and "not a photo" in response.json()["error"]
    assert FakeClient.calls == [] and not Scan.objects.exists()


def test_the_monthly_limit_stops_scans(client, owner, prop, block, settings):
    settings.SCAN_MONTHLY_LIMIT = 1
    FakeClient.script = [notebook(("A1", "1"))]
    login(client, owner)
    assert client.post(import_url(prop), {"photo": jpeg(), "mode": "reading"}).status_code == 200
    response = client.post(import_url(prop), {"photo": jpeg(), "mode": "reading"})
    assert response.status_code == 400 and "photo scans are used" in response.json()["error"]
    assert response.json()["left"] == 0 and len(FakeClient.calls) == 1
    assert list(Scan.objects.values_list("status", flat=True)) == ["LIMITED", "READ"]


def test_an_organization_can_have_its_own_limit_or_none(owner, settings):
    org = owner.organization
    settings.SCAN_MONTHLY_LIMIT = 100
    assert ocr.limit(org) == 100 and ocr.available(org)
    org.scan_limit = 0
    assert not ocr.available(org)
    org.scan_limit = 5
    assert ocr.limit(org) == 5


def test_no_key_means_no_scans(client, owner, prop, block, settings):
    settings.OPENAI_API_KEY = ""
    assert not ocr.available(owner.organization)
    login(client, owner)
    page = client.get(reverse("meters:round", args=[prop.public_id])).content.decode()
    assert "data-import-photo" not in page
    response = client.post(import_url(prop), {"photo": jpeg(), "mode": "reading"})
    assert response.status_code == 400 and "not available" in response.json()["error"]


def test_the_sheet_offers_the_photo_and_says_how_many_are_left(client, owner, prop, block):
    login(client, owner)
    page = client.get(reverse("meters:round", args=[prop.public_id])).content.decode()
    assert "data-import-photo" in page and "read by OpenAI" in page and "100 photo scans left" in page


def test_scans_need_the_property(client, owner, prop, block):
    login(client, make_org())
    assert client.post(import_url(prop), {"photo": jpeg(), "mode": "reading"}).status_code == 404
    assert FakeClient.calls == []


def test_cost_is_worked_out_from_the_prices(settings):
    settings.AI_PRICE_INPUT_PER_M, settings.AI_PRICE_OUTPUT_PER_M, settings.USD_TO_KES = "2.00", "12.00", "129"
    assert ai_client.cost_kes(1_000_000, 0) == Decimal("258.0000")
    assert ai_client.cost_kes(0, 0) == 0


# ---------------------------------------------------------------------------
# The utility bill
# ---------------------------------------------------------------------------


@pytest.fixture
def tank(owner, prop):
    _l1, a1 = let(owner, prop, code="A1")
    _l2, a2 = let(owner, prop, code="A2")
    meter = make_meter(owner, prop, [a1, a2], label="Tank")
    services.approve(owner, read(owner, meter, DEC31, 0), today=DEC31)
    return read(owner, meter, JAN31, 30)


def bill_reply(amount="4,520.00", legible=True):
    return FakeClient.image({"legible": legible, "amount": amount, "period_from": "2026-01-01",
                             "period_to": "2026-01-31", "units": "30"})


def test_a_bill_photo_fills_the_amount_to_divide(client, owner, tank):
    FakeClient.script = [bill_reply()]
    login(client, owner)
    response = client.post(reverse("meters:scan_bill", args=[tank.public_id]),
                           {"photo": jpeg(), "vacant": "LANDLORD"})
    assert response.status_code == 302 and "bill=4520.00" in response.url and "vacant=LANDLORD" in response.url
    page = client.get(response.url).content.decode()
    assert "Read from the bill: KES 4,520.00 for 01 Jan 2026 to 31 Jan 2026, 30 units" in page
    assert "KES 2,260.00" in page                                   # 4,520 over two let rooms
    assert MeterReading.objects.get(pk=tank.pk).status == "SUBMITTED"
    scan = Scan.objects.get()
    assert (scan.kind, scan.status, scan.found) == ("BILL", "READ", 1)
    assert "utility bill" in FakeClient.calls[0]["instructions"]


def test_a_bill_that_cannot_be_read_says_so(client, owner, tank):
    FakeClient.script = [bill_reply(amount=None)]
    login(client, owner)
    response = client.post(reverse("meters:scan_bill", args=[tank.public_id]), {"photo": jpeg()})
    assert "bill=" not in response.url
    assert "could not be read on this bill" in client.get(response.url).content.decode()
    assert Scan.objects.get().status == "UNCLEAR"


def test_only_approvers_scan_bills(client, owner, prop, tank):
    caretaker = add_member(owner.organization, "caretaker", properties=[prop])
    login(client, caretaker)
    page = client.get(reverse("meters:shares", args=[tank.public_id])).content.decode()
    assert "Read the bill" not in page
    assert client.post(reverse("meters:scan_bill", args=[tank.public_id]), {"photo": jpeg()}).status_code == 404
    with pytest.raises(PermissionDenied):
        ocr.scan_bill(caretaker, tank, jpeg())
    assert FakeClient.calls == []


def test_the_shares_page_offers_the_bill_photo(client, owner, tank):
    login(client, owner)
    page = client.get(reverse("meters:shares", args=[tank.public_id])).content.decode()
    assert "Read the bill" in page and 'form="scan-bill"' in page and "100 photo scans left" in page


def test_a_bill_scan_cannot_touch_an_approved_reading(owner, tank):
    services.approve(owner, tank, today=JAN31)
    tank.refresh_from_db()
    with pytest.raises(PermissionDenied):
        ocr.scan_bill(owner, tank, jpeg())
    assert FakeClient.calls == []


def test_the_client_sends_the_photo_and_reads_json(settings, monkeypatch):
    settings.OPENAI_API_KEY, settings.SCAN_MODEL = "sk-test", "gpt-5.6-terra"
    sent = []

    def fake_post(url, body, headers, timeout=ai_client.TIMEOUT):
        sent.append((body, timeout))
        return {"choices": [{"message": {"content": json.dumps({"legible": True, "lines": []})}}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 20}}

    monkeypatch.setattr(ai_client, "_post", fake_post)
    out = ai_client.OpenAIClient().read_image(instructions="Read.", image=b"\xff\xd8\xffdata",
                                           schema=ocr.NOTEBOOK_SCHEMA, max_tokens=500)
    assert out == {"data": {"legible": True, "lines": []}, "usage": {"input_tokens": 900, "output_tokens": 20}}
    body, timeout = sent[0]
    assert timeout == ai_client.SCAN_TIMEOUT and body["model"] == "gpt-5.6-terra"
    assert body["response_format"]["json_schema"]["strict"] is True
    image = body["messages"][1]["content"][1]["image_url"]["url"]
    assert image.startswith("data:image/jpeg;base64,/9j/")


def test_the_client_refuses_an_answer_that_is_not_json(settings, monkeypatch):
    monkeypatch.setattr(ai_client, "_post", lambda *a, **k: {"choices": [{"message": {"content": "sorry"}}]})
    with pytest.raises(AssistantError):
        ai_client.OpenAIClient().read_image(instructions="", image=b"x", schema={}, max_tokens=10)


def test_scan_bill_rejects_bad_input_without_a_call(owner, tank):
    with pytest.raises(ValidationError):
        ocr.scan_bill(owner, tank, SimpleUploadedFile("a.png", b"nope", content_type="image/png"))
    assert FakeClient.calls == []
