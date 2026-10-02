"""Maintenance pages (D-068). Thin views; services do the work.

Anything outside what the member may see is a 404, never a 403.
"""

import csv
import uuid

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Case, IntegerField, Prefetch, Value, When
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View

from accounts.mixins import CapabilityRequiredMixin, OrgMemberRequiredMixin
from accounts.permissions import can, visible_properties
from expenses import services as expense_services
from expenses.models import Expense, Supplier
from properties.models import Property, Unit
from properties.views import _apply_errors
from reports.views import _csv_response

from . import forms, services
from .models import MaintenancePhoto, MaintenanceRequest

Status = MaintenanceRequest.Status
PAGE_SIZE = 50
# Open first (worst first), then Done waiting to be checked, then finished.
STATUS_ORDER = Case(
    *(When(status=s, then=Value(i)) for i, s in enumerate(
        (Status.NEW, Status.ASSIGNED, Status.IN_PROGRESS, Status.ON_HOLD, Status.DONE, Status.CLOSED,
         Status.CANCELLED))),
    output_field=IntegerField())
PRIORITY_ORDER = Case(
    *(When(priority=p, then=Value(i)) for i, p in enumerate(MaintenanceRequest.Priority.values)),
    output_field=IntegerField())


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return True


def _errors(exc: ValidationError) -> str:
    return " ".join(exc.messages)


class MaintenanceAccessMixin(OrgMemberRequiredMixin):
    """`maintenance.view` or `maintenance.view_assigned` (D-068 item 10)."""

    def dispatch(self, request, *args, **kwargs):
        m = getattr(request, "membership", None)
        if (request.user.is_authenticated and request.user.phone_verified and m is not None
                and not (can(m, "maintenance.view") or can(m, "maintenance.view_assigned"))):
            raise PermissionDenied("maintenance.view")
        return super().dispatch(request, *args, **kwargs)


def _get_request(request, public_id) -> MaintenanceRequest:
    qs = services.visible_requests(request.membership).select_related(
        "property", "unit", "tenant", "lease", "organization", "reported_by", "assigned_to", "supplier", "closed_by")
    return get_object_or_404(qs, public_id=public_id)


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------


class RequestListView(MaintenanceAccessMixin, View):
    template_name = "maintenance/list.html"
    STATES = {"open": MaintenanceRequest.OPEN, "unfinished": MaintenanceRequest.UNFINISHED}

    def filters(self, request):
        m = request.membership
        g = request.GET
        props = list(visible_properties(m, Property.all_objects.all()).order_by("name"))
        state = g.get("status", "unfinished")
        f = {
            "property": next((p for p in props if str(p.public_id) == g.get("property")), None),
            "status": state if state in (*self.STATES, *Status.values, "all") else "unfinished",
            "priority": g.get("priority") if g.get("priority") in MaintenanceRequest.Priority.values else "",
            "kind": g.get("kind") if g.get("kind") in MaintenanceRequest.Kind.values else "",
            "mine": g.get("mine") == "1",
            "overdue": g.get("overdue") == "1",
            "unit": None,
        }
        if _is_uuid(g.get("unit", "")):
            f["unit"] = Unit.all_objects.filter(property__in=props, public_id=g["unit"]).first()
        return f, props

    def queryset(self, request, f):
        qs = services.visible_requests(request.membership)
        if f["property"] is not None:
            qs = qs.filter(property=f["property"])
        if f["unit"] is not None:
            qs = qs.filter(unit=f["unit"])
        if f["status"] in self.STATES:
            qs = qs.filter(status__in=self.STATES[f["status"]])
        elif f["status"] in Status.values:
            qs = qs.filter(status=f["status"])
        if f["priority"]:
            qs = qs.filter(priority=f["priority"])
        if f["kind"]:
            qs = qs.filter(kind=f["kind"])
        if f["mine"]:
            qs = qs.filter(assigned_to=request.user)
        if f["overdue"]:
            qs = qs.filter(status__in=MaintenanceRequest.OPEN, due_at__lt=timezone.now())
        return qs

    def get(self, request):
        m = request.membership
        f, props = self.filters(request)
        qs = self.queryset(request, f).select_related("property", "unit", "assigned_to", "supplier")
        if request.GET.get("format") == "csv":
            if not can(m, "reports.export"):
                raise PermissionDenied("reports.export")
            return self.csv(request, qs)
        ordered = qs.annotate(so=STATUS_ORDER, po=PRIORITY_ORDER).order_by("so", "po", "due_at", "pk")
        page = Paginator(ordered, PAGE_SIZE).get_page(request.GET.get("page"))
        query = request.GET.copy()
        query.pop("page", None)
        query["format"] = "csv"
        return render(request, self.template_name, {
            "page": page, "f": f, "properties": props, "now": timezone.now(),
            "statuses": Status.choices, "priorities": MaintenanceRequest.Priority.choices,
            "kinds": MaintenanceRequest.Kind.choices, "summary": services.summary(services.visible_requests(m)),
            "can_report": bool(services.reportable_properties(m)), "to_assign": services.to_assign(m).count(),
            "can_export": can(m, "reports.export"), "csv_query": query.urlencode(),
            "filtered": any(v for k, v in f.items() if k != "status") or f["status"] != "unfinished",
        })

    def csv(self, request, qs):
        m = request.membership
        response = _csv_response(f"maintenance-{timezone.localdate():%Y-%m-%d}.csv")
        writer = csv.writer(response)
        show_costs = can(m, "maintenance.costs") or can(m, "expenses.view")
        cost = services.cost_totals(qs) if show_costs else {}
        writer.writerow(["Number", "Reported", "Property", "Unit", "Title", "Kind", "Priority", "Status", "Due by",
                         "Assigned to", "Supplier", "Done", "Closed", *(["Approved cost"] if show_costs else [])])
        for r in qs.order_by("created_at", "pk"):
            writer.writerow([
                r.number, timezone.localtime(r.created_at).strftime("%Y-%m-%d %H:%M"), r.property.name,
                r.unit.code if r.unit else "", r.title, r.get_kind_display(), r.get_priority_display(),
                r.get_status_display(), timezone.localtime(r.due_at).strftime("%Y-%m-%d %H:%M"),
                str(r.assigned_to) if r.assigned_to else "", r.supplier.name if r.supplier else "",
                timezone.localtime(r.done_at).strftime("%Y-%m-%d") if r.done_at else "",
                timezone.localtime(r.closed_at).strftime("%Y-%m-%d") if r.closed_at else "",
                *([cost.get(r.pk, "")] if show_costs else [])])
        return response


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


