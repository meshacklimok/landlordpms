"""Document pages (D-071). Thin views; services do the work.

Anything outside what the member may see is a 404, never a 403.
"""

import datetime
import uuid

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View

from accounts.mixins import CapabilityRequiredMixin
from accounts.permissions import can, visible_properties
from leases.models import Lease
from leases.services import visible_leases
from properties.models import Property, Unit
from properties.views import _apply_errors
from tenants.models import Tenant
from tenants.services import visible_tenants

from . import forms, services
from .models import EXPIRING_DAYS, Document

PAGE_SIZE = 50
PLACES = ("property", "unit", "lease", "tenant")


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return True


def _errors(exc: ValidationError) -> str:
    return " ".join(exc.messages)


def _place_from_query(request, *, live: bool = False):
    """The property, unit, lease or tenant named in the query string, if the member can see it; else a 404."""
    m = request.membership
    props = visible_properties(m, Property.all_objects.all())
    lookups = {
        "lease": visible_leases(m, Lease.all_objects.all()).select_related("unit__property"),
        "unit": Unit.all_objects.filter(property__in=props).select_related("property"),
        "tenant": visible_tenants(m, Tenant.all_objects.all()),
        "property": props,
    }
    for key, qs in lookups.items():
        value = request.GET.get(key, "")
        if value:
            if not _is_uuid(value):
                raise Http404
            place = get_object_or_404(qs, public_id=value)
            if live and services.place_archived(place):
                raise Http404
            return place
    return None


def place_url(place) -> str:
    if isinstance(place, Tenant):
        return reverse("tenants:detail", args=[place.public_id])
    if isinstance(place, Lease):
        return reverse("leases:detail", args=[place.public_id])
    if isinstance(place, Unit):
        return reverse("properties:unit", args=[place.public_id])
    return reverse("properties:detail", args=[place.public_id])


def file_response(doc: Document):
    """Photos open in the browser; anything else downloads under its original name. Never public."""
    try:
        handle = doc.file.open("rb")
    except FileNotFoundError:
        raise Http404 from None
    if doc.is_photo:
        response = FileResponse(handle, content_type="image/jpeg")
    else:
        response = FileResponse(handle, content_type=services.CONTENT_TYPES[doc.kind], as_attachment=True,
                                filename=doc.filename)
    response["Cache-Control"] = "private, max-age=3600"
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _get_document(request, public_id) -> Document:
    qs = services.visible_documents(request.membership, Document.all_objects.all()).select_related(
        "property", "unit__property", "lease__unit__property", "tenant", "uploaded_by", "archived_by")
    return get_object_or_404(qs, public_id=public_id)


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------


class DocumentListView(CapabilityRequiredMixin, View):
    template_name = "documents/list.html"
    required_capability = "documents.view"

    def filters(self, request):
        m = request.membership
        g = request.GET
        props = list(visible_properties(m, Property.all_objects.all()).order_by("name"))
        f = {
            "property": next((p for p in props if str(p.public_id) == g.get("property")), None),
            "category": g.get("category") if g.get("category") in Document.Category.values else "",
            "where": g.get("where") if g.get("where") in PLACES else "",
            "expiry": g.get("expiry") if g.get("expiry") in ("expiring", "expired") else "",
            "archived": g.get("archived") == "1",
            "q": g.get("q", "").strip()[:60],
            # From a unit, lease or tenant page's "All" link.
            "place": _place_from_query(request) if any(g.get(k) for k in ("unit", "lease", "tenant")) else None,
        }
        return f, props

    def queryset(self, request, f):
        base = Document.all_objects.archived() if f["archived"] else Document.objects.all()
        qs = services.visible_documents(request.membership, base)
        if f["place"] is not None:
            qs = qs.filter(services.at(f["place"]))
        if f["property"] is not None:
            qs = qs.filter(property=f["property"])
        if f["category"]:
            qs = qs.filter(category=f["category"])
        if f["where"] == "tenant":
            qs = qs.filter(tenant__isnull=False)
        elif f["where"] == "lease":
            qs = qs.filter(lease__isnull=False)
        elif f["where"] == "unit":
            qs = qs.filter(unit__isnull=False)
        elif f["where"] == "property":
            qs = qs.filter(property__isnull=False, unit__isnull=True, lease__isnull=True)
        today = timezone.localdate()
        if f["expiry"] == "expired":
            qs = qs.filter(expires_on__lt=today)
        elif f["expiry"] == "expiring":
            qs = qs.filter(expires_on__lte=today + datetime.timedelta(days=EXPIRING_DAYS))
        if f["q"]:
            qs = qs.filter(title__icontains=f["q"])
        return qs

    def get(self, request):
        m = request.membership
        f, props = self.filters(request)
        qs = (self.queryset(request, f)
              .select_related("property", "unit", "lease__unit", "tenant", "uploaded_by")
              .order_by("-created_at", "-pk"))
        page = Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))
        place_key = type(f["place"]).__name__.lower() if f["place"] else ""
        return render(request, self.template_name, {
            "page": page, "f": f, "properties": props, "categories": Document.Category.choices,
            "can_upload": services.can_upload_anywhere(m) if f["place"] is None
            else services.can_upload(m, f["place"]),
            "place_key": place_key, "upload_query": f"{place_key}={f['place'].public_id}" if place_key else "",
            "can_manage": can(m, "documents.manage"),
            "expiring": services.expiring(m).count(),
            "filtered": any(v for v in f.values()),
        })


