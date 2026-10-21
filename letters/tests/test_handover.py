"""The advocate handover pack (D-122): what it holds, what it warns about, who may download it, and the page."""

import datetime
import hashlib
import io
import json
import zipfile

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from accounts.capabilities import ROLE_TEMPLATES
from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from audit.models import AuditEvent
from billing import followups
from billing.models import FollowUp
from billing.tests.test_invoicing import make_lease
from documents import services as documents
from documents.models import Document
from leases import services as lease_services
from leases.models import LeaseNotice
from letters import handover, notices
from letters.models import NoticeLetter
from letters.tests.test_notices import PDF, in_arrears
from payments.models import Payment

pytestmark = pytest.mark.django_db

D = datetime.date


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def prop(owner):
    return make_property(owner.organization)


def unzip(content: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as pack:
        return {name: pack.read(name) for name in pack.namelist()}


def served_demand(owner, lease, *, proof=True):
    letter = notices.issue_demand(owner, lease)
    upload = SimpleUploadedFile("s.pdf", PDF, content_type="application/pdf") if proof else None
    return notices.record_service(owner, letter, served_on=timezone.localdate(),
                                  served_via=NoticeLetter.ServedVia.HAND, received_by="Tenant A1",
                                  proof_file=upload)


# ---------------------------------------------------------------------------
# What the pack holds
# ---------------------------------------------------------------------------


def test_an_arrears_pack_holds_the_records_with_their_fingerprints(owner, prop):
    lease = in_arrears(owner, prop)
    letter = served_demand(owner, lease)
    documents.upload(owner, lease, file=SimpleUploadedFile("lease.pdf", PDF, content_type="application/pdf"),
                     category=Document.Category.LEASE, title="Signed lease")
    followups.record_follow_up(owner, lease, outcome=FollowUp.Outcome.NO_ANSWER, note="Called twice")

    filename, content = handover.build(owner, lease, advocate="Wanjiru & Co. Advocates", note="Keys not returned")
    assert filename.startswith(lease.number) and filename.endswith(".zip")
    files = unzip(content)
    assert {"00-brief.pdf", "01-statement.pdf", "01-statement.csv", "02-payments.csv", "03-follow-ups.csv",
            f"04-letters/{letter.number}.pdf", f"04-letters/{letter.number}-as-served.pdf", "record.json",
            "certificate.pdf"} <= set(files)
    assert any(name.startswith("07-documents/") and "Signed-lease" in name for name in files)
    # The signed copy of the letter is filed once, beside the letter, not again under documents.
    assert sum(1 for name in files if name.startswith("07-documents/")) == 1
    for name in ("00-brief.pdf", "01-statement.pdf", "certificate.pdf"):
        assert files[name].startswith(b"%PDF")

    record = json.loads(files["record.json"])
    for name, sha in record["files"].items():
        assert hashlib.sha256(files[name]).hexdigest() == sha
    assert record["owed_past_grace"] == "45000.00" and record["advocate"] == "Wanjiru & Co. Advocates"
    assert record["letters"][0]["number"] == letter.number and record["letters"][0]["served_via"] == "HAND"
    assert {e["action"] for e in record["letters_audit"]} >= {"notice_letter.issue", "notice_letter.serve"}

    statement = files["01-statement.csv"].decode("utf-8-sig").splitlines()
    assert statement[0].startswith("Date,Lease,Details") and statement[-1].endswith(",45000.00")
    assert "Called twice" in files["03-follow-ups.csv"].decode("utf-8-sig")

    event = AuditEvent.objects.get(action="handover.pack")
    assert event.changes["sha256"][1] == hashlib.sha256(content).hexdigest()
    assert event.changes["owed"][1] == "45000.00" and event.changes["purpose"][1] == "ARREARS"


def test_csv_text_cannot_run_as_a_formula(owner, prop):
    lease = in_arrears(owner, prop)
    followups.record_follow_up(owner, lease, outcome=FollowUp.Outcome.OTHER, note="=HYPERLINK(\"x\")")
    files = unzip(handover.build(owner, lease)[1])
    assert "'=HYPERLINK" in files["03-follow-ups.csv"].decode("utf-8-sig")


def test_a_move_out_pack_has_the_move_out_statement(owner, prop):
    lease = make_lease(owner, prop)
    lease_services.give_notice(owner, lease, given_on=timezone.localdate(), given_by=LeaseNotice.GivenBy.TENANT)
    lease.refresh_from_db()
    data = handover.summary(owner, lease, purpose=handover.Purpose.MOVE_OUT)
    text = " ".join(data["concerns"])
    assert "no completed move-out condition report" in text and "move-in condition report" in text
    files = unzip(handover.build(owner, lease, purpose=handover.Purpose.MOVE_OUT)[1])
    assert files["05-move-out-statement.pdf"].startswith(b"%PDF")


def test_a_draft_lease_has_nothing_to_hand_over(owner, prop):
    from billing.tests.test_invoicing import JAN
    from properties import services as property_services
    from tenants import services as tenant_services

    unit = property_services.create_unit(owner, prop, code="B1")
    tenant = tenant_services.create_tenant(owner, name="Draft", phone="0712345699")
    draft = lease_services.create_lease(owner, unit=unit, tenants=[tenant], start_date=JAN, end_date=None,
                                        rent=10000)
    assert handover.problem(draft)
    with pytest.raises(ValidationError):
        handover.build(owner, draft)
    with pytest.raises(ValidationError):
        handover.build(owner, in_arrears(owner, prop), purpose="REVENGE")


# ---------------------------------------------------------------------------
# What to check
# ---------------------------------------------------------------------------


def test_concerns_before_handing_over(owner, prop):
    lease = in_arrears(owner, prop)
    text = " ".join(handover.summary(owner, lease)["concerns"])
    assert "No demand letter has been recorded as served" in text
    assert "No signed tenancy agreement" in text and "no ID or registration number" in text

    served_demand(owner, lease, proof=False)
    followups.record_follow_up(owner, lease, outcome=FollowUp.Outcome.DISPUTED, note="Says Feb was paid")
    Payment.objects.create(organization=owner.organization, lease=lease, amount=5000,
                           method=Payment.Method.MPESA, paid_at=timezone.localdate(), reference="QWE123RTY")
    text = " ".join(handover.summary(owner, lease)["concerns"])
    assert "No demand letter has been recorded as served" not in text
    assert "its days run until" in text and "disputes the amount" in text and "waiting for review" in text


def test_nothing_owed_is_said(owner, prop):
    lease = make_lease(owner, prop)
    assert "Nothing past its grace days is owed" in " ".join(handover.summary(owner, lease)["concerns"])


# ---------------------------------------------------------------------------
# Who may
# ---------------------------------------------------------------------------


def test_only_those_who_issue_notices_may_download_it(owner, prop):
    lease = in_arrears(owner, prop)
    with pytest.raises(PermissionDenied):
        handover.build(add_member(owner.organization, "accountant", all_properties=True), lease)
    elsewhere = add_member(owner.organization, "manager", properties=[make_property(owner.organization)])
    with pytest.raises(PermissionDenied):
        handover.build(elsewhere, lease)
    with pytest.raises(PermissionDenied):
        handover.build(make_org(), lease)
    assert handover.build(add_member(owner.organization, "manager", properties=[prop]), lease)


def test_ids_are_left_out_for_those_who_cannot_see_them(owner, prop):
    lease = in_arrears(owner, prop)
    tenant = lease.primary_tenant
    tenant.id_type, tenant.id_number = "NATIONAL_ID", "12345678"
    tenant.save()
    assert handover.summary(owner, lease)["tenants"][0]["id"].endswith("12345678")
    role = next(t.key for t in ROLE_TEMPLATES
                if "leases.view" in t.capabilities and "tenants.view_sensitive" not in t.capabilities)
    member = add_member(owner.organization, role, all_properties=True)
    data = handover.summary(member, lease)
    assert data["tenants"][0]["id"] is None and data["tenants"][0]["kra_pin"] is None
    assert "left out because you cannot see them" in " ".join(data["concerns"])


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


def test_the_page(client, owner, prop):
    lease = in_arrears(owner, prop)
    login(client, owner)
    assert b"Advocate pack" in client.get(reverse("leases:detail", args=[lease.public_id])).content
    assert b"Download the handover pack" in client.get(reverse("letters:notices", args=[lease.public_id])).content
    url = reverse("letters:handover", args=[lease.public_id])
    page = client.get(url)
    assert page.status_code == 200 and b"45,000" in page.content and b"What the pack holds" in page.content
    assert b"Disputed move-out" in client.get(url + "?purpose=MOVE_OUT").content
    assert client.post(url, {"purpose": "NONSENSE"}).status_code == 400
    response = client.post(url, {"purpose": "ARREARS", "advocate": "A & Co."})
    assert response.status_code == 200 and response["Content-Type"] == "application/zip"
    assert response["Cache-Control"] == "private, no-store"
    assert "00-brief.pdf" in unzip(response.content)


def test_the_page_outside_scope_is_404_and_without_capability_403(client, owner, prop):
    lease = in_arrears(owner, prop)
    url = reverse("letters:handover", args=[lease.public_id])
    login(client, make_org())
    assert client.get(url).status_code == 404
    login(client, add_member(owner.organization, "manager", properties=[make_property(owner.organization)]))
    assert client.get(url).status_code == 404
    login(client, add_member(owner.organization, "accountant", all_properties=True))
    assert client.get(url).status_code == 403
    assert client.post(url, {"purpose": "ARREARS"}).status_code == 403
