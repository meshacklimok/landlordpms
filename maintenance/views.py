"""Maintenance pages (D-068). Thin views; services do the work.

Anything outside what the member may see is a 404, never a 403.
"""

import datetime
import json
import uuid

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Case, IntegerField, Prefetch, Value, When
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _
from django.views import View

from accounts.mixins import CapabilityRequiredMixin, OrgMemberRequiredMixin
from accounts.models import Membership
from accounts.permissions import can, visible_properties
from core import charts, exports
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


# D-074 item 4, for CSV: met, missed or still late; blank while pending or cancelled.
ON_TIME = {"met": "Yes", "missed": "No", "late": "No", "": ""}


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
            "late": g.get("late") == "1",
            "unit": None,
            "supplier": None,
            "staff": None,
        }
        if _is_uuid(g.get("unit", "")):
            f["unit"] = Unit.all_objects.filter(property__in=props, public_id=g["unit"]).first()
        if _is_uuid(g.get("supplier", "")):
            f["supplier"] = Supplier.all_objects.filter(organization=m.organization, public_id=g["supplier"]).first()
        if _is_uuid(g.get("staff", "")):
            member = (Membership.objects.filter(organization=m.organization, user__public_id=g["staff"])
                      .select_related("user").first())
            f["staff"] = member.user if member else None
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
        if f["late"]:
            qs = services.late_to_start(qs)
        if f["supplier"] is not None:
            qs = qs.filter(supplier=f["supplier"])
        if f["staff"] is not None:
            qs = qs.filter(assigned_to=f["staff"])
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
            "can_performance": can(m, "maintenance.view"),
            "late_to_start": services.late_to_start(services.visible_requests(m)).count(),
            "filtered": any(v for k, v in f.items() if k != "status") or f["status"] != "unfinished",
        })

    def csv(self, request, qs):
        m = request.membership
        response = _csv_response(f"maintenance-{timezone.localdate():%Y-%m-%d}.csv")
        writer = exports.writer(response)
        show_costs = can(m, "maintenance.costs") or can(m, "expenses.view")
        cost = services.cost_totals(qs) if show_costs else {}
        writer.writerow(["Number", "Reported", "Property", "Unit", "Title", "Kind", "Priority", "Status",
                         "Respond by", "Started", "Due by", "Assigned to", "Supplier", "Done", "Closed",
                         "Responded on time", "Fixed on time", *(["Approved cost"] if show_costs else [])])
        now = timezone.now()

        def when(moment, fmt="%Y-%m-%d %H:%M"):
            return timezone.localtime(moment).strftime(fmt) if moment else ""

        for r in qs.order_by("created_at", "pk"):
            writer.writerow([
                r.number, when(r.created_at), r.property.name,
                r.unit.code if r.unit else "", r.title, r.get_kind_display(), r.get_priority_display(),
                r.get_status_display(), when(r.respond_by), when(r.started_at), when(r.due_at),
                str(r.assigned_to) if r.assigned_to else "", r.supplier.name if r.supplier else "",
                when(r.done_at, "%Y-%m-%d"), when(r.closed_at, "%Y-%m-%d"),
                ON_TIME[r.response_state(now)], ON_TIME[r.fix_state(now)],
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
        return forms.RequestForm(data, files, initial=initial, properties=props,
                                 targets=services.targets(request.membership.organization))

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


class OfflineReportView(CapabilityRequiredMixin, View):
    """A report kept on the phone while offline, sent as JSON once back online (D-090 item 3).

    2xx: recorded (or recorded before). 422: refused, with reasons per field. Anything else: the phone tries later.
    """

    required_capability = "maintenance.create"
    MAX_BODY = 16 * 1024
    FIELDS = ("title", "description", "kind", "priority")

    def get(self, request):
        return JsonResponse({"error": "POST a report."}, status=405)

    def refused(self, errors):
        return JsonResponse({"status": "refused", "errors": errors,
                             "message": _("Nothing was saved. Fix the report and send it again.")}, status=422)

    def post(self, request):
        if len(request.body) > self.MAX_BODY:
            return JsonResponse({"error": "Too long."}, status=413)
        try:
            data = json.loads(request.body or b"{}")
        except ValueError:
            return JsonResponse({"error": "The body is not valid JSON."}, status=400)
        key = data.get("id") if isinstance(data, dict) else None
        fields = data.get("fields") if isinstance(data, dict) else None
        if not isinstance(key, str) or not 8 <= len(key) <= 64 or not isinstance(fields, dict) \
                or not all(isinstance(fields.get(f, ""), str) for f in (*self.FIELDS, "property", "unit")):
            return JsonResponse({"error": "Send id and fields."}, status=400)
        typed_at = parse_datetime(data["typed_at"]) if isinstance(data.get("typed_at"), str) else None
        if typed_at is not None and timezone.is_naive(typed_at):
            typed_at = None
        props = {str(p.public_id): p for p in services.reportable_properties(request.membership)}
        prop = props.get(fields.get("property", ""))
        if prop is None:
            return self.refused({"property": [_("You cannot report problems at this property.")]})
        unit = None
        if fields.get("unit"):
            if _is_uuid(fields["unit"]):
                unit = Unit.objects.filter(property=prop, public_id=fields["unit"]).first()
            if unit is None:
                return self.refused({"unit": [_("Choose a unit of this property.")]})
        try:
            req, made = services.report_offline(
                request.membership, prop, key=key, typed_at=typed_at, request=request, unit=unit,
                from_tenant=bool(fields.get("from_tenant")),
                **{f: fields[f] for f in self.FIELDS if f in fields})
        except ValidationError as exc:
            return self.refused(exc.message_dict if hasattr(exc, "error_dict") else {"": exc.messages})
        except PermissionDenied:
            return self.refused({"property": [_("You cannot report problems at this property.")]})
        body = {"status": "recorded" if made else "already", "number": req.number,
                "url": reverse("maintenance:detail", args=[req.public_id])}
        return JsonResponse(body, status=201 if made else 200)


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
            "details_open": "details_form" in forms_,
        }
        if services.can_edit_details(m, req):
            ctx["details_form"] = forms_.get("details_form") or forms.DetailsForm(initial={
                "title": req.title, "description": req.description, "kind": req.kind})
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
            elif action == "details":
                form = forms.DetailsForm(request.POST)
                if not form.is_valid():
                    return render(request, self.template_name, self.context(request, req, details_form=form))
                services.edit_details(m, req, request=request, **form.cleaned_data)
                messages.success(request, _("Saved."))
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


# ---------------------------------------------------------------------------
# Targets and performance (D-074)
# ---------------------------------------------------------------------------


class TargetsView(CapabilityRequiredMixin, View):
    template_name = "maintenance/targets.html"
    required_capability = "organization.manage"

    def render(self, request, form):
        return render(request, self.template_name, {"form": form})

    def get(self, request):
        return self.render(request, forms.TargetsForm(targets=services.targets(request.organization)))

    def post(self, request):
        form = forms.TargetsForm(request.POST, targets=services.targets(request.organization))
        if form.is_valid():
            try:
                services.save_targets(request.membership, form.hours(), request=request)
            except ValidationError as exc:
                for priority, errors in exc.message_dict.items():
                    form.add_error(f"{priority}_fix" if priority in MaintenanceRequest.Priority.values
                                   else None, errors)
            else:
                messages.success(request, _("Maintenance targets saved. They apply to new requests."))
                return redirect("maintenance:targets")
        return self.render(request, form)


class PerformanceView(CapabilityRequiredMixin, View):
    """Who keeps to the targets: one row per supplier and per staff member (D-074 item 5)."""

    template_name = "maintenance/performance.html"
    required_capability = "maintenance.view"
    PERIODS = (30, 90, 365)

    def get(self, request):
        m = request.membership
        g = request.GET
        props = list(visible_properties(m, Property.all_objects.all()).order_by("name"))
        prop = next((p for p in props if str(p.public_id) == g.get("property")), None)
        days = int(g["days"]) if g.get("days") in {str(d) for d in self.PERIODS} else 90
        now = timezone.now()
        qs = services.visible_requests(m).filter(created_at__gte=now - datetime.timedelta(days=days))
        if prop is not None:
            qs = qs.filter(property=prop)
        with_costs = can(m, "maintenance.costs") or can(m, "expenses.view")
        suppliers = services.performance(qs, "supplier", now=now, with_costs=with_costs)
        staff = services.performance(qs, "assigned_to", now=now, with_costs=with_costs)
        # D-110: the same measures by kind of job, priority and property, and for all jobs together.
        kinds = services.performance(qs, "kind", now=now, with_costs=with_costs)
        priorities = services.performance(qs, "priority", now=now, with_costs=with_costs)
        places = services.performance(qs, "property", now=now, with_costs=with_costs) if prop is None else []
        total = services.overall(qs, now=now, with_costs=with_costs)
        if g.get("format") == "csv":
            if not can(m, "reports.export"):
                raise PermissionDenied("reports.export")
            return self.csv(days, [("All", [total]), ("Supplier", suppliers), ("Staff", staff), ("Kind", kinds),
                                   ("Priority", priorities), ("Property", places)], with_costs)
        query = request.GET.copy()
        query["format"] = "csv"
        # D-140: jobs by kind, and how well each priority keeps to its targets; the tables below say the same.
        specs = {}
        if total.jobs:
            specs["kinds"] = charts.doughnut(_("Jobs"), charts.fold([(r.name, r.jobs) for r in kinds], _("Other")),
                                             charts.SERIES, axis="count")
            targets = [r for r in priorities if r.respond_on_time is not None or r.fix_on_time is not None]
            if targets:
                specs["priorities"] = charts.chart("bar", [r.name for r in targets], [
                    charts.series(_("Started on time"), [r.respond_on_time for r in targets], "1-soft"),
                    charts.series(_("Fixed on time"), [r.fix_on_time for r in targets], "1"),
                ], axis="pct")
        return render(request, self.template_name, {
            "specs": specs, "suppliers": suppliers, "staff": staff, "properties": props, "property": prop, "days": days,
            "kinds": kinds, "priorities": priorities, "places": places, "total": total,
            "periods": self.PERIODS, "with_costs": with_costs, "currency": m.organization.currency,
            "targets": self.targets(m.organization), "can_targets": can(m, "organization.manage"),
            "can_export": can(m, "reports.export"), "csv_query": query.urlencode(),
            "unassigned": qs.exclude(status=Status.CANCELLED).filter(assigned_to__isnull=True,
                                                                     supplier__isnull=True).count(),
        })

    @staticmethod
    def targets(org):
        targets = services.targets(org)
        return [(label, forms.duration(targets[value].respond), forms.duration(targets[value].fix))
                for value, label in MaintenanceRequest.Priority.choices]

    def csv(self, days, groups, with_costs):
        response = _csv_response(f"maintenance-performance-{days}d-{timezone.localdate():%Y-%m-%d}.csv")
        writer = exports.writer(response)
        writer.writerow(["Type", "Name", "Jobs", "Finished", "Responded on time %", "Fixed on time %",
                         "Average hours to respond", "Average days to fix", "Open", "Overdue", "Reopened",
                         "Due time moved", *(["Approved cost", "Cost per job"] if with_costs else [])])
        for kind, rows in groups:
            for r in rows:
                writer.writerow([kind, r.name, r.jobs, r.finished, _blank(r.respond_on_time), _blank(r.fix_on_time),
                                 _blank(r.avg_hours_to_respond), _blank(r.avg_days_to_fix), r.open, r.overdue,
                                 r.reopened, r.due_moved, *([r.cost, _blank(r.cost_per_job)] if with_costs else [])])
        return response


def _blank(value):
    return "" if value is None else value


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
