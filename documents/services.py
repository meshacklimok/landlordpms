"""Documents (D-071). Views stay thin; the rules live here.

A "place" is the Property, Unit, Lease or Tenant a document belongs to.
"""

import datetime
import os
import secrets
import zipfile

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import can, visible_properties
from audit import services as audit
from inspections import photos
from leases.models import Lease, LeaseTenant
from properties.models import Property, Unit
from tenants.models import Tenant
from tenants.services import visible_tenants

from .models import EXPIRING_DAYS, Document

Kind = Document.Kind
Category = Document.Category

MAX_BYTES = 10 * 1024 * 1024
# Extension → (kind, a folder every real file of that kind has inside the zip).
OFFICE = {".docx": (Kind.WORD, "word/"), ".xlsx": (Kind.EXCEL, "xl/")}
EXTENSIONS = {Kind.PDF: ".pdf", Kind.PHOTO: ".jpg", Kind.WORD: ".docx", Kind.EXCEL: ".xlsx"}
CONTENT_TYPES = {
    Kind.PDF: "application/pdf",
    Kind.PHOTO: "image/jpeg",
    Kind.WORD: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    Kind.EXCEL: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
ACCEPT = "application/pdf,image/jpeg,image/png,.docx,.xlsx"
PANEL_SIZE = 10


# ---------------------------------------------------------------------------
# Places
# ---------------------------------------------------------------------------


def place_fields(place) -> dict:
    """The document fields for a place. Unit and lease documents also record their property."""
    if isinstance(place, Tenant):
        return {"tenant": place}
    if isinstance(place, Lease):
        return {"lease": place, "property": place.unit.property}
    if isinstance(place, Unit):
        return {"unit": place, "property": place.property}
    if isinstance(place, Property):
        return {"property": place}
    raise TypeError(f"Documents cannot belong to {type(place).__name__}")


def place_property(place) -> Property | None:
    return place_fields(place).get("property")


def place_archived(place) -> bool:
    fields = place_fields(place)
    return any(obj.is_archived for obj in fields.values())


def at(place) -> Q:
    """Documents shown on a place's page: a unit also shows its leases' documents, a tenant their leases'."""
    if isinstance(place, Tenant):
        return Q(tenant=place) | Q(lease__in=LeaseTenant.objects.filter(tenant=place).values("lease_id"))
    if isinstance(place, Lease):
        return Q(lease=place)
    if isinstance(place, Unit):
        return Q(unit=place) | Q(lease__unit=place)
    return Q(property=place, unit__isnull=True, lease__isnull=True)


# ---------------------------------------------------------------------------
# Scope (D-071 item 4)
# ---------------------------------------------------------------------------


def visible_documents(membership: Membership, queryset=None):
    qs = (queryset if queryset is not None else Document.objects.all()).for_org(membership.organization)
    q = Q(pk__in=[])
    if can(membership, "documents.view"):
        q |= Q(property__in=visible_properties(membership, Property.all_objects.all()).values("pk"))
        if can(membership, "tenants.view_sensitive"):
            q |= Q(property__isnull=True,
                   tenant__in=visible_tenants(membership, Tenant.all_objects.all()).values("pk"))
    return qs.filter(q)


def _can_at(membership: Membership, capability: str, place) -> bool:
    """`capability` where the place is: its property, or for a tenant `tenants.view_sensitive` and a visible tenant."""
    if isinstance(place, Tenant):
        return (place.organization_id == membership.organization_id and can(membership, capability)
                and can(membership, "tenants.view_sensitive")
                and visible_tenants(membership, Tenant.all_objects.all()).filter(pk=place.pk).exists())
    prop = place_property(place)
    return prop.organization_id == membership.organization_id and can(membership, capability, prop)


def can_view_at(membership: Membership, place) -> bool:
    return _can_at(membership, "documents.view", place)


def can_upload(membership: Membership, place) -> bool:
    return not place_archived(place) and _can_at(membership, "documents.upload", place)


def sees(membership: Membership, doc: Document) -> bool:
    return visible_documents(membership, Document.all_objects.all()).filter(pk=doc.pk).exists()


def _doc_place(doc: Document):
    return doc.tenant or doc.lease or doc.unit or doc.property


def can_manage(membership: Membership, doc: Document) -> bool:
    return sees(membership, doc) and _can_at(membership, "documents.manage", _doc_place(doc))


def can_share(membership: Membership, doc: Document) -> bool:
    return (doc.shareable and not doc.is_archived and sees(membership, doc)
            and can(membership, "documents.share_with_tenant", doc.property))


def uploadable_properties(membership: Membership) -> list[Property]:
    """Live properties where the member may upload, for the upload page opened from the list."""
    return [p for p in visible_properties(membership, Property.objects.all()).order_by("name")
            if can(membership, "documents.upload", p)]


def can_upload_anywhere(membership: Membership) -> bool:
    return can(membership, "documents.upload") and bool(uploadable_properties(membership))


# ---------------------------------------------------------------------------
# Files (D-071 item 3)
# ---------------------------------------------------------------------------


def _office_kind(upload, extension: str) -> str | None:
    kind, folder = OFFICE.get(extension, (None, ""))
    if kind is None:
        return None
    try:
        with zipfile.ZipFile(upload) as archive:
            names = archive.namelist()
    except (zipfile.BadZipFile, OSError, ValueError):
        return None
    finally:
        upload.seek(0)
    if "[Content_Types].xml" in names and any(n.startswith(folder) for n in names):
        return kind
    return None


def process_file(upload) -> tuple[ContentFile, str, str]:
    """Returns (file, kind, original name) or raises ValidationError on `file`."""
    if not upload:
        raise ValidationError({"file": _("Choose a file.")})
    if upload.size > MAX_BYTES:
        raise ValidationError({"file": _("The file is larger than 10 MB.")})
    name = os.path.basename(upload.name or "").strip() or "document"
    stem, extension = os.path.splitext(name)
    extension = extension.lower()
    head = upload.read(5)
    upload.seek(0)
    if head == b"%PDF-":
        kind = Kind.PDF
    elif head[:4] == b"PK\x03\x04":
        kind = _office_kind(upload, extension)
        if kind is None:
            raise ValidationError({"file": _("Use a PDF, a photo, or a Word or Excel file (.docx, .xlsx).")})
    else:
        try:
            image, _w, _h = photos.process(upload)
        except ValidationError:
            raise ValidationError(
                {"file": _("Use a PDF, a photo, or a Word or Excel file (.docx, .xlsx).")}) from None
        return image, Kind.PHOTO, f"{stem[:190]}.jpg"
    stored = ContentFile(upload.read(), name=f"{secrets.token_hex(16)}{EXTENSIONS[kind]}")
    return stored, kind, f"{stem[:190]}{EXTENSIONS[kind]}"


# ---------------------------------------------------------------------------
# Changes
# ---------------------------------------------------------------------------


def _audit(action, actor: Membership, doc: Document, request, changes=None):
    audit.record(action, actor=actor.user, organization=actor.organization, obj=doc, request=request,
                 changes=changes or {})


def _clean(title: str, category: str, note: str) -> dict:
    errors = {}
    title = (title or "").strip()
    if not title:
        errors["title"] = _("Give the document a title.")
    elif len(title) > 120:
        errors["title"] = _("Keep the title under 120 characters.")
    if category not in Category.values:
        errors["category"] = _("Choose a category.")
    note = (note or "").strip()
    if len(note) > 300:
        errors["note"] = _("Keep the note under 300 characters.")
    if errors:
        raise ValidationError(errors)
    return {"title": title, "category": category, "note": note}


def upload(actor: Membership, place, *, file, title: str = "", category: str = Category.OTHER,
           expires_on: datetime.date | None = None, note: str = "", share: bool = False,
           request=None) -> Document:
    if not can_upload(actor, place):
        raise PermissionDenied("documents.upload")
    stored, kind, filename = process_file(file)
    if not (title or "").strip():
        title = os.path.splitext(filename)[0][:120]
    fields = _clean(title, category, note)
    doc = Document(organization=actor.organization, **place_fields(place), **fields, expires_on=expires_on,
                   kind=kind, filename=filename, size=stored.size, uploaded_by=actor.user)
    if share:
        if not doc.shareable:
            raise ValidationError({"share": _("Only lease and property documents can be shared with tenants.")})
        if not can(actor, "documents.share_with_tenant", doc.property):
            raise PermissionDenied("documents.share_with_tenant")
        doc.shared_with_tenant = True
    doc.file = stored
    doc.save()
    _audit("documents.upload", actor, doc, request, {
        "place": [None, doc.place], "title": [None, doc.title], "category": [None, doc.category],
        "file": [None, doc.filename], **({"shared_with_tenant": [False, True]} if doc.shared_with_tenant else {})})
    return doc


def _lock(doc: Document) -> Document:
    return Document.all_objects.select_for_update().get(pk=doc.pk)


def update(actor: Membership, doc: Document, *, title: str, category: str, expires_on: datetime.date | None,
           note: str = "", request=None) -> Document:
    if not can_manage(actor, doc):
        raise PermissionDenied("documents.manage")
    fields = {**_clean(title, category, note), "expires_on": expires_on}
    with transaction.atomic():
        doc = _lock(doc)
        before = {k: getattr(doc, k) for k in fields}
        for k, v in fields.items():
            setattr(doc, k, v)
        doc.save(update_fields=[*fields, "updated_at"])
    changes = audit.diff({k: str(v or "") for k, v in before.items()}, {k: str(v or "") for k, v in fields.items()})
    if changes:
        _audit("documents.edit", actor, doc, request, changes)
    return doc


def set_shared(actor: Membership, doc: Document, shared: bool, *, request=None) -> Document:
    if not can_share(actor, doc):
        raise PermissionDenied("documents.share_with_tenant")
    with transaction.atomic():
        doc = _lock(doc)
        if doc.shared_with_tenant == shared:
            return doc
        doc.shared_with_tenant = shared
        doc.save(update_fields=["shared_with_tenant", "updated_at"])
    _audit("documents.share" if shared else "documents.unshare", actor, doc, request,
           {"shared_with_tenant": [not shared, shared]})
    return doc


def archive(actor: Membership, doc: Document, *, request=None) -> Document:
    if not can_manage(actor, doc):
        raise PermissionDenied("documents.manage")
    if doc.is_archived:
        raise ValidationError(_("This document is already archived."))
    doc.archive(actor.user)
    _audit("documents.archive", actor, doc, request)
    return doc


def restore(actor: Membership, doc: Document, *, request=None) -> Document:
    if not can_manage(actor, doc):
        raise PermissionDenied("documents.manage")
    if not doc.is_archived:
        raise ValidationError(_("This document is not archived."))
    if place_archived(_doc_place(doc)):
        raise ValidationError(_("Restore the place it belongs to first."))
    doc.restore()
    _audit("documents.restore", actor, doc, request)
    return doc


def opened(actor: Membership, doc: Document, *, request=None) -> None:
    """Tenant documents hold ID copies: every opening is on record (D-071 item 8)."""
    if doc.place == "tenant":
        _audit("documents.open", actor, doc, request)


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------


def expiring(membership: Membership, today: datetime.date | None = None):
    """Live documents expired or expiring within 30 days."""
    today = today or timezone.localdate()
    return visible_documents(membership).filter(expires_on__lte=today + datetime.timedelta(days=EXPIRING_DAYS))


def panel(membership: Membership, place) -> dict | None:
    """The Documents card on a property, unit, lease or tenant page; None when the member may not see it."""
    if not can_view_at(membership, place):
        return None
    qs = (visible_documents(membership).filter(at(place))
          .select_related("unit", "lease", "tenant").order_by("-created_at", "-pk"))
    return {
        "docs": list(qs[:PANEL_SIZE]), "count": qs.count(), "today": timezone.localdate(),
        "can_upload": can_upload(membership, place),
        # ?property=…, ?unit=…, ?lease=… or ?tenant=… for the list and upload pages.
        "query": f"{type(place).__name__.lower()}={place.public_id}",
    }
