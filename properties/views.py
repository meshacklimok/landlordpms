"""Thin views: forms in, services do the work (D-009).

Lookups are always scoped twice: to the active organization and to the properties
the member may see. Anything outside that is a 404, never a 403, so other
organizations' records cannot be probed.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View

from accounts.mixins import CapabilityRequiredMixin
from accounts.permissions import can, visible_properties
from leases.services import with_occupancy

from . import forms, selectors, services
from .models import Building, Property, Unit

PAGE_SIZE = 25


def _apply_errors(form, exc: ValidationError) -> None:
    if hasattr(exc, "error_dict"):
        for field, errors in exc.message_dict.items():
            form.add_error(field if field in form.fields else None, errors)
    else:
        form.add_error(None, exc.messages)


def _get_property(request, public_id, *, include_archived=False) -> Property:
    base = Property.all_objects.all() if include_archived else Property.objects.all()
    return get_object_or_404(visible_properties(request.membership, base), public_id=public_id)


def _get_unit(request, public_id) -> Unit:
    props = visible_properties(request.membership, Property.all_objects.all())
    qs = Unit.all_objects.for_org(request.organization).filter(property__in=props).select_related(
        "property", "building"
    )
    return get_object_or_404(qs, public_id=public_id)


def _require(request, capability, prop):
    if not can(request.membership, capability, prop):
        raise PermissionDenied(capability)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


class PropertyListView(CapabilityRequiredMixin, View):
    template_name = "properties/property_list.html"
    required_capability = "properties.view"

    def get(self, request):
        search = forms.PropertySearchForm(request.GET)
        search.is_valid()
        q = search.cleaned_data.get("q", "")
        show_archived = search.cleaned_data.get("archived") and can(request.membership, "properties.manage")
        base = Property.all_objects.archived() if show_archived else Property.objects.all()
        qs = visible_properties(request.membership, base).annotate(
            unit_count=Count("units", filter=Q(units__archived_at__isnull=True))
        )
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(code__iexact=q) | Q(area__icontains=q))
        page = Paginator(qs.order_by("name"), PAGE_SIZE).get_page(request.GET.get("page"))
        return render(request, self.template_name, {
            "page": page, "q": q, "show_archived": show_archived,
            "can_manage": can(request.membership, "properties.manage"),
        })


class PropertyCreateView(CapabilityRequiredMixin, View):
    template_name = "properties/property_form.html"
    required_capability = "properties.manage"

    def get(self, request):
        return render(request, self.template_name, {"form": forms.PropertyForm()})

    def post(self, request):
        form = forms.PropertyForm(request.POST)
        if form.is_valid():
            try:
                prop = services.create_property(request.membership, request=request, **form.cleaned_data)
            except ValidationError as exc:
                _apply_errors(form, exc)
            else:
                messages.success(request, _("Property added."))
                return redirect("properties:detail", public_id=prop.public_id)
        return render(request, self.template_name, {"form": form})


class PropertyDetailView(CapabilityRequiredMixin, View):
    template_name = "properties/property_detail.html"
    required_capability = "properties.view"

    def get(self, request, public_id):
        prop = _get_property(request, public_id, include_archived=True)
        m = request.membership
        ctx = {
            "property": prop,
            "can_manage": can(m, "properties.manage", prop),
            "can_manage_units": can(m, "units.manage", prop),
            "show_units": can(m, "units.view", prop),
            "building_form": forms.BuildingForm(),
        }
        if ctx["show_units"]:
            search = forms.UnitSearchForm(request.GET)
            search.is_valid()
            q, status = search.cleaned_data.get("q", ""), search.cleaned_data.get("status", "")
            units = with_occupancy(Unit.objects.filter(property=prop))
            ctx["unit_total"] = units.count()
            ctx["status_counts"] = selectors.status_counts(units)
            matching = selectors.filter_units(units, status=status, q=q).select_related("building")
            ctx["units"] = Paginator(matching, PAGE_SIZE).get_page(request.GET.get("page"))
            ctx["q"], ctx["status"] = q, status
            ctx["archived_units"] = Unit.all_objects.archived().filter(property=prop) if ctx["can_manage_units"] else []
        ctx["buildings"] = Building.objects.filter(property=prop).annotate(
            unit_count=Count("units", filter=Q(units__archived_at__isnull=True))
        )
        return render(request, self.template_name, ctx)


class PropertyEditView(CapabilityRequiredMixin, View):
    template_name = "properties/property_form.html"
    required_capability = "properties.manage"

    def get(self, request, public_id):
        prop = _get_property(request, public_id)
        _require(request, "properties.manage", prop)
        return render(request, self.template_name, {"form": forms.PropertyForm(instance=prop), "property": prop})

    def post(self, request, public_id):
        prop = _get_property(request, public_id)
        form = forms.PropertyForm(request.POST, instance=prop)
        if form.is_valid():
            try:
                services.update_property(request.membership, prop, request=request, **form.cleaned_data)
            except ValidationError as exc:
                prop.refresh_from_db()
                _apply_errors(form, exc)
            else:
                messages.success(request, _("Property saved."))
                return redirect("properties:detail", public_id=prop.public_id)
        return render(request, self.template_name, {"form": form, "property": prop})


class PropertyArchiveView(CapabilityRequiredMixin, View):
    required_capability = "properties.manage"

    def post(self, request, public_id):
        prop = _get_property(request, public_id, include_archived=True)
        try:
            if request.POST.get("action") == "restore":
                services.restore_property(request.membership, prop, request=request)
                messages.success(request, _("Property restored."))
            else:
                services.archive_property(request.membership, prop, request=request)
                messages.success(request, _("Property archived with its buildings and units."))
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect("properties:detail", public_id=prop.public_id)


# ---------------------------------------------------------------------------
# Buildings
# ---------------------------------------------------------------------------


class BuildingCreateView(CapabilityRequiredMixin, View):
    required_capability = "properties.manage"

    def post(self, request, public_id):
        prop = _get_property(request, public_id)
        form = forms.BuildingForm(request.POST)
        if form.is_valid():
            try:
                services.create_building(request.membership, prop, name=form.cleaned_data["name"], request=request)
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
            else:
                messages.success(request, _("Building added."))
        else:
            messages.error(request, _("Enter a building name."))
        return redirect("properties:detail", public_id=prop.public_id)


class BuildingEditView(CapabilityRequiredMixin, View):
    template_name = "properties/building_form.html"
    required_capability = "properties.manage"

    def get_building(self, request, public_id) -> Building:
        props = visible_properties(request.membership, Property.objects.all())
        return get_object_or_404(
            Building.objects.for_org(request.organization).filter(property__in=props).select_related("property"),
            public_id=public_id,
        )

    def get(self, request, public_id):
        building = self.get_building(request, public_id)
        _require(request, "properties.manage", building.property)
        form = forms.BuildingForm(initial={"name": building.name})
        return render(request, self.template_name, {"form": form, "building": building})

    def post(self, request, public_id):
        building = self.get_building(request, public_id)
        prop = building.property
        if request.POST.get("action") == "archive":
            try:
                services.archive_building(request.membership, building, request=request)
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
                return redirect("properties:building_edit", public_id=building.public_id)
            messages.success(request, _("Building archived."))
            return redirect("properties:detail", public_id=prop.public_id)
        form = forms.BuildingForm(request.POST)
        if form.is_valid():
            try:
                services.rename_building(request.membership, building, name=form.cleaned_data["name"],
                                         request=request)
            except ValidationError as exc:
                _apply_errors(form, exc)
            else:
                messages.success(request, _("Building saved."))
                return redirect("properties:detail", public_id=prop.public_id)
        return render(request, self.template_name, {"form": form, "building": building})


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


class UnitListView(CapabilityRequiredMixin, View):
    """Every unit the member can see, across properties; `?status=vacant` is the vacancy list."""

    template_name = "properties/unit_list.html"
    required_capability = "units.view"

    def get(self, request):
        search = forms.UnitSearchForm(request.GET)
        search.is_valid()
        data = search.cleaned_data
        props = visible_properties(request.membership, Property.objects.all()).order_by("name")
        units = selectors.visible_units(request.membership)
        prop = props.filter(public_id=data["property"]).first() if data.get("property") else None
        if prop is not None:
            units = units.filter(property=prop)
        if data.get("unit_type"):
            units = units.filter(unit_type=data["unit_type"])
        counts = selectors.status_counts(units)
        status = data.get("status", "")
        units = selectors.filter_units(units, status=status, q=data.get("q", ""))
        page = Paginator(units.select_related("property", "building"), PAGE_SIZE).get_page(request.GET.get("page"))
        return render(request, self.template_name, {
            "page": page,
            "q": data.get("q", ""),
            "status": status,
            "unit_type": data.get("unit_type", ""),
            "selected_property": prop,
            "properties": props[:200],
            "status_counts": counts,
            "status_choices": [(key, label) for key, (label, _cond) in selectors.STATUS_FILTERS.items()],
            "type_choices": Unit.Type.choices,
        })


class UnitCreateView(CapabilityRequiredMixin, View):
    template_name = "properties/unit_form.html"
    required_capability = "units.manage"

    def get(self, request, public_id):
        prop = _get_property(request, public_id)
        _require(request, "units.manage", prop)
        return render(request, self.template_name, {"form": forms.UnitForm(property=prop), "property": prop})

    def post(self, request, public_id):
        prop = _get_property(request, public_id)
        form = forms.UnitForm(request.POST, property=prop)
        if form.is_valid():
            data = dict(form.cleaned_data)
            code = data.pop("code")
            try:
                services.create_unit(request.membership, prop, code=code, request=request, **data)
            except ValidationError as exc:
                _apply_errors(form, exc)
            else:
                messages.success(request, _("Unit %(code)s added.") % {"code": code})
                if "add_another" in request.POST:
                    return redirect("properties:unit_create", public_id=prop.public_id)
                return redirect("properties:detail", public_id=prop.public_id)
        return render(request, self.template_name, {"form": form, "property": prop})


class UnitDetailView(CapabilityRequiredMixin, View):
    """Shows a unit; edit, status and archive forms appear by capability."""

    template_name = "properties/unit_detail.html"
    required_capability = "units.view"

    def context(self, request, unit, form=None, status_form=None):
        m = request.membership
        can_edit = can(m, "units.manage", unit.property) and not unit.is_archived
        show_leases = can(m, "leases.view", unit.property)
        return {
            "leases": unit.leases.prefetch_related("lease_tenants__tenant") if show_leases else None,
            "can_draft_lease": (can(m, "leases.draft", unit.property) and not unit.is_archived
                                and unit.manual_status != Unit.ManualStatus.INACTIVE),
            "today": timezone.localdate(),
            "unit": unit,
            "property": unit.property,
            "can_edit": can_edit,
            "can_archive": can(m, "units.manage", unit.property),
            "can_set_status": can(m, "units.set_status", unit.property) and not unit.is_archived,
            "form": form or (forms.UnitForm(instance=unit, property=unit.property) if can_edit else None),
            "status_form": status_form or forms.UnitStatusForm(initial={"manual_status": unit.manual_status}),
        }

    def get(self, request, public_id):
        unit = _get_unit(request, public_id)
        return render(request, self.template_name, self.context(request, unit))

    def post(self, request, public_id):
        unit = _get_unit(request, public_id)
        m = request.membership
        action = request.POST.get("action")
        try:
            if action == "status":
                status_form = forms.UnitStatusForm(request.POST)
                if not status_form.is_valid():
                    return render(request, self.template_name, self.context(request, unit, status_form=status_form))
                services.set_unit_status(m, unit, status_form.cleaned_data["manual_status"], request=request)
                messages.success(request, _("Status updated."))
            elif action == "archive":
                services.archive_unit(m, unit, request=request)
                messages.success(request, _("Unit archived."))
                return redirect("properties:detail", public_id=unit.property.public_id)
            elif action == "restore":
                services.restore_unit(m, unit, request=request)
                messages.success(request, _("Unit restored."))
            else:
                form = forms.UnitForm(request.POST, instance=unit, property=unit.property)
                if form.is_valid():
                    try:
                        services.update_unit(m, unit, request=request, **form.cleaned_data)
                    except ValidationError as exc:
                        unit.refresh_from_db()
                        _apply_errors(form, exc)
                    else:
                        messages.success(request, _("Unit saved."))
                        return redirect("properties:unit", public_id=unit.public_id)
                return render(request, self.template_name, self.context(request, unit, form=form))
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect("properties:unit", public_id=unit.public_id)
