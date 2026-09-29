"""Expense pages (D-067). Thin views; services do the work.

Anything outside the member's properties is a 404, never a 403.
"""

import csv
import datetime

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
from properties.models import Property
from properties.views import _apply_errors
from reports.metrics import month_end
from reports.views import _csv_response

from . import forms, services
from .models import Expense, ExpenseCategory, Supplier

Status = Expense.Status
PAGE_SIZE = 50


def _errors(exc: ValidationError) -> str:
    return " ".join(exc.messages)


def _month(value: str) -> datetime.date | None:
    try:
        return datetime.datetime.strptime((value or "").strip()[:7], "%Y-%m").date()
    except ValueError:
        return None


def _get_expense(request, public_id) -> Expense:
    qs = services.visible_expenses(request.membership).select_related(
        "property", "category", "supplier", "recorded_by", "approved_by", "rejected_by", "voided_by")
    return get_object_or_404(qs, public_id=public_id)


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------


class ExpenseListView(CapabilityRequiredMixin, View):
    template_name = "expenses/list.html"
    required_capability = "expenses.view"

    def filters(self, request):
        m, org = request.membership, request.membership.organization
        g = request.GET
        props = list(visible_properties(m, Property.all_objects.all()).order_by("name"))
        cats = list(ExpenseCategory.all_objects.for_org(org).order_by("name"))
        sups = list(Supplier.all_objects.for_org(org).order_by("name"))
        f = {
            "property": next((p for p in props if str(p.public_id) == g.get("property")), None),
            "category": next((c for c in cats if str(c.public_id) == g.get("category")), None),
            "supplier": next((s for s in sups if str(s.public_id) == g.get("supplier")), None),
            "status": g.get("status") if g.get("status") in Status.values else "",
            "from": _month(g.get("from", "")),
            "to": _month(g.get("to", "")),
        }
        return f, {"properties": props, "categories": cats, "suppliers": sups}

    def queryset(self, request, f):
        qs = services.visible_expenses(request.membership)
        for name in ("property", "category", "supplier"):
            if f[name] is not None:
                qs = qs.filter(**{name: f[name]})
        if f["status"]:
            qs = qs.filter(status=f["status"])
        if f["from"]:
            qs = qs.filter(paid_on__gte=f["from"])
        if f["to"]:
            qs = qs.filter(paid_on__lte=month_end(f["to"]))
        return qs

    def get(self, request):
        m = request.membership
        f, choices = self.filters(request)
        qs = self.queryset(request, f).select_related("property", "category", "supplier")
        if request.GET.get("format") == "csv":
            if not can(m, "reports.export"):
                raise PermissionDenied("reports.export")
            return self.csv(qs)
        page = Paginator(qs.order_by("-paid_on", "-pk"), PAGE_SIZE).get_page(request.GET.get("page"))
        query = request.GET.copy()
        query.pop("page", None)
        query["format"] = "csv"
        return render(request, self.template_name, {
            "page": page, "f": f, **choices, "statuses": Status.choices, "totals": services.totals(qs),
            "currency": m.organization.currency, "can_record": bool(services.recordable_properties(m)),
            "can_export": can(m, "reports.export"), "csv_query": query.urlencode(),
            "to_approve": services.to_approve(m).count(),
            "filtered": any(v for v in f.values()),
        })

    def csv(self, qs):
        response = _csv_response(f"expenses-{timezone.localdate():%Y-%m-%d}.csv")
        writer = csv.writer(response)
        writer.writerow(["Number", "Date paid", "Property", "Category", "Description", "Supplier", "Amount",
                         "Paid by", "Reference", "Status", "Receipt"])
        for e in qs.order_by("paid_on", "pk"):
            writer.writerow([e.number, e.paid_on.isoformat(), e.property.name, e.category.name, e.description,
                             e.supplier.name if e.supplier else "", e.amount, e.get_method_display(), e.reference,
                             e.get_status_display(), "yes" if e.receipt else ""])
        return response


