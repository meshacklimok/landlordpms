from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views import View

from accounts.mixins import OrgMemberRequiredMixin
from accounts.permissions import can
from meters import ocr

from . import scan, services, sheets
from .models import ImportBatch

ROW_PAGE_SIZE = 100
KINDS = {"units": ImportBatch.Kind.UNITS, "tenants": ImportBatch.Kind.TENANTS,
         "leases": ImportBatch.Kind.LEASES, "balances": ImportBatch.Kind.BALANCES}


class UploadForm(forms.Form):
    file = forms.FileField(label=gettext_lazy("Excel or CSV file"),
                           widget=forms.ClearableFileInput(attrs={"accept": ".xlsx,.csv"}))
    activate = forms.BooleanField(required=False)


class PhotoForm(forms.Form):
    photo = forms.FileField(label=gettext_lazy("Photo of the filled form"),
                            widget=forms.ClearableFileInput(attrs={"accept": "image/*"}))
    activate = forms.BooleanField(required=False)


def _kinds(membership) -> list[str]:
    return [k for k in ImportBatch.Kind.values if services.can_import(membership, k)]


def _kind(request, slug) -> str:
    kind = KINDS.get(slug)
    if kind is None:
        raise Http404
    if not services.can_import(request.membership, kind):
        raise PermissionDenied(services.CAPABILITY[kind])
    return kind


class ImportListView(OrgMemberRequiredMixin, View):
    template_name = "imports/import_list.html"

    def get(self, request):
        kinds = _kinds(request.membership)
        if not kinds:
            raise PermissionDenied("imports")
        batches = services.visible_batches(request.membership).select_related("created_by").defer("rows")
        page = Paginator(batches, 25).get_page(request.GET.get("page"))
        cards = [(slug, kind) for slug, kind in KINDS.items() if kind in kinds]
        m = request.membership
        return render(request, self.template_name, {
            "page": page, "cards": cards, "form": UploadForm(), "can_activate": can(m, "leases.activate"),
            "photos": ocr.available(m.organization),
        })


class ImportUploadView(OrgMemberRequiredMixin, View):
    def post(self, request, kind):
        kind = _kind(request, kind)
        form = UploadForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, _("Choose an Excel or CSV file to upload."))
            return redirect("imports:list")
        try:
            batch = services.preview_import(request.membership, kind, form.cleaned_data["file"], request=request,
                                            activate=form.cleaned_data["activate"])
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect("imports:list")
        return redirect("imports:detail", public_id=batch.public_id)


class ImportTemplateView(OrgMemberRequiredMixin, View):
    def get(self, request, kind):
        kind = _kind(request, kind)
        response = HttpResponse(services.template_csv(kind), content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{kind.lower()}-template.csv"'
        return response


class ImportExcelTemplateView(OrgMemberRequiredMixin, View):
    def get(self, request, kind):
        kind = _kind(request, kind)
        response = HttpResponse(
            sheets.template_xlsx(request.membership, kind),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response["Content-Disposition"] = f'attachment; filename="{kind.lower()}-sheet.xlsx"'
        return response


class ImportPrintView(OrgMemberRequiredMixin, View):
    def get(self, request, kind):
        kind = _kind(request, kind)
        if kind not in sheets.PRINT_COLUMNS:
            raise Http404
        response = HttpResponse(sheets.template_pdf(request.membership, kind), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{kind.lower()}-form.pdf"'
        return response


class ImportPhotoView(OrgMemberRequiredMixin, View):
    def post(self, request, kind):
        kind = _kind(request, kind)
        form = PhotoForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, _("Choose a photo of the filled form."))
            return redirect("imports:list")
        m = request.membership
        try:
            rows = scan.read_form(m, kind, form.cleaned_data["photo"])
            batch = services.preview_rows(m, kind, rows, file_name=_("Photo of a filled form"),
                                          activate=form.cleaned_data["activate"], source=ImportBatch.Source.PHOTO)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect("imports:list")
        return redirect("imports:detail", public_id=batch.public_id)


class ImportDetailView(OrgMemberRequiredMixin, View):
    template_name = "imports/import_detail.html"

    def get_batch(self, request, public_id) -> ImportBatch:
        return get_object_or_404(services.visible_batches(request.membership), public_id=public_id)

    def get(self, request, public_id):
        batch = self.get_batch(request, public_id)
        only_errors = request.GET.get("errors") == "1"
        rows = [r for r in batch.rows if r["errors"]] if only_errors else batch.rows
        columns = [c for c in services.COLUMNS[batch.kind]
                   if c not in services.SENSITIVE or can(request.membership, "tenants.view_sensitive")]
        page = Paginator(rows, ROW_PAGE_SIZE).get_page(request.GET.get("page"))
        for row in page:
            row["cells"] = [row["data"].get(c, "") for c in columns]
        return render(request, self.template_name, {
            "batch": batch, "expired": services.is_expired(batch), "page": page, "columns": columns,
            "only_errors": only_errors,
        })

    def post(self, request, public_id):
        batch = self.get_batch(request, public_id)
        m = request.membership
        action = request.POST.get("action")
        try:
            if action == "apply":
                batch = services.apply_import(m, batch, request=request,
                                              checked_by_eye=request.POST.get("checked") == "1")
                messages.success(request, _("Imported %(ok)s rows, skipped %(bad)s.")
                                 % {"ok": batch.ok_count, "bad": batch.error_count})
            elif action == "discard":
                services.discard_import(m, batch)
                messages.success(request, _("Import discarded. Nothing was added."))
                return redirect("imports:list")
            elif action == "undo":
                removed, kept = services.undo_import(m, batch, request=request)
                messages.success(request, _("Removed %(n)s records.") % {"n": len(removed)})
                if kept:
                    messages.warning(request, _("Kept because they are in use or were changed since: %(labels)s")
                                     % {"labels": ", ".join(kept)})
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect("imports:detail", public_id=batch.public_id)
