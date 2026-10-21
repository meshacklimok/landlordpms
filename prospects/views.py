"""Prospect pages (D-072). Thin views; services do the work.

Anything outside what the member may see is a 404, never a 403.
"""

import uuid

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Case, IntegerField, Prefetch, Q, Value, When
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View

from accounts.mixins import CapabilityRequiredMixin
from accounts.permissions import can, visible_properties
from core import charts
from core.phone import InvalidPhoneNumber, normalize_phone
from properties.models import Property, Unit
from properties.selectors import is_available_to_let
from properties.views import _apply_errors
from tenants import services as tenant_services

from . import forms, services
from .models import Prospect, Viewing

Status = Prospect.Status
PAGE_SIZE = 50
STATUS_ORDER = Case(*(When(status=s, then=Value(i)) for i, s in enumerate(Status.values)),
                    output_field=IntegerField())


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return True


def _iso(day) -> str:
    """Date inputs need ISO dates; the form partial prints the initial value as it is."""
    return day.isoformat() if day else ""


def _get_prospect(request, public_id) -> Prospect:
    qs = services.visible_prospects(request.membership).select_related(
        "property", "unit", "organization", "assigned_to", "held_unit", "tenant", "created_by")
    return get_object_or_404(qs, public_id=public_id)


def _form_properties(membership):
    ids = [p.pk for p in services.manageable_properties(membership)]
    return (Property.objects.filter(pk__in=ids).order_by("name")
            .prefetch_related(Prefetch("units", Unit.objects.order_by("code"))))


def _unit_of(prop, public_id):
    if not public_id or not _is_uuid(public_id):
        return None
    return Unit.objects.filter(property=prop, public_id=public_id).first()


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------


class ProspectListView(CapabilityRequiredMixin, View):
    template_name = "prospects/list.html"
    required_capability = "prospects.view"

    def filters(self, request):
        m = request.membership
        g = request.GET
        props = list(visible_properties(m, Property.all_objects.all()).order_by("name"))
        state = g.get("status", "open")
        f = {
            "property": next((p for p in props if str(p.public_id) == g.get("property")), None),
            "status": state if state in ("open", "all", *Status.values) else "open",
            "mine": g.get("mine") == "1",
            "due": g.get("due") == "1",
            "lapsed": g.get("lapsed") == "1",
            "q": (g.get("q") or "").strip()[:100],
            "unit": None,
        }
        if _is_uuid(g.get("unit", "")):
            f["unit"] = Unit.all_objects.filter(property__in=props, public_id=g["unit"]).first()
        return f, props

    def queryset(self, request, f):
        qs = services.visible_prospects(request.membership)
        if f["property"] is not None:
            qs = qs.filter(property=f["property"])
        if f["unit"] is not None:
            qs = qs.filter(Q(unit=f["unit"]) | Q(held_unit=f["unit"]) | Q(viewings__unit=f["unit"])).distinct()
        if f["status"] == "open":
            qs = qs.filter(status__in=Prospect.OPEN)
        elif f["status"] in Status.values:
            qs = qs.filter(status=f["status"])
        if f["mine"]:
            qs = qs.filter(assigned_to=request.user)
        if f["due"]:
            qs = qs.filter(status__in=Prospect.OPEN).filter(
                Q(status=Status.NEW) | Q(next_contact_on__lte=timezone.localdate()))
        if f["lapsed"]:
            qs = qs.filter(held_unit__isnull=False, held_until__lt=timezone.localdate())
        if f["q"]:
            digits = "".join(c for c in f["q"] if c.isdigit())
            match = Q(name__icontains=f["q"])
            if len(digits) >= 3:
                match |= Q(phone__contains=digits[1:] if digits.startswith("0") else digits)
            qs = qs.filter(match)
        return qs

    def get(self, request):
        m = request.membership
        f, props = self.filters(request)
        qs = (self.queryset(request, f).select_related("property", "unit", "assigned_to", "held_unit")
              .annotate(so=STATUS_ORDER).order_by("so", "next_contact_on", "-created_at", "pk"))
        page = Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))
        visible = services.visible_prospects(m)
        flow = services.pipeline(visible)
        specs = {}
        if any(n for _label, n in flow["stages"]):
            specs["stages"] = charts.chart(
                "bar", [label for label, _n in flow["stages"]],
                [charts.series(_("Open prospects"), [n for _label, n in flow["stages"]], "1")],
                axis="count", horizontal=True)
        if flow["sources"]:
            specs["sources"] = charts.doughnut(_("Enquiries"), charts.fold(flow["sources"], _("Other")),
                                               charts.SERIES, axis="count")
        if flow["lost"]:
            specs["lost"] = charts.chart("bar", [label for label, _n in flow["lost"]],
                                         [charts.series(_("Lost"), [n for _label, n in flow["lost"]], "late-3")],
                                         axis="count", horizontal=True)
        return render(request, self.template_name, {
            "flow": flow, "specs": specs,
            "page": page, "f": f, "properties": props, "today": timezone.localdate(),
            "statuses": Status.choices, "summary": services.summary(visible),
            "viewings": list(services.upcoming_viewings(m)[:20]),
            "lapsed": services.lapsed_holds(m).count(),
            "can_add": bool(services.manageable_properties(m)),
            "filtered": any(v for k, v in f.items() if k != "status") or f["status"] != "open",
        })


