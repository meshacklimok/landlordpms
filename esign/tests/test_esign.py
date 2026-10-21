"""E-signatures (D-118): sending, the tenant's code and signature, the landlord's, completion, and the pages."""

import datetime
import hashlib
import io
import re

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone
from pypdf import PdfReader

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from audit.models import AuditEvent
from billing.tests.test_invoicing import make_lease
from documents.models import Document
from esign import services
from esign.models import Signer, SigningRequest
from leases import services as lease_services
from notifications import catalog
from notifications import services as notification_services
from notifications.models import Message

pytestmark = pytest.mark.django_db

Kind = SigningRequest.Kind
Status = SigningRequest.Status


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def prop(owner):
    return make_property(owner.organization)


@pytest.fixture
def lease(owner, prop):
    return make_lease(owner, prop)


def token_from(outbox) -> str:
    return re.search(r"/sign/([^/\s]+)/", outbox[-1][1]).group(1)


def code_from(outbox) -> str:
    return re.search(r"is (\d{6})\.", outbox[-1][1]).group(1)


def send(owner, lease, kind=Kind.AGREEMENT, **kw):
    if kind == Kind.AGREEMENT:
        kw.setdefault("no_renewal_option", True)
    req = services.create(owner, lease, kind, **kw)
    services.send_links(owner, req)
    return req


def tenant_signs(client, outbox, token, name="Tenant A1"):
    client.post(f"/sign/{token}/", {"action": "send_code"})
    client.post(f"/sign/{token}/", {"action": "check_code", "code": code_from(outbox)})
    return client.post(f"/sign/{token}/", {"action": "sign", "typed_name": name, "agree": "on"})


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def test_sending_freezes_the_pdf_and_texts_each_tenant_a_link(owner, lease, outbox):
    req = send(owner, lease)
    content = req.original_pdf.open("rb").read()
    assert hashlib.sha256(content).hexdigest() == req.original_sha256
    assert req.number.startswith("SIG-")
    assert [s.role for s in req.signers.all()] == [Signer.Role.LANDLORD, Signer.Role.TENANT]
    to, body = outbox[-1]
    assert to == lease.primary_tenant.phone and "/sign/" in body
    tenant = req.signers.get(role=Signer.Role.TENANT)
    assert tenant.token_hash == hashlib.sha256(token_from(outbox).encode()).hexdigest()
    assert AuditEvent.objects.filter(action="esign.create", object_id=str(req.public_id)).exists()


def test_the_pdf_sent_says_it_is_signed_electronically_not_on_lines(owner, lease):
    req = services.create(owner, lease, Kind.AGREEMENT, no_renewal_option=True)
    text = "".join(p.extract_text() for p in PdfReader(req.original_pdf.open("rb")).pages)
    assert "Signed electronically" in text and "Witness" not in text


def test_one_waiting_request_per_document(owner, lease):
    services.create(owner, lease, Kind.AGREEMENT, no_renewal_option=True)
    with pytest.raises(ValidationError, match="already waiting"):
        services.create(owner, lease, Kind.AGREEMENT, no_renewal_option=True)


def test_an_expired_request_does_not_block_a_new_one(owner, lease):
    old = services.create(owner, lease, Kind.AGREEMENT, no_renewal_option=True)
    SigningRequest.objects.filter(pk=old.pk).update(expires_at=timezone.now() - datetime.timedelta(seconds=1))
    services.create(owner, lease, Kind.AGREEMENT, no_renewal_option=True)
    old.refresh_from_db()
    assert old.status == Status.EXPIRED


def test_the_move_out_statement_needs_notice_or_an_ended_lease(owner, lease):
    with pytest.raises(ValidationError, match="move-out statement applies"):
        services.create(owner, lease, Kind.MOVE_OUT)
    lease_services.give_notice(owner, lease, given_on=timezone.localdate())
    req = services.create(owner, lease, Kind.MOVE_OUT)
    assert req.title.startswith("Move-out statement")


def test_a_caretaker_cannot_send(owner, prop, lease):
    caretaker = add_member(owner.organization, "caretaker", properties=[prop])
    with pytest.raises(PermissionDenied):
        services.create(caretaker, lease, Kind.AGREEMENT)


def test_another_organization_cannot_send(lease):
    other = make_org("Other Homes")
    with pytest.raises(PermissionDenied):
        services.create(other, lease, Kind.AGREEMENT)


def test_with_sms_stopped_the_link_is_given_to_staff_instead(owner, lease, outbox):
    notification_services.set_tenant_channel(owner, lease.primary_tenant, catalog.SMS, allowed=False)
    req = services.create(owner, lease, Kind.AGREEMENT, no_renewal_option=True)
    before = len(outbox)
    unsent = services.send_links(owner, req)
    assert len(outbox) == before
    assert len(unsent) == 1 and "/sign/" in unsent[0][1]


