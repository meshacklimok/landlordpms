"""Documents (D-071): files, places, scope, changes and the audit trail."""

import datetime
import io
import zipfile

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from accounts.tests.factories import add_member, make_org, make_property
from audit.models import AuditEvent
from documents import services
from documents.models import Document
from reports.tests.test_income import make_lease

from .conftest import jpeg, office, pdf, upload

pytestmark = pytest.mark.django_db


def actions(doc):
    return list(AuditEvent.objects.filter(object_id=str(doc.public_id)).order_by("pk")
                .values_list("action", flat=True))


# --- files ------------------------------------------------------------------


def test_a_pdf_keeps_its_name_but_is_stored_under_a_random_one(owner, prop):
    doc = upload(owner, prop, file=pdf("Title deed.pdf"))
    assert doc.kind == Document.Kind.PDF and doc.filename == "Title deed.pdf" and doc.title == "Title deed"
    assert "Title deed" not in doc.file.name and doc.file.name.endswith(".pdf")
    assert doc.size > 0 and doc.uploaded_by == owner.user


def test_photos_are_re_encoded_as_jpeg(owner, prop):
    doc = upload(owner, prop, file=jpeg("gate.png", fmt="PNG"))
    assert doc.kind == Document.Kind.PHOTO and doc.filename == "gate.jpg" and doc.is_photo
    with doc.file.open("rb") as handle:
        assert handle.read(3) == b"\xff\xd8\xff"


def test_word_and_excel_files_are_checked_inside(owner, prop):
    assert upload(owner, prop, file=office("rules.docx")).kind == Document.Kind.WORD
    assert upload(owner, prop, file=office("rent.xlsx", "xl/")).kind == Document.Kind.EXCEL
    with pytest.raises(ValidationError) as err:
        upload(owner, prop, file=office("rules.docx", "xl/"))
    assert "file" in err.value.message_dict
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("a.txt", "x")
    with pytest.raises(ValidationError):  # any zip renamed to .docx
        upload(owner, prop, file=SimpleUploadedFile("x.docx", out.getvalue()))


@pytest.mark.parametrize("content", [b"MZ\x90\x00 a program", b"<html><script>", b"plain text"])
def test_other_files_are_refused(owner, prop, content):
    with pytest.raises(ValidationError) as err:
        upload(owner, prop, file=SimpleUploadedFile("x.pdf", content))
    assert "file" in err.value.message_dict
    assert not Document.all_objects.exists()


def test_files_over_10_mb_are_refused(owner, prop, monkeypatch):
    monkeypatch.setattr(services, "MAX_BYTES", 10)
    with pytest.raises(ValidationError) as err:
        upload(owner, prop, file=SimpleUploadedFile("big.pdf", b"%PDF-" + b"x" * 20))
    assert "10 MB" in str(err.value)


# --- places -----------------------------------------------------------------


def test_places_and_their_pages(owner, prop, lease, unit, tenant):
    on_prop = upload(owner, prop, title="Title deed")
    on_unit = upload(owner, unit, title="Unit photo")
    on_lease = upload(owner, lease, title="Signed lease")
    on_tenant = upload(owner, tenant, title="National ID", file=jpeg())
    assert (on_prop.place, on_unit.place, on_lease.place, on_tenant.place) == ("property", "unit", "lease", "tenant")
    assert on_unit.property == prop and on_lease.property == prop and on_tenant.property is None

    def shown(place):
        return set(Document.objects.filter(services.at(place)).values_list("title", flat=True))

    assert shown(prop) == {"Title deed"}
    assert shown(unit) == {"Unit photo", "Signed lease"}
    assert shown(lease) == {"Signed lease"}
    assert shown(tenant) == {"National ID", "Signed lease"}


def test_other_leases_stay_off_a_tenants_page(owner, prop, tenant):
    upload(owner, make_lease(owner, prop, code="B2"), title="Another lease")
    assert services.panel(owner, tenant)["count"] == 0


def test_nothing_is_uploaded_to_an_archived_place(owner, prop):
    prop.archive(owner.user)
    with pytest.raises(PermissionDenied):
        upload(owner, prop)


# --- scope ------------------------------------------------------------------


def test_other_organizations_see_nothing(owner, prop, tenant):
    upload(owner, prop)
    upload(owner, tenant)
    other = make_org()
    assert not services.visible_documents(other).exists()
    assert not services.can_view_at(other, prop) and not services.can_upload(other, prop)
    with pytest.raises(PermissionDenied):
        upload(other, prop)


