"""Condition report pages (D-047). Thin views; services do the work.

Anything outside the member's properties is a 404, never a 403.
"""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View

from accounts.mixins import CapabilityRequiredMixin
from accounts.permissions import can
from billing import deposits
from billing.forms import DeductForm
from leases.models import Lease
from leases.services import visible_leases
from properties.models import Unit
from properties.views import _apply_errors, _get_unit

from . import forms, services
from .models import Condition, ConditionPhoto, ConditionReport, UnitItem

KINDS = {"move-in": ConditionReport.Kind.MOVE_IN, "move-out": ConditionReport.Kind.MOVE_OUT}


def _get_report(request, public_id) -> ConditionReport:
    qs = services.visible_reports(request.membership).select_related(
        "lease__unit__property", "unit__property", "inspected_by", "completed_by", "cancelled_by")
    return get_object_or_404(qs, public_id=public_id)


class StartView(CapabilityRequiredMixin, View):
    required_capability = "inspections.record"

    def post(self, request, public_id, kind):
        if kind not in KINDS:
            raise Http404
        lease = get_object_or_404(visible_leases(request.membership, Lease.all_objects.all()), public_id=public_id)
        try:
            report = services.start(request.membership, lease, KINDS[kind], request=request)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect("leases:detail", public_id=lease.public_id)
        return redirect("inspections:report", public_id=report.public_id)


class ReportView(CapabilityRequiredMixin, View):
    template_name = "inspections/report.html"
    required_capability = "inspections.view"

    def context(self, request, report, **forms_in):
        m, prop = request.membership, report.unit.property
        before = services.baseline(report)
        table = services.rows(report, before)
        can_edit = report.is_draft and can(m, "inspections.record", prop)
        ctx = {
            "report": report, "lease": report.lease, "unit": report.unit, "property": prop, "rows": table,
            "before": before, "general_photos": report.photos.filter(line__isnull=True),
            "conditions": Condition.choices, "can_edit": can_edit,
            "can_cancel": (report.status != ConditionReport.Status.CANCELLED and can(m, "inspections.record", prop)
                           and not report.deposit_entries.exists()),
            "worse_count": sum(1 for r in table if r["worse"]),
            "left_count": sum(1 for r in table if not r["line"].condition),
            "deductions": report.deposit_entries.order_by("entry_date", "pk"),
            "can_see_account": can(m, "invoices.view", prop),
        }
        if can_edit:
            ctx["details_form"] = forms_in.get("details_form") or forms.DetailsForm(initial={
                "inspected_on": report.inspected_on.isoformat(), "tenant_present": report.tenant_present,
                "tenant_comments": report.tenant_comments, "keys_handed": report.keys_handed,
                "notes": report.notes})
            ctx["item_form"] = forms_in.get("item_form") or forms.ItemForm()
            ctx["photo_form"] = forms_in.get("photo_form") or forms.PhotoForm()
            ctx["photo_error_line"] = forms_in.get("photo_error_line")
        if ctx["can_cancel"]:
            ctx["cancel_form"] = forms_in.get("cancel_form") or forms.CancelForm()
        if services.can_deduct(m, report):
            ctx["held"] = deposits.held(report.lease)
            if ctx["held"] > 0:
                ctx["deduct_form"] = forms_in.get("deduct_form") or DeductForm(prefix="deduct", initial={
                    "entry_date": timezone.localdate().isoformat(),
                    "reason": services.deduction_reason(report, table)})
        return ctx

    def get(self, request, public_id):
        report = _get_report(request, public_id)
        return render(request, self.template_name, self.context(request, report))

    def post(self, request, public_id):
        report = _get_report(request, public_id)
        action = request.POST.get("action", "")
        handler = getattr(self, f"do_{action}", None)
        if handler is None:
            return redirect("inspections:report", public_id=report.public_id)
        return handler(request, report)

    def _back(self, report):
        return redirect("inspections:report", public_id=report.public_id)

    def _render(self, request, report, **forms_in):
        return render(request, self.template_name, self.context(request, report, **forms_in))

    def _save(self, request, report):
        """Saves the details and the line conditions. Returns the bound form (with errors if any)."""
        form = forms.DetailsForm(request.POST)
        if not form.is_valid():
            return form
        lines = {}
        for line in report.lines.all():
            key = f"condition-{line.pk}"
            if key in request.POST:
                lines[line.pk] = (request.POST.get(key, ""), request.POST.get(f"note-{line.pk}", ""))
        try:
            services.save_details(request.membership, report, lines=lines, request=request, **form.cleaned_data)
        except ValidationError as exc:
            _apply_errors(form, exc)
        return form

    def do_save(self, request, report):
        form = self._save(request, report)
        if form.errors:
            return self._render(request, report, details_form=form)
        messages.success(request, _("Report saved."))
        return self._back(report)

    def do_complete(self, request, report):
        form = self._save(request, report)
        if form.errors:
            return self._render(request, report, details_form=form)
        try:
            services.complete(request.membership, report, request=request)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, _("Report completed. It can no longer be changed."))
        return self._back(report)

    def do_add_line(self, request, report):
        form = forms.ItemForm(request.POST)
        if form.is_valid():
            try:
                services.add_line(request.membership, report, request=request, **form.cleaned_data)
            except ValidationError as exc:
                _apply_errors(form, exc)
            else:
                messages.success(request, _("Item added to the report and the unit's register."))
                return self._back(report)
        return self._render(request, report, item_form=form)

    def do_add_photo(self, request, report):
        form = forms.PhotoForm(request.POST, request.FILES)
        line_id = request.POST.get("line", "")
        line = None
        if line_id:
            line = get_object_or_404(report.lines, pk=int(line_id) if line_id.isdigit() else 0)
        if form.is_valid():
            try:
                services.add_photo(request.membership, report, form.cleaned_data["image"], line=line,
                                   caption=form.cleaned_data["caption"], request=request)
            except ValidationError as exc:
                _apply_errors(form, exc)
            else:
                messages.success(request, _("Photo added."))
                return self._back(report)
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
        return self._back(report)

    def do_remove_photo(self, request, report):
        photo = get_object_or_404(ConditionPhoto.objects.filter(report=report),
                                  public_id=request.POST.get("photo") or None)
        try:
            services.remove_photo(request.membership, photo, request=request)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, _("Photo removed."))
        return self._back(report)

    def do_cancel(self, request, report):
        form = forms.CancelForm(request.POST)
        if form.is_valid():
            try:
                services.cancel(request.membership, report, reason=form.cleaned_data["reason"], request=request)
            except ValidationError as exc:
                _apply_errors(form, exc)
            else:
                messages.success(request, _("Report cancelled. A new one can be started from the lease."))
                return self._back(report)
        return self._render(request, report, cancel_form=form)

    def do_deduct(self, request, report):
        form = DeductForm(request.POST, prefix="deduct")
        if form.is_valid():
            data = form.cleaned_data
            try:
                services.deduct(request.membership, report, amount=data["amount"], reason=data["reason"],
                                deposit_type=data["deposit_type"], entry_date=data["entry_date"],
                                apply_to_balance=data["apply_to_balance"], request=request)
            except ValidationError as exc:
                _apply_errors(form, exc)
            else:
                messages.success(request, _("Deduction recorded. It cites this report."))
                return self._back(report)
        return self._render(request, report, deduct_form=form)