def test_a_new_link_stops_the_old_one(client, owner, lease, outbox):
    req = send(owner, lease)
    old = token_from(outbox)
    services.new_link(owner, req.signers.get(role=Signer.Role.TENANT))
    assert client.get(f"/sign/{old}/").status_code == 404
    assert client.get(f"/sign/{token_from(outbox)}/").status_code == 200


# ---------------------------------------------------------------------------
# The tenant's code
# ---------------------------------------------------------------------------


def test_nothing_is_shown_before_the_code(client, owner, lease, outbox):
    send(owner, lease)
    token = token_from(outbox)
    response = client.get(f"/sign/{token}/")
    assert b"Send me a code" in response.content and b"Type your full name" not in response.content
    assert client.get(f"/sign/{token}/pdf/").status_code == 404
    response = client.post(f"/sign/{token}/", {"action": "sign", "typed_name": "X Y", "agree": "on"})
    assert not Signer.objects.filter(signed_at__isnull=False).exists()


def test_a_wrong_code_fails_and_tries_run_out(client, owner, lease, outbox):
    send(owner, lease)
    token = token_from(outbox)
    client.post(f"/sign/{token}/", {"action": "send_code"})
    good = code_from(outbox)
    bad = f"{(int(good) + 1) % 1000000:06d}"
    for _ in range(services.MAX_ATTEMPTS):
        client.post(f"/sign/{token}/", {"action": "check_code", "code": bad})
    client.post(f"/sign/{token}/", {"action": "check_code", "code": good})
    assert client.get(f"/sign/{token}/pdf/").status_code == 404


def test_codes_wait_a_minute_between_sends(owner, lease, outbox):
    req = send(owner, lease)
    signer = req.signers.get(role=Signer.Role.TENANT)
    services.send_code(signer)
    with pytest.raises(services.SigningError, match="wait a minute"):
        services.send_code(signer)


def test_the_code_goes_even_when_sms_is_stopped(owner, lease, outbox):
    notification_services.set_tenant_channel(owner, lease.primary_tenant, catalog.SMS, allowed=False)
    req = services.create(owner, lease, Kind.AGREEMENT, no_renewal_option=True)
    services.send_code(req.signers.get(role=Signer.Role.TENANT))
    assert "code to sign" in outbox[-1][1]


def test_an_expired_code_does_not_work(owner, lease, outbox):
    req = send(owner, lease)
    signer = req.signers.get(role=Signer.Role.TENANT)
    services.send_code(signer)
    Signer.objects.filter(pk=signer.pk).update(code_expires_at=timezone.now())
    assert not services.check_code(signer, code_from(outbox))


# ---------------------------------------------------------------------------
# Signing and completion
# ---------------------------------------------------------------------------


def test_the_tenant_signs_and_the_details_are_kept(client, owner, lease, outbox):
    req = send(owner, lease)
    token = token_from(outbox)
    assert client.get(f"/sign/{token}/pdf/").status_code == 404
    tenant_signs(client, outbox, token, name="  Tenant   A1 ")
    signer = req.signers.get(role=Signer.Role.TENANT)
    assert signer.signed_name == "Tenant A1" and signer.signed_ip and signer.verified_at
    req.refresh_from_db()
    assert req.status == Status.PENDING
    assert AuditEvent.objects.filter(action="esign.sign").count() == 1


def test_signing_needs_the_tick_and_a_name(client, owner, lease, outbox):
    send(owner, lease)
    token = token_from(outbox)
    client.post(f"/sign/{token}/", {"action": "send_code"})
    client.post(f"/sign/{token}/", {"action": "check_code", "code": code_from(outbox)})
    client.post(f"/sign/{token}/", {"action": "sign", "typed_name": "Tenant A1"})
    client.post(f"/sign/{token}/", {"action": "sign", "typed_name": "", "agree": "on"})
    assert not Signer.objects.filter(signed_at__isnull=False).exists()