class ExpenseCreateView(CapabilityRequiredMixin, View):
    template_name = "expenses/form.html"
    required_capability = "expenses.submit"

    def form(self, request, data=None, files=None, initial=None):
        m, org = request.membership, request.membership.organization
        ids = [p.pk for p in services.recordable_properties(m)]
        return forms.ExpenseForm(
            data, files, initial=initial,
            properties=Property.objects.filter(pk__in=ids).order_by("name"),
            categories=services.categories(org),
            suppliers=Supplier.objects.for_org(org).order_by("name"))

    def get(self, request):
        initial = {"paid_on": timezone.localdate().isoformat()}
        props = services.recordable_properties(request.membership)
        if len(props) == 1:
            initial["property"] = str(props[0].public_id)
        elif request.GET.get("property"):
            initial["property"] = request.GET["property"]
        return render(request, self.template_name, {"form": self.form(request, initial=initial)})

    def post(self, request):
        form = self.form(request, request.POST, request.FILES)
        if form.is_valid():
            data = dict(form.cleaned_data)
            prop = data.pop("property")
            try:
                expense = services.record_expense(request.membership, prop, request=request, **data)
            except ValidationError as exc:
                _apply_errors(form, exc)
            except PermissionDenied:
                raise Http404 from None
            else:
                if expense.status == Status.APPROVED:
                    messages.success(request, _("Expense %(n)s recorded.") % {"n": expense.number})
                else:
                    messages.success(request, _("Expense %(n)s recorded. It is waiting for approval.")
                                     % {"n": expense.number})
                return redirect("expenses:detail", public_id=expense.public_id)
        if request.FILES:
            form.add_error(None, _("Choose the receipt again."))
        return render(request, self.template_name, {"form": form})


class ExpenseDetailView(CapabilityRequiredMixin, View):
    template_name = "expenses/detail.html"
    required_capability = "expenses.view"

    def get(self, request, public_id):
        expense = _get_expense(request, public_id)
        m = request.membership
        return render(request, self.template_name, {
            "expense": expense, "currency": m.organization.currency,
            "can_approve": services.can_approve(m, expense), "can_void": services.can_void(m, expense),
            "can_add_receipt": services.can_add_receipt(m, expense), "receipt_form": forms.ReceiptForm(),
            "can_suppliers": can(m, "contractors.manage"),
        })

    def post(self, request, public_id):
        expense = _get_expense(request, public_id)
        m = request.membership
        action = request.POST.get("action", "")
        try:
            if action == "approve":
                services.approve(m, expense, request=request)
                messages.success(request, _("Expense approved."))
            elif action == "reject":
                services.reject(m, expense, reason=request.POST.get("reason", ""), request=request)
                messages.success(request, _("Expense rejected."))
            elif action == "void":
                services.void(m, expense, reason=request.POST.get("reason", ""), request=request)
                messages.success(request, _("Expense voided."))
            elif action == "receipt":
                services.add_receipt(m, expense, request.FILES.get("receipt"), request=request)
                messages.success(request, _("Receipt added."))
        except ValidationError as exc:
            messages.error(request, _errors(exc))
        except PermissionDenied:
            raise Http404 from None
        if request.POST.get("next") == "list":
            return redirect(f"{reverse('expenses:list')}?status={Status.SUBMITTED}")
        return redirect("expenses:detail", public_id=expense.public_id)


class ReceiptView(CapabilityRequiredMixin, View):
    """Serves a receipt only to members who may see the expense. Never public."""

    required_capability = "expenses.view"

    def get(self, request, public_id):
        expense = get_object_or_404(services.visible_expenses(request.membership).exclude(receipt=""),
                                    public_id=public_id)
        try:
            handle = expense.receipt.open("rb")
        except FileNotFoundError:
            raise Http404 from None
        if expense.receipt_is_pdf:
            response = FileResponse(handle, content_type="application/pdf", as_attachment=True,
                                    filename=f"{expense.number}-receipt.pdf")
        else:
            response = FileResponse(handle, content_type="image/jpeg")
        response["Cache-Control"] = "private, max-age=3600"
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response


