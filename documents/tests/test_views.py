"""Documents pages (D-071): the list, uploading, one document, the file, place cards, the portal and home."""

import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from documents.models import Document
from leases.models import Lease
from reports import home
from reports.tests.test_income import make_lease

from .conftest import jpeg, office, pdf, portal_user, upload

pytestmark = pytest.mark.django_db


def detail(doc):
    return reverse("documents:detail", args=[doc.public_id])


def file_url(doc):
    return reverse("documents:file", args=[doc.public_id])


# --- list -------------------------------------------------------------------


def test_the_list_and_its_filters(client, owner, prop, lease):
    deed = upload(owner, prop, title="Acacia deed copy", category=Document.Category.TITLE)
    signed = upload(owner, lease, title="Signed lease", category=Document.Category.LEASE)
    cover = upload(owner, prop, title="Fire cover", category=Document.Category.INSURANCE,
                   expires_on=timezone.localdate() + datetime.timedelta(days=5))
    login(client, owner)
    url = reverse("documents:list")
    page = client.get(url).content.decode()
    assert deed.title in page and signed.title in page and cover.title in page
    page = client.get(url, {"category": "LEASE"}).content.decode()
    assert signed.title in page and deed.title not in page
    page = client.get(url, {"expiry": "expiring"}).content.decode()
    assert cover.title in page and deed.title not in page
    page = client.get(url, {"where": "lease"}).content.decode()
    assert signed.title in page and deed.title not in page
    page = client.get(url, {"q": "deed copy"}).content.decode()
    assert deed.title in page and signed.title not in page
    page = client.get(url, {"lease": str(lease.public_id)}).content.decode()
    assert signed.title in page and deed.title not in page


def test_archived_documents_are_listed_on_request(client, owner, prop):
    from documents import services
    doc = upload(owner, prop, title="Old rules")
    services.archive(owner, doc)
    login(client, owner)
    assert doc.title not in client.get(reverse("documents:list")).content.decode()
    assert doc.title in client.get(reverse("documents:list"), {"archived": "1"}).content.decode()


def test_who_can_open_the_list(client, org, prop):
    login(client, add_member(org, "accountant", all_properties=True))
    assert client.get(reverse("documents:list")).status_code == 403
    login(client, add_member(org, "leasing_agent", properties=[prop]))
    assert client.get(reverse("documents:list")).status_code == 200


def test_a_place_in_another_org_is_404(client, owner, prop):
    other = make_org()
    theirs = make_property(other.organization)
    login(client, owner)
    assert client.get(reverse("documents:list"), {"lease": "not-a-uuid"}).status_code == 404
    assert client.get(reverse("documents:upload"), {"property": str(theirs.public_id)}).status_code == 404


# --- upload -----------------------------------------------------------------


def test_upload_from_a_lease_page(client, owner, lease):
    login(client, owner)
    url = reverse("documents:upload") + f"?lease={lease.public_id}"
    page = client.get(url).content.decode()
    assert 'value="LEASE" selected' in page and 'name="share"' in page
    response = client.post(url, {"file": pdf("Signed.pdf"), "title": "", "category": "LEASE", "share": "on"})
    assert response.status_code == 302 and response.url == reverse("leases:detail", args=[lease.public_id])
    doc = Document.objects.get()
    assert doc.lease == lease and doc.title == "Signed" and doc.shared_with_tenant


def test_upload_from_the_list_chooses_a_property(client, owner, prop):
    login(client, owner)
    url = reverse("documents:upload")
    assert 'name="property"' in client.get(url).content.decode()
    response = client.post(url, {"property": str(prop.public_id), "file": office(), "title": "House rules",
                                 "category": "RULES"})
    doc = Document.objects.get()
    assert response.url == detail(doc) and doc.property == prop and doc.kind == Document.Kind.WORD


def test_a_bad_file_is_refused_on_the_form(client, owner, prop):
    from django.core.files.uploadedfile import SimpleUploadedFile
    login(client, owner)
    url = reverse("documents:upload") + f"?property={prop.public_id}"
    page = client.post(url, {"file": SimpleUploadedFile("x.pdf", b"MZ nope"), "category": "OTHER"}).content.decode()
    assert "Use a PDF" in page and "Choose the file again" in page
    assert not Document.all_objects.exists()


def test_tenant_documents_are_not_offered_without_the_right(client, org, owner, tenant, lease):
    agent = add_member(org, "leasing_agent", all_properties=True)
    login(client, agent)
    assert client.get(reverse("documents:upload"), {"tenant": str(tenant.public_id)}).status_code == 404
    ident = upload(owner, tenant, title="National ID copy", file=jpeg())
    assert client.get(detail(ident)).status_code == 404
    assert client.get(file_url(ident)).status_code == 404
    page = client.get(reverse("tenants:detail", args=[tenant.public_id])).content.decode()
    assert "National ID copy" not in page
    assert 'name="share"' not in client.get(reverse("documents:upload"),
                                            {"lease": str(lease.public_id)}).content.decode()


# --- one document -----------------------------------------------------------


def test_edit_share_and_archive_from_the_page(client, owner, lease):
    doc = upload(owner, lease, title="Lease")
    login(client, owner)
    assert "Share with the tenant" in client.get(detail(doc)).content.decode()
    client.post(detail(doc), {"action": "edit", "title": "Signed lease", "category": "LEASE", "expires_on": "",
                              "note": "Both pages"})
    client.post(detail(doc), {"action": "share"})
    doc.refresh_from_db()
    assert doc.title == "Signed lease" and doc.note == "Both pages" and doc.shared_with_tenant
    client.post(detail(doc), {"action": "archive"})
    doc.refresh_from_db()
    assert doc.is_archived
    assert "Restore" in client.get(detail(doc)).content.decode()