def test_when_everyone_has_signed_the_copy_is_filed_and_checkable(client, owner, lease, outbox):
    req = send(owner, lease, share=True)
    token = token_from(outbox)
    tenant_signs(client, outbox, token)
    req = services.sign_as_landlord(owner, req, typed_name="Owner Person", agreed=True)
    assert req.status == Status.COMPLETED
    signed = req.signed_pdf.open("rb").read()
    assert hashlib.sha256(signed).hexdigest() == req.signed_sha256
    original_pages = len(PdfReader(req.original_pdf.open("rb")).pages)
    reader = PdfReader(io.BytesIO(signed))
    assert len(reader.pages) == original_pages + 1
    record = reader.pages[-1].extract_text()
    assert "SIGNING RECORD" in record and req.original_sha256 in record.replace("\n", "")
    doc = req.document
    assert doc.lease == lease and doc.shared_with_tenant and doc.category == Document.Category.LEASE
    assert Message.objects.filter(type="esign_completed", user=owner.user).exists()
    # The tenant's link still opens, now offering the signed copy.
    assert client.get(f"/sign/{token}/pdf/").status_code == 200
    page = client.get(f"/s/{req.verify_code}/")
    assert page.status_code == 200 and req.signed_sha256.encode() in page.content
    assert page["X-Robots-Tag"] == "noindex, nofollow"


def test_the_signed_copy_is_offered_to_the_tenant_after_a_code(client, owner, lease, outbox):
    req = send(owner, lease)
    token = token_from(outbox)
    tenant_signs(client, outbox, token)
    services.sign_as_landlord(owner, req, typed_name="Owner Person", agreed=True)
    client.cookies.clear()
    assert client.get(f"/sign/{token}/pdf/").status_code == 404
    Signer.objects.update(code_sent_at=timezone.now() - datetime.timedelta(seconds=61))
    client.post(f"/sign/{token}/", {"action": "send_code"})
    client.post(f"/sign/{token}/", {"action": "check_code", "code": code_from(outbox)})
    response = client.get(f"/sign/{token}/pdf/")
    assert response.status_code == 200 and response["Content-Type"] == "application/pdf"


def test_declining_closes_the_request_and_tells_the_sender(client, owner, lease, outbox):
    req = send(owner, lease)
    token = token_from(outbox)
    client.post(f"/sign/{token}/", {"action": "send_code"})
    client.post(f"/sign/{token}/", {"action": "check_code", "code": code_from(outbox)})
    client.post(f"/sign/{token}/", {"action": "decline", "reason": "The rent is wrong"})
    req.refresh_from_db()
    assert req.status == Status.DECLINED and req.close_reason == "The rent is wrong"
    assert Message.objects.filter(type="esign_declined", user=owner.user).exists()
    with pytest.raises(ValidationError):
        services.sign_as_landlord(owner, req, typed_name="Owner Person", agreed=True)


def test_cancelling_stops_the_links(client, owner, lease, outbox):
    req = send(owner, lease)
    token = token_from(outbox)
    services.cancel(owner, req, "Wrong rent")
    response = client.get(f"/sign/{token}/")
    assert response.status_code == 404


def test_cancelling_needs_a_reason(owner, lease):
    req = services.create(owner, lease, Kind.AGREEMENT, no_renewal_option=True)
    with pytest.raises(ValidationError):
        services.cancel(owner, req, " ")


def test_a_changed_stored_pdf_is_not_signed(client, owner, lease, outbox):
    req = send(owner, lease)
    with open(req.original_pdf.path, "ab") as f:
        f.write(b"%changed")
    tenant_signs(client, outbox, token_from(outbox))
    with pytest.raises(RuntimeError, match="fingerprint"):
        services.sign_as_landlord(owner, req, typed_name="Owner Person", agreed=True)


# ---------------------------------------------------------------------------
# Staff pages
# ---------------------------------------------------------------------------


def test_the_lease_page_offers_e_signature(client, owner, lease):
    login(client, owner)
    response = client.get(f"/leases/{lease.public_id}/")
    assert b"for e-signature" in response.content


def test_sending_from_the_lease_page(client, owner, lease, outbox):
    login(client, owner)
    response = client.post(f"/esign/lease/{lease.public_id}/send/", {"kind": "AGREEMENT", "no_renewal_option": "on"})
    req = SigningRequest.objects.get()
    assert response.status_code == 302 and response["Location"] == f"/esign/{req.public_id}/"
    page = client.get(f"/esign/{req.public_id}/")
    assert page.status_code == 200 and b"Sign for the landlord" in page.content
    assert client.get(f"/esign/{req.public_id}/pdf/").status_code == 200


def test_the_landlord_signs_on_the_request_page(client, owner, lease):
    req = services.create(owner, lease, Kind.AGREEMENT, no_renewal_option=True)
    login(client, owner)
    client.post(f"/esign/{req.public_id}/", {"action": "sign", "typed_name": "Owner Person", "agree": "on"})
    landlord = req.signers.get(role=Signer.Role.LANDLORD)
    assert landlord.signed_by == owner.user and landlord.signed_name == "Owner Person"