# ---------------------------------------------------------------------------
# Suppliers
# ---------------------------------------------------------------------------


class SupplierListView(CapabilityRequiredMixin, View):
    template_name = "expenses/suppliers.html"
    required_capability = "expenses.view"

    def get(self, request):
        m = request.membership
        show_archived = request.GET.get("archived") == "1"
        base = Supplier.all_objects if show_archived else Supplier.objects
        suppliers = list(base.for_org(m.organization).order_by("name"))
        paid = services.supplier_totals(m)
        for s in suppliers:
            s.paid_count, s.paid_total = paid.get(s.pk, (0, None))
        return render(request, self.template_name, {
            "suppliers": suppliers, "show_archived": show_archived, "currency": m.organization.currency,
            "can_manage": can(m, "contractors.manage")})


class SupplierFormView(CapabilityRequiredMixin, View):
    template_name = "expenses/supplier_form.html"
    required_capability = "contractors.manage"

    def supplier(self, request, kwargs) -> Supplier | None:
        if "public_id" not in kwargs:
            return None
        return get_object_or_404(Supplier.all_objects.for_org(request.membership.organization),
                                 public_id=kwargs["public_id"])

    def get(self, request, **kwargs):
        supplier = self.supplier(request, kwargs)
        return render(request, self.template_name, {"supplier": supplier,
                                                    "form": forms.SupplierForm(instance=supplier)})

    def post(self, request, **kwargs):
        supplier = self.supplier(request, kwargs)
        m = request.membership
        action = request.POST.get("action", "save")
        if supplier and action in ("archive", "restore"):
            getattr(services, f"{action}_supplier")(m, supplier, request=request)
            messages.success(request, _("Supplier archived.") if action == "archive" else _("Supplier restored."))
            return redirect("expenses:suppliers")
        form = forms.SupplierForm(request.POST, instance=supplier)
        if form.is_valid():
            fields = {k: form.cleaned_data[k] for k in services.SUPPLIER_FIELDS}
            try:
                if supplier:
                    services.update_supplier(m, supplier, request=request, **fields)
                else:
                    services.create_supplier(m, request=request, **fields)
            except ValidationError as exc:
                _apply_errors(form, exc)
            else:
                messages.success(request, _("Supplier saved."))
                return redirect("expenses:suppliers")
        return render(request, self.template_name, {"supplier": supplier, "form": form})


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


class CategoryView(CapabilityRequiredMixin, View):
    template_name = "expenses/categories.html"
    required_capability = "expenses.view"

    def render_page(self, request, form=None):
        m = request.membership
        services.ensure_categories(m.organization)
        cats = list(ExpenseCategory.all_objects.for_org(m.organization).order_by("archived_at", "name"))
        return render(request, self.template_name, {
            "categories": cats, "can_manage": services.can_manage_categories(m),
            "form": form or forms.CategoryForm()})

    def get(self, request):
        return self.render_page(request)

    def post(self, request):
        m = request.membership
        if not services.can_manage_categories(m):
            raise PermissionDenied("expenses.approve")
        action = request.POST.get("action", "")
        if action == "create":
            form = forms.CategoryForm(request.POST)
            if form.is_valid():
                try:
                    services.create_category(m, name=form.cleaned_data["name"], request=request)
                except ValidationError as exc:
                    _apply_errors(form, exc)
            if form.errors:
                return self.render_page(request, form)
            messages.success(request, _("Category added."))
            return redirect("expenses:categories")
        category = get_object_or_404(ExpenseCategory.all_objects.for_org(m.organization),
                                     public_id=request.POST.get("category") or None)
        try:
            if action == "rename":
                services.rename_category(m, category, name=request.POST.get("name", ""), request=request)
                messages.success(request, _("Category renamed."))
            elif action == "archive":
                services.archive_category(m, category, request=request)
                messages.success(request, _("Category archived."))
            elif action == "restore":
                services.restore_category(m, category, request=request)
                messages.success(request, _("Category restored."))
        except ValidationError as exc:
            messages.error(request, _errors(exc))
        return redirect("expenses:categories")