def test_a_viewer_cannot_change_a_document(client, owner, org, prop):
    doc = upload(owner, prop, title="Deed")
    login(client, add_member(org, "leasing_agent", all_properties=True))
    page = client.get(detail(doc)).content.decode()
    assert 'value="archive"' not in page and 'value="edit"' not in page
    assert client.post(detail(doc), {"action": "archive"}).status_code == 404
    doc.refresh_from_db()
    assert not doc.is_archived


def test_the_file_is_private(client, owner, prop, tenant):
    deed = upload(owner, prop, file=pdf("Deed.pdf"))
    ident = upload(owner, tenant, file=jpeg())
    login(client, owner)
    response = client.get(file_url(deed))
    assert response["Content-Type"] == "application/pdf" and "attachment" in response["Content-Disposition"]
    assert 'filename="Deed.pdf"' in response["Content-Disposition"]
    assert response["X-Content-Type-Options"] == "nosniff" and "private" in response["Cache-Control"]
    assert response["X-Robots-Tag"] == "noindex, nofollow"
    response = client.get(file_url(ident))
    assert response["Content-Type"] == "image/jpeg" and "attachment" not in response.get("Content-Disposition", "")
    from audit.models import AuditEvent
    assert AuditEvent.objects.filter(action="documents.open", object_id=str(ident.public_id)).count() == 1
    client.logout()
    assert client.get(file_url(deed)).status_code in (302, 403, 404)


def test_another_org_gets_404(client, owner, prop):
    doc = upload(owner, prop)
    login(client, make_org())
    assert client.get(detail(doc)).status_code == 404 and client.get(file_url(doc)).status_code == 404


# --- place pages ------------------------------------------------------------


def test_cards_on_the_place_pages(client, owner, prop, lease, unit, tenant):
    upload(owner, prop, title="Title deed")
    upload(owner, lease, title="Signed lease")
    upload(owner, tenant, title="National ID", file=jpeg())
    login(client, owner)
    page = client.get(reverse("properties:detail", args=[prop.public_id])).content.decode()
    assert "Title deed" in page and "Signed lease" not in page
    page = client.get(reverse("properties:unit", args=[unit.public_id])).content.decode()
    assert "Signed lease" in page and "Title deed" not in page
    page = client.get(reverse("leases:detail", args=[lease.public_id])).content.decode()
    assert "Signed lease" in page and f"lease={lease.public_id}" in page
    page = client.get(reverse("tenants:detail", args=[tenant.public_id])).content.decode()
    assert "National ID" in page and "Signed lease" in page


# --- portal -----------------------------------------------------------------


def test_the_tenant_sees_only_shared_documents(client, owner, prop, lease):
    user = portal_user(owner, lease)
    shared = upload(owner, lease, title="Signed lease", share=True)
    rules = upload(owner, prop, title="House rules", share=True)
    private = upload(owner, lease, title="Internal checklist")
    neighbour = upload(owner, make_lease(owner, prop, code="B2"), title="Neighbour lease", share=True)
    client.force_login(user)
    page = client.get(reverse("portal:lease", args=[lease.public_id])).content.decode()
    assert "Signed lease" in page and "House rules" in page
    assert "Internal checklist" not in page and "Neighbour lease" not in page
    response = client.get(reverse("portal:document", args=[shared.public_id]))
    assert response.status_code == 200 and response["Cache-Control"] == "private, no-store"
    assert client.get(reverse("portal:document", args=[rules.public_id])).status_code == 200
    assert client.get(reverse("portal:document", args=[private.public_id])).status_code == 404
    assert client.get(reverse("portal:document", args=[neighbour.public_id])).status_code == 404


def test_property_documents_stop_when_the_lease_ends(client, owner, prop, lease):
    user = portal_user(owner, lease)
    signed = upload(owner, lease, title="Signed lease", share=True)
    rules = upload(owner, prop, title="House rules", share=True)
    Lease.all_objects.filter(pk=lease.pk).update(status=Lease.Status.ENDED)
    client.force_login(user)
    page = client.get(reverse("portal:lease", args=[lease.public_id])).content.decode()
    assert "Signed lease" in page and "House rules" not in page
    assert client.get(reverse("portal:document", args=[signed.public_id])).status_code == 200
    assert client.get(reverse("portal:document", args=[rules.public_id])).status_code == 404


def test_unsharing_or_archiving_hides_it_from_the_tenant(client, owner, lease):
    from documents import services
    user = portal_user(owner, lease)
    a = upload(owner, lease, title="A", share=True)
    b = upload(owner, lease, title="B", share=True)
    services.set_shared(owner, a, False)
    services.archive(owner, b)
    client.force_login(user)
    assert client.get(reverse("portal:document", args=[a.public_id])).status_code == 404
    assert client.get(reverse("portal:document", args=[b.public_id])).status_code == 404


def test_staff_without_a_portal_account_get_404(client, owner, lease):
    doc = upload(owner, lease, share=True)
    client.force_login(owner.user)
    assert client.get(reverse("portal:document", args=[doc.public_id])).status_code in (302, 404)


# --- home -------------------------------------------------------------------


def test_home_task_for_expiring_documents(owner, org, prop):
    today = datetime.date(2026, 9, 30)
    upload(owner, prop, title="Permit", expires_on=today + datetime.timedelta(days=10))
    upload(owner, prop, title="Deed")
    tasks = {t.key: t for t in home.home(owner, today=today).tasks}
    assert tasks["documents"].count == 1 and tasks["documents"].url.endswith("?expiry=expiring")
    agent = add_member(org, "leasing_agent", all_properties=True)
    assert "documents" not in {t.key for t in home.home(agent, today=today).tasks}