class RequestCreateView(CapabilityRequiredMixin, View):
    template_name = "maintenance/form.html"
    required_capability = "maintenance.create"

    def form(self, request, data=None, files=None, initial=None):
        ids = [p.pk for p in services.reportable_properties(request.membership)]
        props = (Property.objects.filter(pk__in=ids).order_by("name")
                 .prefetch_related(Prefetch("units", Unit.objects.order_by("code"))))
        return forms.RequestForm(data, files, initial=initial, properties=props)

    def get(self, request):
        initial = {}
        props = services.reportable_properties(request.membership)
        if len(props) == 1:
            initial["property"] = str(props[0].public_id)
        unit = request.GET.get("unit")
        if unit and _is_uuid(unit):
            u = Unit.objects.filter(property__in=props, public_id=unit).select_related("property").first()
            if u is not None:
                initial.update(property=str(u.property.public_id), unit=str(u.public_id))
        return render(request, self.template_name, {"form": self.form(request, initial=initial)})

    def post(self, request):
        form = self.form(request, request.POST, request.FILES)
        if form.is_valid():
            data = dict(form.cleaned_data)
            prop = data.pop("property")
            unit_id = data.pop("unit")
            data["unit"] = Unit.objects.filter(property=prop, public_id=unit_id).first() if unit_id else None
            if unit_id and data["unit"] is None:
                form.add_error("unit", _("Choose a unit of this property."))
            else:
                try:
                    req = services.report(request.membership, prop, request=request, **data)
                except ValidationError as exc:
                    _apply_errors(form, exc)
                except PermissionDenied:
                    raise Http404 from None
                else:
                    messages.success(request, _("Request %(n)s reported.") % {"n": req.number})
                    return redirect("maintenance:detail", public_id=req.public_id)
        if request.FILES:
            form.add_error(None, _("Choose the photos again."))
        return render(request, self.template_name, {"form": form})


# ---------------------------------------------------------------------------
# One request
# ---------------------------------------------------------------------------