def test_scoped_members_see_only_their_properties(owner, org, prop):
    elsewhere = make_property(org, name="Baobab Flats")
    here, there = upload(owner, prop, title="Here"), upload(owner, elsewhere, title="There")
    agent = add_member(org, "leasing_agent", properties=[prop])
    assert list(services.visible_documents(agent)) == [here]
    assert services.can_upload(agent, prop) and not services.can_upload(agent, elsewhere)
    assert not services.sees(agent, there)


def test_tenant_documents_need_the_right_to_see_id_details(owner, org, lease, tenant):
    ident = upload(owner, tenant, title="National ID")
    signed = upload(owner, lease, title="Signed lease")
    agent = add_member(org, "leasing_agent", all_properties=True)
    assert set(services.visible_documents(agent)) == {signed}
    assert not services.can_view_at(agent, tenant) and not services.can_upload(agent, tenant)
    assert services.panel(agent, tenant) is None
    assert set(services.visible_documents(owner)) == {ident, signed}


def test_who_may_edit_share_and_archive(owner, org, lease):
    doc = upload(owner, lease)
    agent = add_member(org, "leasing_agent", all_properties=True)
    assert services.sees(agent, doc)
    assert not services.can_manage(agent, doc) and not services.can_share(agent, doc)
    with pytest.raises(PermissionDenied):
        services.archive(agent, doc)
    with pytest.raises(PermissionDenied):
        services.set_shared(agent, doc, True)
    with pytest.raises(PermissionDenied):
        upload(agent, lease, share=True)
    manager = add_member(org, "manager", all_properties=True)
    assert services.can_manage(manager, doc) and services.can_share(manager, doc)


# --- changes and audit ------------------------------------------------------


def test_edit_share_archive_and_restore_are_on_record(owner, prop):
    doc = upload(owner, prop, title="Insurance", category=Document.Category.INSURANCE)
    services.update(owner, doc, title="Fire insurance 2026", category=Document.Category.INSURANCE,
                    expires_on=datetime.date(2026, 12, 31), note="Jubilee")
    doc.refresh_from_db()
    assert doc.title == "Fire insurance 2026" and doc.expires_on == datetime.date(2026, 12, 31)
    services.set_shared(owner, doc, True)
    services.set_shared(owner, doc, True)  # no change, nothing recorded
    services.set_shared(owner, doc, False)
    services.archive(owner, doc)
    assert not Document.objects.filter(pk=doc.pk).exists()
    services.restore(owner, doc)
    assert actions(doc) == ["documents.upload", "documents.edit", "documents.share", "documents.unshare",
                            "documents.archive", "documents.restore"]


def test_details_are_checked(owner, prop):
    doc = upload(owner, prop)
    with pytest.raises(ValidationError) as err:
        services.update(owner, doc, title=" ", category="NOPE", expires_on=None, note="x" * 301)
    assert set(err.value.message_dict) == {"title", "category", "note"}


def test_only_lease_and_property_documents_are_shared(owner, unit, tenant):
    with pytest.raises(ValidationError):
        upload(owner, unit, share=True)
    doc = upload(owner, tenant)
    assert not doc.shareable and not services.can_share(owner, doc)


def test_restoring_needs_a_live_place(owner, prop):
    doc = upload(owner, prop)
    services.archive(owner, doc)
    prop.archive(owner.user)
    with pytest.raises(ValidationError):
        services.restore(owner, doc)


def test_opening_a_tenant_document_is_on_record(owner, prop, tenant):
    ident, deed = upload(owner, tenant), upload(owner, prop)
    services.opened(owner, ident)
    services.opened(owner, deed)
    assert actions(ident)[-1] == "documents.open" and "documents.open" not in actions(deed)


# --- expiry -----------------------------------------------------------------


def test_expiring_documents(owner, prop):
    today = datetime.date(2026, 9, 30)
    upload(owner, prop, title="Lapsed", expires_on=today - datetime.timedelta(days=1))
    upload(owner, prop, title="Soon", expires_on=today + datetime.timedelta(days=30))
    upload(owner, prop, title="Later", expires_on=today + datetime.timedelta(days=31))
    upload(owner, prop, title="Never")
    assert set(services.expiring(owner, today).values_list("title", flat=True)) == {"Lapsed", "Soon"}
    states = {d.title: d.expiry_state(today) for d in Document.objects.all()}
    assert states == {"Lapsed": "expired", "Soon": "expiring", "Later": "", "Never": ""}