# ---------------------------------------------------------------------------
# Adding and editing
# ---------------------------------------------------------------------------


class ProspectCreateView(CapabilityRequiredMixin, View):
    template_name = "prospects/form.html"
    required_capability = "prospects.manage"

    def form(self, request, data=None, initial=None):
        m = request.membership
        return forms.ProspectForm(data, initial=initial, properties=_form_properties(m),
                                  users=services.prospect_staff(m.organization))

    def get(self, request):
        m = request.membership
        props = services.manageable_properties(m)
        initial = {"assigned_to": str(request.user.public_id)}
        if len(props) == 1:
            initial["property"] = str(props[0].public_id)
        unit = request.GET.get("unit")
        if unit and _is_uuid(unit):
            u = Unit.objects.filter(property__in=props, public_id=unit).select_related("property").first()
            if u is not None:
                initial.update(property=str(u.property.public_id), unit=str(u.public_id))
        if request.GET.get("source") in Prospect.Source.values:
            initial["source"] = request.GET["source"]
        return render(request, self.template_name, {"form": self.form(request, initial=initial)})

    def post(self, request):
        m = request.membership
        form = self.form(request, request.POST)
        duplicates = []
        if form.is_valid():
            data = dict(form.cleaned_data)
            prop = data.pop("property")
            unit_id = data.pop("unit")
            confirmed = data.pop("confirm_duplicate")
            data["unit"] = _unit_of(prop, unit_id)
            if unit_id and data["unit"] is None:
                form.add_error("unit", _("Choose a unit of this property."))
            else:
                try:
                    phone = normalize_phone(data["phone"])
                except InvalidPhoneNumber:
                    phone = None
                if phone and not confirmed:
                    duplicates = list(services.open_with_phone(m, phone, prop).select_related("assigned_to"))
                if not duplicates:
                    try:
                        p = services.create_prospect(m, prop, request=request, **data)
                    except ValidationError as exc:
                        _apply_errors(form, exc)
                    except PermissionDenied:
                        raise Http404 from None
                    else:
                        messages.success(request, _("Prospect %(name)s added.") % {"name": p.name})
                        return redirect("prospects:detail", public_id=p.public_id)
        return render(request, self.template_name, {"form": form, "duplicates": duplicates})