# ---------------------------------------------------------------------------
# Uploading
# ---------------------------------------------------------------------------


class UploadView(CapabilityRequiredMixin, View):
    """Opened from a place's page (?property=, ?unit=, ?lease= or ?tenant=) or from the list (choose a property)."""

    template_name = "documents/upload.html"
    required_capability = "documents.upload"

    def load(self, request):
        place = _place_from_query(request, live=True)
        if place is not None and not services.can_upload(request.membership, place):
            raise Http404
        if place is None and not services.can_upload_anywhere(request.membership):
            raise Http404
        return place

    def form(self, request, place, data=None, files=None, initial=None):
        m = request.membership
        properties = None
        if place is None:
            properties = Property.objects.filter(pk__in=[p.pk for p in services.uploadable_properties(m)])
            shareable = can(m, "documents.share_with_tenant")
        else:
            # Only lease and property documents can be shared (D-071 item 5).
            shareable = (isinstance(place, (Property, Lease))
                         and can(m, "documents.share_with_tenant", services.place_property(place)))
        return forms.UploadForm(data, files, initial=initial, properties=properties, can_share=shareable)

    def render(self, request, place, form):
        return render(request, self.template_name, {
            "form": form, "place": place, "is_tenant": isinstance(place, Tenant),
            "back": place_url(place) if place else reverse("documents:list")})

    def get(self, request):
        place = self.load(request)
        initial = {}
        if isinstance(place, Lease):
            initial["category"] = Document.Category.LEASE
        elif isinstance(place, Tenant):
            initial["category"] = Document.Category.ID
        return self.render(request, place, self.form(request, place, initial=initial))

    def post(self, request):
        place = self.load(request)
        form = self.form(request, place, request.POST, request.FILES)
        if form.is_valid():
            data = dict(form.cleaned_data)
            target = data.pop("property", None) or place
            try:
                doc = services.upload(request.membership, target, request=request, **data)
            except ValidationError as exc:
                _apply_errors(form, exc)
            except PermissionDenied:
                raise Http404 from None
            else:
                messages.success(request, _("“%(title)s” uploaded.") % {"title": doc.title})
                return redirect(place_url(place) if place else reverse("documents:detail", args=[doc.public_id]))
        if request.FILES:
            form.add_error(None, _("Choose the file again."))
        return self.render(request, place, form)


# ---------------------------------------------------------------------------
# One document
# ---------------------------------------------------------------------------


class DocumentDetailView(CapabilityRequiredMixin, View):
    template_name = "documents/detail.html"
    required_capability = "documents.view"

    def context(self, request, doc, form=None):
        m = request.membership
        can_manage = services.can_manage(m, doc)
        place = doc.tenant or doc.lease or doc.unit or doc.property
        ctx = {
            "doc": doc, "today": timezone.localdate(), "place_url": place_url(place),
            "can_manage": can_manage, "can_share": services.can_share(m, doc),
        }
        if can_manage and not doc.is_archived:
            ctx["form"] = form or forms.DetailsForm(initial={
                "title": doc.title, "category": doc.category, "expires_on": doc.expires_on, "note": doc.note})
        return ctx

    def get(self, request, public_id):
        doc = _get_document(request, public_id)
        return render(request, self.template_name, self.context(request, doc))

    def post(self, request, public_id):
        doc = _get_document(request, public_id)
        m = request.membership
        action = request.POST.get("action", "")
        try:
            if action == "edit":
                form = forms.DetailsForm(request.POST)
                if not form.is_valid():
                    return render(request, self.template_name, self.context(request, doc, form=form))
                services.update(m, doc, request=request, **form.cleaned_data)
                messages.success(request, _("Saved."))
            elif action in ("share", "unshare"):
                services.set_shared(m, doc, action == "share", request=request)
                messages.success(request, _("The tenant can now see it in the portal.") if action == "share"
                                 else _("The tenant no longer sees it."))
            elif action == "archive":
                services.archive(m, doc, request=request)
                messages.success(request, _("Archived."))
            elif action == "restore":
                services.restore(m, doc, request=request)
                messages.success(request, _("Restored."))
        except ValidationError as exc:
            messages.error(request, _errors(exc))
        except PermissionDenied:
            raise Http404 from None
        return redirect("documents:detail", public_id=doc.public_id)


class FileView(CapabilityRequiredMixin, View):
    required_capability = "documents.view"

    def get(self, request, public_id):
        doc = _get_document(request, public_id)
        response = file_response(doc)
        services.opened(request.membership, doc, request=request)
        return response