class PhotoView(CapabilityRequiredMixin, View):
    """Serves a photo only to members who may see its report. Never public, never cached by proxies."""

    required_capability = "inspections.view"

    def get(self, request, public_id):
        photo = get_object_or_404(
            ConditionPhoto.objects.filter(report__in=services.visible_reports(request.membership)),
            public_id=public_id)
        try:
            handle = photo.image.open("rb")
        except FileNotFoundError:
            raise Http404 from None
        response = FileResponse(handle, content_type="image/jpeg")
        response["Cache-Control"] = "private, max-age=3600"
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response


class RegisterView(CapabilityRequiredMixin, View):
    template_name = "inspections/register.html"
    required_capability = "inspections.view"

    def _unit(self, request, public_id) -> Unit:
        return _get_unit(request, public_id)

    def context(self, request, unit, form=None):
        can_edit = can(request.membership, "inspections.record", unit.property) and not unit.is_archived
        return {
            "unit": unit, "property": unit.property, "can_edit": can_edit,
            "items": UnitItem.objects.filter(unit=unit), "archived": UnitItem.all_objects.filter(unit=unit).archived(),
            "reports": services.visible_reports(request.membership).filter(unit=unit).select_related("lease"),
            "form": (form or forms.RegisterItemForm()) if can_edit else None,
            "edit_id": request.GET.get("edit", ""),
        }

    def get(self, request, public_id):
        unit = self._unit(request, public_id)
        return render(request, self.template_name, self.context(request, unit))

    def post(self, request, public_id):
        unit = self._unit(request, public_id)
        m, action = request.membership, request.POST.get("action", "")
        try:
            if action == "defaults":
                if services.fill_defaults(m, unit, request=request):
                    messages.success(request, _("Standard items added. Edit them to match the unit."))
            elif action in ("add", "edit"):
                form = forms.RegisterItemForm(request.POST)
                if not form.is_valid():
                    if action == "edit":
                        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
                        url = reverse("inspections:register", args=[unit.public_id])
                        return redirect(f"{url}?edit={request.POST.get('item', '')}")
                    return render(request, self.template_name, self.context(request, unit, form=form))
                if action == "add":
                    services.add_item(m, unit, request=request, **form.cleaned_data)
                    messages.success(request, _("Item added."))
                else:
                    item = self._item(unit, request.POST.get("item"))
                    services.update_item(m, item, request=request, **form.cleaned_data)
                    messages.success(request, _("Item saved."))
            elif action == "archive":
                services.archive_item(m, self._item(unit, request.POST.get("item")), request=request)
                messages.success(request, _("Item archived. Reports already made keep it."))
            elif action == "restore":
                services.restore_item(m, self._item(unit, request.POST.get("item")), request=request)
                messages.success(request, _("Item restored."))
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect("inspections:register", public_id=unit.public_id)

    def _item(self, unit, pk) -> UnitItem:
        return get_object_or_404(UnitItem.all_objects.filter(unit=unit), pk=int(pk) if (pk or "").isdigit() else 0)