class ProspectEditView(CapabilityRequiredMixin, View):
    template_name = "prospects/form.html"
    required_capability = "prospects.manage"

    def load(self, request, public_id):
        p = _get_prospect(request, public_id)
        if not services.can_manage(request.membership, p):
            raise Http404
        return p

    def form(self, request, p, data=None, initial=None):
        props = (Property.objects.filter(pk=p.property_id)
                 .prefetch_related(Prefetch("units", Unit.objects.order_by("code"))))
        return forms.ProspectForm(data, initial=initial, properties=props, users=[], editing=True)

    def get(self, request, public_id):
        p = self.load(request, public_id)
        initial = {"name": p.name, "phone": p.phone, "email": p.email, "source": p.source, "budget": p.budget,
                   "move_in_by": _iso(p.move_in_by), "wants": p.wants, "next_contact_on": _iso(p.next_contact_on),
                   "unit": str(p.unit.public_id) if p.unit_id else ""}
        return render(request, self.template_name, {"form": self.form(request, p, initial=initial), "prospect": p})

    def post(self, request, public_id):
        p = self.load(request, public_id)
        form = self.form(request, p, request.POST)
        if form.is_valid():
            data = dict(form.cleaned_data)
            unit_id = data.pop("unit")
            data["unit"] = _unit_of(p.property, unit_id)
            if unit_id and data["unit"] is None:
                form.add_error("unit", _("Choose a unit of this property."))
            else:
                try:
                    services.update_prospect(request.membership, p, request=request, **data)
                except ValidationError as exc:
                    _apply_errors(form, exc)
                except PermissionDenied:
                    raise Http404 from None
                else:
                    messages.success(request, _("Saved."))
                    return redirect("prospects:detail", public_id=p.public_id)
        return render(request, self.template_name, {"form": form, "prospect": p})


# ---------------------------------------------------------------------------
# One prospect
# ---------------------------------------------------------------------------


class ProspectDetailView(CapabilityRequiredMixin, View):
    template_name = "prospects/detail.html"
    required_capability = "prospects.view"

    @staticmethod
    def viewing_units(p):
        return Unit.objects.filter(property=p.property_id).order_by("code")

    @staticmethod
    def hold_units(p):
        units = [u for u in Unit.objects.filter(property=p.property_id).order_by("code")
                 if u.pk == p.held_unit_id or is_available_to_let(u)]
        return Unit.objects.filter(pk__in=[u.pk for u in units]).order_by("code")

    def context(self, request, p, **forms_):
        m = request.membership
        manage = services.can_manage(m, p)
        ctx = {
            "p": p, "today": timezone.localdate(), "now": timezone.now(),
            "notes": p.notes.select_related("by").all(),
            "viewings": p.viewings.select_related("unit", "with_user").all(),
            "can_manage": manage, "can_convert": services.can_convert(m, p),
            "can_see_tenant": p.tenant_id is not None and can(m, "tenants.view"),
            "duplicates": list(services.open_with_phone(m, p.phone, exclude_pk=p.pk).select_related("property")),
            "outcome_form": forms.OutcomeForm(),
        }
        if manage:
            users = services.assignable_users(p.property)
            default_unit = p.held_unit or p.unit
            ctx["note_form"] = forms_.get("note_form") or forms.NoteForm(initial={
                "next_contact_on": _iso(p.next_contact_on)})
            ctx["lose_form"] = forms_.get("lose_form") or forms.LoseForm()
            if p.is_open:
                ctx["viewing_form"] = forms_.get("viewing_form") or forms.ViewingForm(
                    initial={"unit": str(default_unit.public_id) if default_unit else "",
                             "with_user": str(p.assigned_to.public_id) if p.assigned_to_id else ""},
                    units=self.viewing_units(p), users=users)
                ctx["hold_form"] = forms_.get("hold_form") or forms.HoldForm(
                    initial={"unit": str(default_unit.public_id) if default_unit else "",
                             "until": _iso(p.held_until)},
                    units=self.hold_units(p))
                ctx["assign_form"] = forms_.get("assign_form") or forms.AssignForm(
                    initial={"assigned_to": str(p.assigned_to.public_id) if p.assigned_to_id else ""}, users=users)
        return ctx

    def get(self, request, public_id):
        p = _get_prospect(request, public_id)
        return render(request, self.template_name, self.context(request, p))

    def post(self, request, public_id):
        p = _get_prospect(request, public_id)
        m = request.membership
        if not services.can_manage(m, p):
            raise Http404
        action = request.POST.get("action", "")
        try:
            if action == "note":
                form = forms.NoteForm(request.POST)
                if not form.is_valid():
                    return render(request, self.template_name, self.context(request, p, note_form=form))
                services.add_note(m, p, request=request, **form.cleaned_data)
                messages.success(request, _("Saved."))
            elif action == "viewing":
                form = forms.ViewingForm(request.POST, units=self.viewing_units(p),
                                         users=services.assignable_users(p.property))
                if not form.is_valid():
                    return render(request, self.template_name, self.context(request, p, viewing_form=form))
                try:
                    services.book_viewing(m, p, request=request, **form.cleaned_data)
                except ValidationError as exc:
                    _apply_errors(form, exc)
                    return render(request, self.template_name, self.context(request, p, viewing_form=form))
                messages.success(request, _("Viewing booked."))
            elif action == "outcome":
                viewing = get_object_or_404(Viewing, prospect=p, public_id=request.POST.get("viewing")
                                            if _is_uuid(request.POST.get("viewing", "")) else None)
                form = forms.OutcomeForm(request.POST)
                if not form.is_valid():
                    raise ValidationError(_("Choose what happened."))
                services.viewing_outcome(m, viewing, form.cleaned_data["status"],
                                         outcome=form.cleaned_data["outcome"], request=request)
                messages.success(request, _("Viewing updated."))
            elif action == "hold":
                form = forms.HoldForm(request.POST, units=self.hold_units(p))
                if not form.is_valid():
                    return render(request, self.template_name, self.context(request, p, hold_form=form))
                try:
                    services.hold_unit(m, p, request=request, **form.cleaned_data)
                except ValidationError as exc:
                    _apply_errors(form, exc)
                    return render(request, self.template_name, self.context(request, p, hold_form=form))
                messages.success(request, _("Unit held. It shows as Reserved."))
            elif action == "release":
                services.release_unit(m, p, note=request.POST.get("note", ""), request=request)
                messages.success(request, _("Hold released."))
            elif action == "assign":
                form = forms.AssignForm(request.POST, users=services.assignable_users(p.property))
                if not form.is_valid():
                    return render(request, self.template_name, self.context(request, p, assign_form=form))
                services.assign(m, p, form.cleaned_data["assigned_to"], request=request)
                messages.success(request, _("Assignment saved."))
            elif action == "lose":
                form = forms.LoseForm(request.POST)
                if not form.is_valid():
                    return render(request, self.template_name, self.context(request, p, lose_form=form))
                try:
                    services.lose(m, p, request=request, **form.cleaned_data)
                except ValidationError as exc:
                    _apply_errors(form, exc)
                    return render(request, self.template_name, self.context(request, p, lose_form=form))
                messages.success(request, _("Marked as lost."))
            elif action == "reopen":
                services.reopen(m, p, request=request)
                messages.success(request, _("Prospect reopened."))
            else:
                messages.error(request, _("Unknown action."))
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        except PermissionDenied:
            raise Http404 from None
        return redirect("prospects:detail", public_id=p.public_id)