def test_another_organization_gets_404(client, owner, lease):
    req = services.create(owner, lease, Kind.AGREEMENT, no_renewal_option=True)
    other = make_org("Other Homes")
    login(client, other)
    assert client.get(f"/esign/{req.public_id}/").status_code == 404
    assert client.get(f"/esign/{req.public_id}/pdf/").status_code == 404
    client.post(f"/esign/{req.public_id}/", {"action": "cancel", "reason": "x"})
    req.refresh_from_db()
    assert req.status == Status.PENDING
    assert client.post(f"/esign/lease/{lease.public_id}/send/", {"kind": "AGREEMENT"}).status_code == 404


def test_staff_kept_to_another_property_get_404(client, owner, lease):
    req = services.create(owner, lease, Kind.AGREEMENT, no_renewal_option=True)
    elsewhere = add_member(owner.organization, "manager", properties=[make_property(owner.organization, "Far")])
    login(client, elsewhere)
    assert client.get(f"/esign/{req.public_id}/").status_code == 404


def test_an_unknown_check_code_is_404(client):
    assert client.get("/s/nothing-here/").status_code == 404


# ---------------------------------------------------------------------------
# What the law allows (D-118 item 10)
# ---------------------------------------------------------------------------


def test_a_lease_over_two_years_is_signed_on_paper(owner, lease):
    Lease = type(lease)
    Lease.all_objects.filter(pk=lease.pk).update(end_date=lease.start_date.replace(year=lease.start_date.year + 2)
                                                  + datetime.timedelta(days=1))
    lease.refresh_from_db()
    assert services.is_long_lease(lease)
    with pytest.raises(ValidationError, match="more than two years"):
        services.create(owner, lease, Kind.AGREEMENT, no_renewal_option=True)
    Lease.all_objects.filter(pk=lease.pk).update(end_date=lease.start_date.replace(year=lease.start_date.year + 2))
    lease.refresh_from_db()
    assert not services.is_long_lease(lease)


def test_the_agreement_needs_the_no_renewal_confirmation(client, owner, lease):
    with pytest.raises(ValidationError, match="no option to renew"):
        services.create(owner, lease, Kind.AGREEMENT)
    login(client, owner)
    client.post(f"/esign/lease/{lease.public_id}/send/", {"kind": "AGREEMENT"})
    assert not SigningRequest.objects.exists()


def test_the_move_out_statement_needs_no_term_confirmation(owner, lease):
    lease_services.give_notice(owner, lease, given_on=timezone.localdate())
    req = services.create(owner, lease, Kind.MOVE_OUT, no_renewal_option=True)
    assert not req.no_renewal_option


def test_the_evidence_pack_holds_files_trail_and_certificate(client, owner, lease, outbox):
    import json
    import zipfile

    req = send(owner, lease)
    tenant_signs(client, outbox, token_from(outbox))
    req = services.sign_as_landlord(owner, req, typed_name="Owner Person", agreed=True)
    login(client, owner)
    response = client.get(f"/esign/{req.public_id}/evidence/")
    assert response.status_code == 200 and response["Content-Type"] == "application/zip"
    pack = zipfile.ZipFile(io.BytesIO(response.content))
    names = set(pack.namelist())
    assert {f"{req.number}.pdf", f"{req.number}-signed.pdf", "record.json", "certificate.pdf"} <= names
    record = json.loads(pack.read("record.json"))
    assert record["files"][f"{req.number}-signed.pdf"] == req.signed_sha256
    assert [e["action"] for e in record["audit"]][:1] == ["esign.create"]
    assert {s["signed_name"] for s in record["signers"]} == {"Tenant A1", "Owner Person"}
    text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(pack.read("certificate.pdf"))).pages)
    assert "106B" in text and req.original_sha256 in text.replace("\n", "")
    assert AuditEvent.objects.filter(action="esign.evidence", object_id=str(req.public_id)).exists()


def test_the_evidence_pack_is_for_members_who_send(client, owner, prop, lease):
    req = services.create(owner, lease, Kind.AGREEMENT, no_renewal_option=True)
    caretaker = add_member(owner.organization, "caretaker", properties=[prop])
    login(client, caretaker)
    assert client.get(f"/esign/{req.public_id}/evidence/").status_code in (403, 404)


def test_signed_copies_are_kept_six_years_after_the_lease_ends(owner, lease):
    req = services.create(owner, lease, Kind.AGREEMENT, no_renewal_option=True)
    assert services.keep_until(req) is None
    type(lease).all_objects.filter(pk=lease.pk).update(end_date=datetime.date(2027, 12, 31))
    req.lease.refresh_from_db()
    assert services.keep_until(req) == datetime.date(2033, 12, 31)
