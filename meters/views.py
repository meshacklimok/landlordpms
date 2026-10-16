"""Meter pages: water (D-057) and electricity (D-070). Thin views; services do the work.

Anything outside the member's properties is a 404, never a 403.
"""

import csv
import datetime
import json

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Prefetch
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.http import urlencode
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views import View

from accounts.mixins import CapabilityRequiredMixin
from accounts.permissions import can, visible_properties
from core.ids import uuid_or_none
from properties.models import Property
from properties.views import _apply_errors

from . import forms, ocr, services, sheet, usage
from . import split as splitting
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
        readings = MeterReading.objects.select_related("previous").order_by("-read_on", "-pk")
        utility = request.GET.get("utility", "")
        meters = services.visible_meters(m)
        if utility in Meter.Utility.values:
            meters = meters.filter(utility=utility)
        else:
            utility = ""
        meters = (meters.select_related("property")
                  .prefetch_related(Prefetch("readings", queryset=readings), "served__unit"))
        waiting = dict(services.visible_readings(m).filter(status=MeterReading.Status.SUBMITTED)
                       .values_list("meter__property_id").annotate(n=Count("pk")))
        today = timezone.localdate()
        groups = {}
        for meter in meters:
            last = _last(meter)
            # Billed meters only: a prepaid meter is never read for a bill, so it is not "left to read".
            read = last is not None and (last.read_on.year, last.read_on.month) == (today.year, today.month)
            groups.setdefault(meter.property, []).append({"meter": meter, "last": last, "read_this_month": read,
                                                          "used": last.usage if last else None})
        props = list(visible_properties(m).order_by("name"))
        rows = [{"property": p, "meters": groups.get(p, []), "waiting": waiting.get(p.pk, 0),
                 "to_read": sum(1 for g in groups.get(p, []) if not g["read_this_month"] and not g["meter"].is_prepaid),
                 "can_record": can(m, "meters.record", p), "can_manage": can(m, "meters.manage", p)}
                for p in props if p in groups or can(m, "meters.manage", p)]
        return render(request, self.template_name, {
            "rows": rows, "utility": utility, "utilities": Meter.Utility.choices,
            "to_approve": services.to_approve(m).count() if can(m, "meters.approve") else 0})


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
            initial = {"utility": meter.utility, "label": meter.label, "serial": meter.serial, "kind": meter.kind,
                       "split": meter.split, "payer": meter.payer, "vacant_share": meter.vacant_share,
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
        reading_form = reading_form or (forms.ReadingForm(initial={"read_on": timezone.localdate().isoformat()})
                                        if can_record else None)
        if reading_form is not None:
            reading_form.fields["value"].label = _("Reading (%(unit)s)") % {"unit": meter.unit_label}
        return {
            "meter": meter, "property": prop, "readings": readings, "latest": latest,
            "served": meter.served.select_related("unit"),
            "usual_daily": services.usual_daily_use(meter, timezone.localdate()),
            "can_manage": can(m, "meters.manage", prop), "can_record": can_record,
            "can_see_invoices": can(m, "invoices.view", prop),
            "reading_form": reading_form,
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
        reading = get_object_or_404(meter.readings.all(), public_id=uuid_or_none(request.POST.get("reading")))
        _act(request, action, reading)
        return redirect("meters:detail", public_id=meter.public_id)


def _act(request, action, reading) -> None:
    """Approve, reject or undo one reading, with the outcome as a message."""
    m = request.membership
    try:
        if action == "approve":
            charges = services.approve(m, reading, note=request.POST.get("note", ""), request=request)
            n = len(charges)
            messages.success(request, ngettext("Approved. %(n)s charge made.",
                                               "Approved. %(n)s charges made.", n) % {"n": n}
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
                        .select_related("meter__property", "previous", "recorded_by")
                        .annotate(served_n=Count("meter__served")).order_by("read_on", "pk"))
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
        reading = get_object_or_404(queue, public_id=uuid_or_none(request.POST.get("reading")))
        _act(request, action, reading)
        return redirect("meters:approvals")


class RoundView(CapabilityRequiredMixin, View):
    """The monthly round as a reading sheet: one row per meter in room order, with who lives there, the last
    reading, and the use and amount worked out as the reading is typed (D-057 item 9, D-143)."""

    template_name = "meters/round.html"
    required_capability = "meters.record"

    def _meters(self, prop):
        return list(Meter.objects.filter(property=prop).prefetch_related(
            Prefetch("readings", queryset=MeterReading.objects.order_by("-read_on", "-pk")),
            Prefetch("served", queryset=MeterUnit.objects.select_related("unit"))).order_by("label"))

    def _rows(self, meters, post=None, errors=None):
        today = timezone.localdate()
        rows = sheet.sheet_meters(meters, today)
        for row in rows:
            pk = row["meter"].pk
            last = _last(row["meter"])
            # Read earlier this month: shown so the same meter is not read twice (the sheet can still be filled in).
            done = last if last and (last.read_on.year, last.read_on.month) == (today.year, today.month) else None
            row.update(last=last, done=done, errors=(errors or {}).get(pk, []))
            if post is not None:
                row.update(value=post.get(f"value-{pk}", ""), units=post.get(f"units-{pk}", ""),
                           by_units=post.get(f"byunits-{pk}") == "1", note=post.get(f"note-{pk}", ""),
                           replaced=f"replaced-{pk}" in post)
        return rows

    def _prop(self, request, property_id):
        prop = _get_property(request, property_id)
        if not can(request.membership, "meters.record", prop):
            raise Http404
        return prop

    def _context(self, prop, form, rows):
        utilities = sorted({row["meter"].utility for row in rows})
        org = prop.organization
        return {"property": prop, "form": form, "rows": rows,
                "done_count": sum(1 for row in rows if row["done"]),
                "utilities": [(u, Meter.Utility(u).label) for u in utilities] if len(utilities) > 1 else [],
                "scans": {"left": ocr.left(org), "limit": ocr.limit(org)} if ocr.available(org) else None}

    def get(self, request, property_id):
        prop = self._prop(request, property_id)
        form = forms.RoundDateForm(initial={"read_on": timezone.localdate().isoformat()})
        return render(request, self.template_name, self._context(prop, form, self._rows(self._meters(prop))))

    @staticmethod
    def _entry(meter, post, files):
        """The row's entry, or None when it was left empty. Units used win when the units box was typed last."""
        pk = meter.pk
        value, units = post.get(f"value-{pk}", "").strip(), post.get(f"units-{pk}", "").strip()
        by_units = post.get(f"byunits-{pk}") == "1" and bool(units)
        if not value and not by_units:
            return None
        return services.RoundEntry(meter, "" if by_units else value, files.get(f"photo-{pk}"),
                                   post.get(f"note-{pk}", ""), f"replaced-{pk}" in post,
                                   units_used=units if by_units else None)

    def post(self, request, property_id):
        prop = self._prop(request, property_id)
        meters = self._meters(prop)
        form = forms.RoundDateForm(request.POST)
        entries = [e for e in (self._entry(m, request.POST, request.FILES) for m in meters) if e is not None]
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
        return render(request, self.template_name,
                      self._context(prop, form, self._rows(meters, request.POST, errors)))


class RoundImportView(RoundView):
    """Lines pasted or uploaded as a CSV ("A1, 134"), matched to the sheet's meters and sent back to fill it in.
    Nothing is saved: the rows are checked and saved with the sheet (D-143)."""

    MAX_UPLOAD = 256 * 1024

    def get(self, request, property_id):
        return JsonResponse({"error": "POST the lines."}, status=405)

    def post(self, request, property_id):
        prop = self._prop(request, property_id)
        utility = request.POST.get("utility", "")
        utility = utility if utility in Meter.Utility.values else ""
        units_used = request.POST.get("mode") == "units"
        photo = request.FILES.get("photo")
        if photo is not None:
            # A photo of the notebook, read by the model (D-144).
            try:
                result = ocr.scan_notebook(request.membership, prop, photo, self._meters(prop),
                                           units_used=units_used, utility=utility)
            except ValidationError as exc:
                return JsonResponse({"error": _errors(exc), "left": ocr.left(request.membership.organization)},
                                    status=400)
            return JsonResponse({"matches": result.matches, "skipped": result.skipped,
                                 "left": ocr.left(request.membership.organization)})
        text = request.POST.get("text", "")
        upload = request.FILES.get("file")
        if upload is not None:
            if upload.size > self.MAX_UPLOAD:
                return JsonResponse({"error": _("The file is too big. Keep it under 256 KB.")}, status=400)
            text = upload.read().decode("utf-8-sig", errors="replace")
        if not text.strip():
            return JsonResponse({"error": _("Paste some lines or choose a file first.")}, status=400)
        result = sheet.match_lines(sheet.parse_lines(text), self._meters(prop), units_used=units_used,
                                   utility=utility)
        return JsonResponse({"matches": result.matches, "skipped": result.skipped})


class SharesView(CapabilityRequiredMixin, View):
    """Who pays what for one reading of a shared meter (D-143). Waiting: every way of dividing it side by side,
    with the utility bill typed in or not, and an Approve button per way. Approved: what each room was charged."""

    template_name = "meters/shares.html"
    required_capability = "meters.view"

    def _reading(self, request, public_id):
        qs = services.visible_readings(request.membership).select_related("meter__property", "previous")
        return get_object_or_404(qs, public_id=public_id)

    def get(self, request, public_id):
        reading = self._reading(request, public_id)
        meter = reading.meter
        served = list(meter.served.select_related("unit"))
        ctx = {"reading": reading, "meter": meter, "property": meter.property, "served": served,
               "can_approve": services.can_approve(request.membership, reading),
               "vacant_choices": Meter.VacantShare.choices}
        if reading.status == MeterReading.Status.APPROVED:
            charges = list(reading.charges.filter(cancelled_at__isnull=True).select_related("lease"))
            ctx["charged"] = [{"unit": mu.unit, "charges": [c for c in charges if c.unit_id == mu.unit_id]}
                              for mu in served]
            ctx["charged_total"] = sum((c.amount for c in charges), 0)
            return render(request, self.template_name, ctx)
        vacant = request.GET.get("vacant", "")
        vacant = vacant if vacant in Meter.VacantShare.values else meter.vacant_share
        bill_text = request.GET.get("bill", "").strip()
        bill = None
        try:
            bill = services.clean_bill_total(bill_text)
        except ValidationError as exc:
            ctx["bill_error"] = _errors(exc)
        org = request.membership.organization
        ctx.update(vacant=vacant, bill=bill_text, billable=services.billable(reading),
                   scans_left=ocr.left(org) if ctx["can_approve"] and ocr.available(org) else None)
        if ctx["billable"]:
            ways = splitting.compare(reading, vacant_share=vacant, bill_total=bill)
            ctx.update(ways=ways, total=ways[0].total,
                       rows=[{"unit": mu.unit, "who": ways[0].rooms[i],
                              "cells": [{"way": w, "room": w.rooms[i]} for w in ways]} for i, mu in enumerate(served)])
        return render(request, self.template_name, ctx)

    def post(self, request, public_id):
        reading = self._reading(request, public_id)
        try:
            charges = services.approve(request.membership, reading, note=request.POST.get("note", ""),
                                       split=request.POST.get("split", ""),
                                       vacant_share=request.POST.get("vacant", ""),
                                       bill_total=request.POST.get("bill", ""), request=request)
        except ValidationError as exc:
            messages.error(request, _errors(exc))
            query = {k: request.POST.get(k, "") for k in ("vacant", "bill") if request.POST.get(k)}
            url = reverse("meters:shares", args=[reading.public_id])
            return redirect(f"{url}?{urlencode(query)}" if query else url)
        except PermissionDenied:
            raise Http404 from None
        n = len(charges)
        messages.success(request, ngettext("Approved. %(n)s charge made.", "Approved. %(n)s charges made.", n)
                         % {"n": n} if n else _("Approved."))
        return redirect("meters:detail", public_id=reading.meter.public_id)


class BillScanView(SharesView):
    """A photo of the utility bill read by the model (D-144): back to the page with the amount filled in, to check
    against the paper before approving. Nothing is approved here."""

    def get(self, request, public_id):
        return redirect("meters:shares", public_id=self._reading(request, public_id).public_id)

    def post(self, request, public_id):
        reading = self._reading(request, public_id)
        vacant = request.POST.get("vacant", "")
        query = {"vacant": vacant} if vacant in Meter.VacantShare.values else {}
        url = reverse("meters:shares", args=[reading.public_id])
        photo = request.FILES.get("photo")
        if photo is None:
            messages.error(request, _("Choose a photo of the bill."))
            return redirect(f"{url}?{urlencode(query)}" if query else url)
        try:
            bill = ocr.scan_bill(request.membership, reading, photo)
        except PermissionDenied:
            raise Http404 from None
        except ValidationError as exc:
            messages.error(request, _errors(exc))
            return redirect(f"{url}?{urlencode(query)}" if query else url)
        query["bill"] = str(bill.amount)
        found = _("Read from the bill: KES %(amount)s") % {"amount": f"{bill.amount:,.2f}"}
        if bill.period_from and bill.period_to:
            found += _(" for %(start)s to %(end)s") % {"start": bill.period_from.strftime("%d %b %Y"),
                                                       "end": bill.period_to.strftime("%d %b %Y")}
        if bill.units is not None:
            found += _(", %(units)s units") % {"units": services.fmt_weight(bill.units)}
        messages.info(request, found + ". " + _("Check it against the bill before you approve."))
        return redirect(f"{url}?{urlencode(query)}")


def _parse(parser, value):
    if not isinstance(value, str):
        return None
    try:
        return parser(value)
    except ValueError:
        return None


class OfflineRoundView(RoundView):
    """A round kept on the phone while offline, sent as JSON once back online (D-088 item 3).

    2xx: recorded (or recorded before). 422: refused, with reasons per meter. Anything else: the phone tries later.
    """

    MAX_KEY = 64

    def get(self, request, property_id):
        return JsonResponse({"error": "POST a round."}, status=405)

    def post(self, request, property_id):
        prop = self._prop(request, property_id)
        try:
            data = json.loads(request.body or b"{}")
        except ValueError:
            return JsonResponse({"error": "The body is not valid JSON."}, status=400)
        key = data.get("id") if isinstance(data, dict) else None
        entries_in = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(key, str) or not 8 <= len(key) <= self.MAX_KEY or not isinstance(entries_in, list):
            return JsonResponse({"error": "Send id, read_on and entries."}, status=400)
        # A well-formed but impossible date (2026-02-30) raises ValueError: treat it as missing, so the
        # phone gets a 422 it stops retrying instead of a 500 it retries forever. typed_at is optional.
        read_on = _parse(parse_date, data.get("read_on"))
        typed_at = _parse(parse_datetime, data.get("typed_at"))
        meters = {str(m.public_id): m for m in self._meters(prop)}
        entries, errors = [], {}
        for item in entries_in:
            if not isinstance(item, dict) or not isinstance(item.get("value"), str):
                return JsonResponse({"error": "Each entry needs meter and value."}, status=400)
            meter_id = str(item.get("meter"))
            if meter_id not in meters:
                errors[meter_id] = [_("This meter is no longer on this property.")]
                continue
            units = item.get("units_used")
            units = units.strip() if isinstance(units, str) and units.strip() else None
            entries.append(services.RoundEntry(meters[meter_id], "" if units else item["value"].strip(), None,
                                               str(item.get("note") or "")[:300], bool(item.get("replaced")),
                                               units_used=units))
        if read_on is None:
            return JsonResponse({"status": "refused", "message": _("The reading date is missing."), "errors": {}},
                                status=422)
        if not entries and not errors:
            return JsonResponse({"status": "refused", "message": _("Enter at least one reading."), "errors": {}},
                                status=422)
        if not errors:
            saved, by_pk = services.record_offline_round(request.membership, prop, key=key, read_on=read_on,
                                                         entries=entries, typed_at=typed_at, request=request)
            if saved is None:
                return JsonResponse({"status": "already"})
            if not by_pk:
                return JsonResponse({"status": "recorded", "readings": len(saved)}, status=201)
            errors = {str(m.public_id): by_pk[m.pk] for m in meters.values() if m.pk in by_pk}
        return JsonResponse({"status": "refused", "errors": errors,
                             "message": _("Nothing was saved. Fix the readings marked and send again.")}, status=422)


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


def _cell(value) -> str:
    """A CSV cell that a spreadsheet will not run as a formula."""
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


class UsageView(CapabilityRequiredMixin, View):
    """Each room's water and electricity in a month, from the approved readings (D-145): the units each tenant
    used, what they were charged, and what fell on the landlord. ?format=csv downloads it."""

    template_name = "meters/usage.html"
    required_capability = "meters.view"

    def get(self, request):
        m = request.membership
        today = timezone.localdate()
        try:
            month = datetime.datetime.strptime(request.GET.get("month", ""), "%Y-%m").date()
        except ValueError:
            month = today.replace(day=1)
        props = list(visible_properties(m, Property.objects.all()).filter(meters__isnull=False).distinct()
                     .order_by("name"))
        prop_id = uuid_or_none(request.GET.get("property", ""))
        prop = next((p for p in props if p.public_id == prop_id), None)
        sections = usage.report(m, month, prop)
        if request.GET.get("format") == "csv":
            return self.csv(month, sections)
        previous = (month - datetime.timedelta(days=1)).replace(day=1)
        following = (month + datetime.timedelta(days=32)).replace(day=1)
        return render(request, self.template_name, {
            "month": month, "previous": previous, "next": following if following <= today else None,
            "properties": props, "property": prop, "sections": sections, "totals": usage.totals(sections)})

    def csv(self, month, sections):
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="meter-usage-{month:%Y-%m}.csv"'
        response.write("\ufeff")
        out = csv.writer(response)
        out.writerow(["Property", "Room", "Tenant", "Water m3", "Water KES", "Electricity kWh", "Electricity KES",
                      "Total KES"])
        def figures(use):
            water, power = use["water"], use["power"]
            return [water["units"], water["amount"], power["units"], power["amount"], water["amount"] + power["amount"]]

        for s in sections:
            for row in s.rows:
                out.writerow([_cell(s.property.name), _cell(row.unit.code), _cell(row.tenant), *figures(row.use)])
            out.writerow([_cell(s.property.name), "", "Landlord (empty rooms, meters the landlord pays)",
                          *figures(s.landlord)])
        return response