# ---------------------------------------------------------------------------
# Becoming a tenant
# ---------------------------------------------------------------------------


class ConvertView(CapabilityRequiredMixin, View):
    """Creates the tenant (or links one with the same phone), then goes on to draft the lease."""

    template_name = "prospects/convert.html"
    required_capability = "prospects.manage"

    def load(self, request, public_id):
        p = _get_prospect(request, public_id)
        if not services.can_convert(request.membership, p):
            raise Http404
        return p

    def matches(self, request, p):
        m = request.membership
        return list(tenant_services.visible_tenants(m, tenant_services.tenants_with_phone(m.organization, p.phone))
                    .filter(archived_at__isnull=True).order_by("name"))

    def get(self, request, public_id):
        p = self.load(request, public_id)
        return render(request, self.template_name, {"p": p, "matches": self.matches(request, p)})

    def post(self, request, public_id):
        p = self.load(request, public_id)
        m = request.membership
        tenant = None
        choice = request.POST.get("tenant", "")
        if choice and choice != "new":
            tenant = next((t for t in self.matches(request, p) if str(t.public_id) == choice), None)
            if tenant is None:
                raise Http404
        try:
            tenant = services.convert(m, p, tenant=tenant, request=request)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect("prospects:detail", public_id=p.public_id)
        except PermissionDenied:
            raise Http404 from None
        unit = p.held_unit or p.unit
        if unit is not None and not unit.is_archived and can(m, "leases.draft", unit.property):
            messages.success(request, _("%(name)s is now a tenant. Draft the lease next.") % {"name": tenant.name})
            return redirect(reverse("leases:create", args=[unit.public_id]) + f"?tenant={tenant.public_id}")
        messages.success(request, _("%(name)s is now a tenant.") % {"name": tenant.name})
        return redirect("tenants:detail", public_id=tenant.public_id)