class RequestDetailView(MaintenanceAccessMixin, View):
    template_name = "maintenance/detail.html"

    def context(self, request, req, **forms_):
        m = request.membership
        can_assign = services.can_assign(m, req)
        ctx = {
            "req": req, "now": timezone.now(), "currency": m.organization.currency,
            "updates": req.updates.select_related("by").all(), "photos": list(req.photos.all()),
            "actions": services.allowed_actions(m, req), "can_update": services.can_update(m, req),
            "can_assign": can_assign, "can_see_costs": services.can_see_costs(m, req),
            "can_record_cost": services.can_record_cost(m, req),
            "can_see_tenant": can(m, "tenants.view", req.property),
            "can_see_expenses": can(m, "expenses.view", req.property),
            "note_form": forms_.get("note_form") or forms.NoteForm(),
        }
        if ctx["can_see_costs"]:
            ctx["costs"] = services.costs(req)
        if can_assign:
            ctx["assign_form"] = forms_.get("assign_form") or forms.AssignForm(
                initial={"assigned_to": str(req.assigned_to.public_id) if req.assigned_to else "",
                         "supplier": str(req.supplier.public_id) if req.supplier else "", "sms_supplier": True},
                users=services.assignable_users(req), suppliers=Supplier.objects.for_org(m.organization))
            ctx["priority_form"] = forms_.get("priority_form") or forms.PriorityForm(initial={
                "priority": req.priority, "due_at": timezone.localtime(req.due_at)})
        return ctx

    def get(self, request, public_id):
        req = _get_request(request, public_id)
        return render(request, self.template_name, self.context(request, req))

    def post(self, request, public_id):
        req = _get_request(request, public_id)
        m = request.membership
        action = request.POST.get("action", "")
        try:
            if action in services.ACTIONS:
                services.act(m, req, action, note=request.POST.get("note", ""), request=request)
                messages.success(request, _("Request updated."))
            elif action == "assign":
                form = forms.AssignForm(request.POST, users=services.assignable_users(req),
                                        suppliers=Supplier.objects.for_org(m.organization))
                if not form.is_valid():
                    return render(request, self.template_name, self.context(request, req, assign_form=form))
                services.assign(m, req, user=form.cleaned_data["assigned_to"], supplier=form.cleaned_data["supplier"],
                                sms_supplier=form.cleaned_data["sms_supplier"], request=request)
                messages.success(request, _("Assignment saved."))
            elif action == "priority":
                form = forms.PriorityForm(request.POST)
                if not form.is_valid():
                    return render(request, self.template_name, self.context(request, req, priority_form=form))
                due = form.cleaned_data["due_at"]
                # The form shows the current due time; unchanged means "from the priority".
                if due is not None and abs((due - req.due_at).total_seconds()) < 60:
                    due = None
                services.set_priority(m, req, priority=form.cleaned_data["priority"], due_at=due, request=request)
                messages.success(request, _("Priority saved."))
            elif action == "note":
                form = forms.NoteForm(request.POST, request.FILES)
                if not form.is_valid():
                    return render(request, self.template_name, self.context(request, req, note_form=form))
                services.add_note(m, req, text=form.cleaned_data["text"], share=form.cleaned_data["share"],
                                  photos=form.cleaned_data["photos"], request=request)
                messages.success(request, _("Saved."))
        except ValidationError as exc:
            messages.error(request, _errors(exc))
        except PermissionDenied:
            raise Http404 from None
        return redirect("maintenance:detail", public_id=req.public_id)


class CostView(MaintenanceAccessMixin, View):
    """Records an expense for the repair (D-068 item 6)."""

    template_name = "maintenance/cost.html"

    def load(self, request, public_id):
        req = _get_request(request, public_id)
        if not services.can_record_cost(request.membership, req):
            raise Http404
        return req

    def form(self, request, req, data=None, files=None, initial=None):
        org = request.membership.organization
        return forms.CostForm(data, files, initial=initial, categories=expense_services.categories(org),
                              suppliers=Supplier.objects.for_org(org).order_by("name"))

    def get(self, request, public_id):
        req = self.load(request, public_id)
        org = request.membership.organization
        repairs = expense_services.categories(org).filter(name="Repairs and maintenance").first()
        initial = {"paid_on": timezone.localdate().isoformat(), "description": f"{req.number}: {req.title}"[:200],
                   "supplier": str(req.supplier.public_id) if req.supplier and not req.supplier.is_archived else "",
                   "category": str(repairs.public_id) if repairs else ""}
        return render(request, self.template_name, {"req": req, "form": self.form(request, req, initial=initial)})

    def post(self, request, public_id):
        req = self.load(request, public_id)
        form = self.form(request, req, request.POST, request.FILES)
        if form.is_valid():
            try:
                expense = services.record_cost(request.membership, req, request=request, **form.cleaned_data)
            except ValidationError as exc:
                _apply_errors(form, exc)
            except PermissionDenied:
                raise Http404 from None
            else:
                if expense.status == Expense.Status.APPROVED:
                    messages.success(request, _("Cost %(n)s recorded.") % {"n": expense.number})
                else:
                    messages.success(request, _("Cost %(n)s recorded. It is waiting for approval.")
                                     % {"n": expense.number})
                return redirect("maintenance:detail", public_id=req.public_id)
        if request.FILES:
            form.add_error(None, _("Choose the receipt again."))
        return render(request, self.template_name, {"req": req, "form": form})


class PhotoView(MaintenanceAccessMixin, View):
    """Serves a photo only to members who may see its request. Never public."""

    def get(self, request, public_id):
        photo = get_object_or_404(
            MaintenancePhoto.objects.filter(request__in=services.visible_requests(request.membership)),
            public_id=public_id)
        return photo_response(photo)


def photo_response(photo: MaintenancePhoto):
    try:
        handle = photo.image.open("rb")
    except FileNotFoundError:
        raise Http404 from None
    response = FileResponse(handle, content_type="image/jpeg")
    response["Cache-Control"] = "private, max-age=3600"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
