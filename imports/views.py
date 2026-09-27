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

from . import services
from .models import ImportBatch

ROW_PAGE_SIZE = 100
KINDS = {"units": ImportBatch.Kind.UNITS, "tenants": ImportBatch.Kind.TENANTS}


class UploadForm(forms.Form):
    file = forms.FileField(label=gettext_lazy("CSV file"), widget=forms.ClearableFileInput(attrs={"accept": ".csv"}))


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
        return render(request, self.template_name, {"page": page, "cards": cards, "form": UploadForm()})


class ImportUploadView(OrgMemberRequiredMixin, View):
    def post(self, request, kind):
        kind = _kind(request, kind)
        form = UploadForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, _("Choose a CSV file to upload."))
            return redirect("imports:list")
        try:
            batch = services.preview_import(request.membership, kind, form.cleaned_data["file"], request=request)
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
                batch = services.apply_import(m, batch, request=request)
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
                    messages.warning(request, _("Kept because they are already in use: %(labels)s")
                                     % {"labels": ", ".join(kept)})
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect("imports:detail", public_id=batch.public_id)
