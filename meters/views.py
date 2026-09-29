"""Water meter pages (D-057). Thin views; services do the work.

Anything outside the member's properties is a 404, never a 403.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Prefetch
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views import View

from accounts.mixins import CapabilityRequiredMixin
from accounts.permissions import can, visible_properties
from properties.models import Property
from properties.views import _apply_errors

from . import forms, services
from .models import Meter, MeterReading, MeterUnit


def _get_property(request, public_id) -> Property:
    return get_object_or_404(visible_properties(request.membership, Property.objects.all()), public_id=public_id)


def _get_meter(request, public_id) -> Meter:
    qs = services.visible_meters(request.membership, Meter.all_objects.all()).select_related("property")
    return get_object_or_404(qs, public_id=public_id)


def _errors(exc: ValidationError) -> str:
    return " ".join(exc.messages)


def _last(meter):
    live = [r for r in meter.readings.all() if r.status in MeterReading.LIVE]
    return live[0] if live else None


class MeterListView(CapabilityRequiredMixin, View):
    template_name = "meters/list.html"
    required_capability = "meters.view"

    def get(self, request):
        m = request.membership
        readings = MeterReading.objects.order_by("-read_on", "-pk")
        meters = (services.visible_meters(m).select_related("property")
                  .prefetch_related(Prefetch("readings", queryset=readings), "served__unit"))
        waiting = dict(services.visible_readings(m).filter(status=MeterReading.Status.SUBMITTED)
                       .values_list("meter__property_id").annotate(n=Count("pk")))
        groups = {}
        for meter in meters:
            groups.setdefault(meter.property, []).append({"meter": meter, "last": _last(meter)})
        props = list(visible_properties(m).order_by("name"))
        rows = [{"property": p, "meters": groups.get(p, []), "waiting": waiting.get(p.pk, 0),
                 "can_record": can(m, "meters.record", p), "can_manage": can(m, "meters.manage", p)}
                for p in props if p in groups or can(m, "meters.manage", p)]
        return render(request, self.template_name, {
            "rows": rows, "to_approve": services.to_approve(m).count() if can(m, "meters.approve") else 0})


class MeterFormView(CapabilityRequiredMixin, View):
    template_name = "meters/form.html"
    required_capability = "meters.manage"

    def _target(self, request, kwargs):
        if "public_id" in kwargs:
            meter = _get_meter(request, kwargs["public_id"])
            return meter, meter.property
        return None, _get_property(request, kwargs["property_id"])

    def _units(self, prop, meter, post=None):
        served = {mu.unit_id: mu.weight for mu in meter.served.all()} if meter else {}
        rows = []
        for unit in services.units_for(prop):
            if post is not None:
                checked, weight = f"unit-{unit.pk}" in post, post.get(f"weight-{unit.pk}", "1")
            else:
                checked, weight = unit.pk in served, served.get(unit.pk, 1)
            rows.append({"unit": unit, "checked": checked, "weight": f"{weight:g}" if not isinstance(weight, str)
                         else weight})
        return rows

    def _render(self, request, prop, meter, form, units):
        return render(request, self.template_name, {"property": prop, "meter": meter, "form": form, "units": units})

    def get(self, request, **kwargs):
        meter, prop = self._target(request, kwargs)
        if not can(request.membership, "meters.manage", prop):
            raise Http404
        initial = {}
        if meter:
            initial = {"label": meter.label, "serial": meter.serial, "kind": meter.kind, "split": meter.split,
                       "rate": f"{meter.rate:g}", "minimum_charge": f"{meter.minimum_charge:g}"
                       if meter.minimum_charge else ""}
        return self._render(request, prop, meter, forms.MeterForm(initial=initial), self._units(prop, meter))

    def post(self, request, **kwargs):
        meter, prop = self._target(request, kwargs)
        if not can(request.membership, "meters.manage", prop):
            raise Http404
        action = request.POST.get("action", "save")
        if meter and action in ("archive", "restore"):
            try:
                getattr(services, f"{action}_meter")(request.membership, meter, request=request)
            except ValidationError as exc:
                messages.error(request, _errors(exc))
            else:
                messages.success(request, _("Meter archived.") if action == "archive" else _("Meter restored."))
            return redirect("meters:detail", public_id=meter.public_id)
        form = forms.MeterForm(request.POST)
        units = self._units(prop, meter, request.POST)
        if not form.is_valid():
            return self._render(request, prop, meter, form, units)
        chosen = {u.pk: u for u in services.units_for(prop)}
        served = [(chosen[int(k.split("-", 1)[1])], request.POST.get(f"weight-{k.split('-', 1)[1]}", "1"))
                  for k in request.POST if k.startswith("unit-") and k.split("-", 1)[1].isdigit()
                  and int(k.split("-", 1)[1]) in chosen]
        try:
            if meter:
                meter = services.update_meter(request.membership, meter, units=served, request=request,
                                              **form.cleaned_data)
            else:
                meter = services.create_meter(request.membership, prop, units=served, request=request,
                                              **form.cleaned_data)
        except ValidationError as exc:
            _apply_errors(form, exc)
            return self._render(request, prop, meter, form, units)
        messages.success(request, _("Meter saved."))
        return redirect("meters:detail", public_id=meter.public_id)


class MeterDetailView(CapabilityRequiredMixin, View):
    template_name = "meters/detail.html"
    required_capability = "meters.view"

    def context(self, request, meter, reading_form=None):
        m, prop = request.membership, meter.property
        readings = list(meter.readings.select_related("previous", "recorded_by", "approved_by", "rejected_by")
                        .prefetch_related("charges__lease", "charges__lines__invoice"))
        for r in readings:
            r.can_approve = services.can_approve(m, r)
            r.can_undo = services.can_undo(m, r)
            r.live_charges = [c for c in r.charges.all() if c.cancelled_at is None]
            if r.can_approve:
                r.estimate = services.estimate(r)
        can_record = can(m, "meters.record", prop) and not meter.is_archived
        latest = services.latest_reading(meter)
        return {
            "meter": meter, "property": prop, "readings": readings, "latest": latest,
            "served": meter.served.select_related("unit"),
            "can_manage": can(m, "meters.manage", prop), "can_record": can_record,
            "can_see_invoices": can(m, "invoices.view", prop),
            "reading_form": reading_form or (forms.ReadingForm(initial={"read_on": timezone.localdate().isoformat()})
                                             if can_record else None),
        }

    def get(self, request, public_id):
        meter = _get_meter(request, public_id)
        return render(request, self.template_name, self.context(request, meter))

    def post(self, request, public_id):
        meter = _get_meter(request, public_id)
        action = request.POST.get("action", "")
        if action == "record":
            form = forms.ReadingForm(request.POST, request.FILES)
            if form.is_valid():
                try:
                    services.record_reading(request.membership, meter, request=request, **form.cleaned_data)
                except ValidationError as exc:
                    _apply_errors(form, exc)
                except PermissionDenied:
                    raise Http404 from None
            if form.errors:
                return render(request, self.template_name, self.context(request, meter, reading_form=form))
            messages.success(request, _("Reading recorded. It is waiting for approval."))
            return redirect("meters:detail", public_id=meter.public_id)
        reading = get_object_or_404(meter.readings.all(), public_id=request.POST.get("reading") or None)
        _act(request, action, reading)
        return redirect("meters:detail", public_id=meter.public_id)


def _act(request, action, reading) -> None:
    """Approve, reject or undo one reading, with the outcome as a message."""
    m = request.membership
    try:
        if action == "approve":
            charges = services.approve(m, reading, note=request.POST.get("note", ""), request=request)
            n = len(charges)
            messages.success(request, ngettext("Approved. %(n)s water charge made.",
                                               "Approved. %(n)s water charges made.", n) % {"n": n}
                             if n else _("Approved."))
        elif action == "reject":
            services.reject(m, reading, reason=request.POST.get("reason", ""), request=request)
            messages.success(request, _("Reading rejected."))
        elif action == "undo":
            services.undo_approval(m, reading, request=request)
            messages.success(request, _("Approval undone. The reading is waiting again."))
    except ValidationError as exc:
        messages.error(request, _errors(exc))
    except PermissionDenied:
        raise Http404 from None


class ApprovalView(CapabilityRequiredMixin, View):
    template_name = "meters/approvals.html"
    required_capability = "meters.approve"

    def get(self, request):
        readings = list(services.to_approve(request.membership)
                        .select_related("meter__property", "previous", "recorded_by").order_by("read_on", "pk"))
        for r in readings:
            r.estimate = services.estimate(r)
        return render(request, self.template_name, {"readings": readings,
                                                    "clean": sum(1 for r in readings if not r.flags)})

    def post(self, request):
        action = request.POST.get("action", "")
        queue = services.to_approve(request.membership).select_related("meter__property", "previous")
        if action == "approve_selected":
            ids = request.POST.getlist("reading")
            done, skipped = services.approve_many(request.membership, list(queue.filter(public_id__in=ids)),
                                                  request=request)
            if done:
                messages.success(request, ngettext("%(n)s reading approved.", "%(n)s readings approved.", done)
                                 % {"n": done})
            if skipped:
                messages.warning(request, ngettext(
                    "%(n)s reading was left: it is flagged or waits for an earlier one.",
                    "%(n)s readings were left: they are flagged or wait for an earlier one.", skipped) % {"n": skipped})
            return redirect("meters:approvals")
        reading = get_object_or_404(queue, public_id=request.POST.get("reading") or None)
        _act(request, action, reading)
        return redirect("meters:approvals")


class RoundView(CapabilityRequiredMixin, View):
    """The monthly round: one box per meter on a property, filled on a phone (D-057 item 9)."""

    template_name = "meters/round.html"
    required_capability = "meters.record"

    def _meters(self, prop):
        return list(Meter.objects.filter(property=prop).prefetch_related(
            Prefetch("readings", queryset=MeterReading.objects.order_by("-read_on", "-pk")),
            Prefetch("served", queryset=MeterUnit.objects.select_related("unit"))).order_by("label"))

    def _rows(self, meters, post=None, errors=None):
        rows = []
        for meter in meters:
            row = {"meter": meter, "last": _last(meter), "errors": (errors or {}).get(meter.pk, [])}
            if post is not None:
                row.update(value=post.get(f"value-{meter.pk}", ""), note=post.get(f"note-{meter.pk}", ""),
                           replaced=f"replaced-{meter.pk}" in post)
            rows.append(row)
        return rows

    def _prop(self, request, property_id):
        prop = _get_property(request, property_id)
        if not can(request.membership, "meters.record", prop):
            raise Http404
        return prop

    def get(self, request, property_id):
        prop = self._prop(request, property_id)
        form = forms.RoundDateForm(initial={"read_on": timezone.localdate().isoformat()})
        return render(request, self.template_name, {"property": prop, "form": form,
                                                    "rows": self._rows(self._meters(prop))})

    def post(self, request, property_id):
        prop = self._prop(request, property_id)
        meters = self._meters(prop)
        form = forms.RoundDateForm(request.POST)
        entries = [services.RoundEntry(meter, request.POST.get(f"value-{meter.pk}", "").strip(),
                                       request.FILES.get(f"photo-{meter.pk}"), request.POST.get(f"note-{meter.pk}", ""),
                                       f"replaced-{meter.pk}" in request.POST)
                   for meter in meters if request.POST.get(f"value-{meter.pk}", "").strip()]
        errors = {}
        if form.is_valid() and not entries:
            form.add_error(None, _("Enter at least one reading."))
        if form.is_valid():
            saved, errors = services.record_round(request.membership, form.cleaned_data["read_on"], entries,
                                                  request=request)
            if not errors:
                messages.success(request, ngettext("%(n)s reading recorded. It is waiting for approval.",
                                                   "%(n)s readings recorded. They are waiting for approval.",
                                                   len(saved)) % {"n": len(saved)})
                return redirect("meters:list")
            form.add_error(None, _("Nothing was saved. Fix the readings marked below and send again. "
                                   "Choose any photos again."))
        return render(request, self.template_name, {"property": prop, "form": form,
                                                    "rows": self._rows(meters, request.POST, errors)})


class PhotoView(CapabilityRequiredMixin, View):
    """Serves a reading's photo only to members who may see the meter. Never public."""

    required_capability = "meters.view"

    def get(self, request, public_id):
        reading = get_object_or_404(services.visible_readings(request.membership).exclude(photo=""),
                                    public_id=public_id)
        try:
            handle = reading.photo.open("rb")
        except FileNotFoundError:
            raise Http404 from None
        response = FileResponse(handle, content_type="image/jpeg")
        response["Cache-Control"] = "private, max-age=3600"
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response
