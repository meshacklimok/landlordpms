"""JSON for the hostel room map and waitlist in the Next.js app (D-168, D-195); the rules live in properties.hostel."""

import json
from collections import defaultdict

from django import forms as dj_forms
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from accounts.permissions import can, visible_properties
from accounts.web_api import MemberJSONView
from core.ids import uuid_or_none
from leases import rollover
from leases.forms import RolloverForm
from leases.models import Lease
from leases.services import with_occupancy
from prospects import services as prospects

from . import forms, hostel
from .models import Property, Room, Unit
from .views import _occupant_name


def _hostel_property(request, public_id) -> Property:
    m = request.membership
    prop = (visible_properties(m, Property.all_objects.all()).filter(public_id=public_id)
            .select_related("organization").first())
    if prop is None or m.organization.org_type != "HOSTEL":
        raise Http404
    return prop


def _need(request, capability, prop) -> None:
    if not can(request.membership, capability, prop):
        raise Http404  # a page the member may not use is answered as one that is not there


def _map(request, prop: Property) -> dict:
    m = request.membership
    beds = (with_occupancy(Unit.objects.filter(property=prop, room__isnull=False, room__archived_at__isnull=True))
            .annotate(occupant=_occupant_name()).order_by("room__code", "code"))
    by_room = defaultdict(list)
    for unit in beds:
        by_room[unit.room_id].append(unit)
    rooms, free, taken = [], 0, 0
    for room in Room.objects.filter(property=prop).select_related("building").order_by("building__name", "code"):
        units = by_room.get(room.pk, [])
        n_taken = sum(1 for u in units if u.is_occupied)
        free += len(units) - n_taken
        taken += n_taken
        rooms.append({
            "id": str(room.public_id), "code": room.code, "gender": room.gender,
            "gender_label": room.get_gender_display() if room.gender else "",
            "building": room.building.name if room.building else "", "taken": n_taken, "beds": len(units),
            "edit_url": reverse("properties:room", args=[room.public_id]),
            "units": [{"id": str(u.public_id), "code": u.code, "occupant": u.occupant or "",
                       "free": not u.is_occupied, "reference": u.payment_reference,
                       "url": reverse("properties:unit", args=[u.public_id])} for u in units],
        })
    live = not prop.is_archived
    return {
        "property": {"id": str(prop.public_id), "name": prop.name, "url": reverse("properties:detail", args=[prop.public_id])},
        "free": free, "taken": taken, "rooms": rooms, "waiting": prospects.waitlist(prop).count(),
        "booking_url": (request.build_absolute_uri(reverse("hostel_stay", args=[prop.booking_token]))
                        if prop.booking_token else ""),
        "can_manage": can(m, "units.manage", prop) and live,
        "can_rollover": can(m, "leases.draft", prop) and live,
        "post_url": reverse("properties:web_rooms", args=[prop.public_id]),
        "links": {"add_rooms": reverse("properties:rooms_create", args=[prop.public_id]),
                  "waitlist": reverse("properties:waitlist", args=[prop.public_id]),
                  "rollover": reverse("properties:rollover", args=[prop.public_id])},
    }


class RoomMapDataView(MemberJSONView):
    """GET the room map; POST {"action": "on" | "off"} turns the public booking page on or off."""

    def get(self, request, public_id):
        prop = _hostel_property(request, public_id)
        _need(request, "units.view", prop)
        return JsonResponse(_map(request, prop))

    def post(self, request, public_id):
        prop = _hostel_property(request, public_id)
        _need(request, "units.manage", prop)
        try:
            action = json.loads(request.body or b"{}").get("action")
        except (ValueError, AttributeError):
            action = None
        if action not in ("on", "off"):
            return JsonResponse({"error": "bad_request", "message": _("That could not be read.")}, status=400)
        try:
            if action == "off":
                hostel.disable_booking_link(request.membership, prop, request=request)
                message = _("The booking page is off.")
            else:
                hostel.enable_booking_link(request.membership, prop, request=request)
                message = _("The booking page is on. Share its link with students.")
        except ValidationError as exc:
            return JsonResponse({"error": "invalid", "message": " ".join(exc.messages)}, status=422)
        prop.refresh_from_db()
        return JsonResponse({"message": message, "map": _map(request, prop)})


class WaitlistDataView(MemberJSONView):
    def get(self, request, public_id):
        prop = _hostel_property(request, public_id)
        _need(request, "prospects.view", prop)
        rows = []
        for p in prospects.waitlist(prop):
            whole = {"Bed": False, "Whole room": True}.get(p.wants)
            rows.append({"name": p.name, "phone": p.phone, "gender": p.gender,
                         "gender_label": p.get_gender_display() if p.gender else "", "wants": p.wants,
                         "since": p.created_at.date().isoformat(),
                         "offered": p.offered_at.date().isoformat() if p.offered_at else "",
                         "free": hostel.free_beds(prop, p.gender, whole).count(),
                         "url": reverse("prospects:detail", args=[p.public_id])})
        return JsonResponse({"property": {"id": str(prop.public_id), "name": prop.name},
                             "rows": rows, "ready": sum(1 for r in rows if r["free"]),
                             "rooms_url": reverse("properties:rooms", args=[prop.public_id])})


# ---------------------------------------------------------------------------
# Forms for the Next.js app (D-196): the Django form says what the fields are and whether they are right
# ---------------------------------------------------------------------------


def _describe(form: dj_forms.Form) -> list[dict]:
    """The form's fields as the screen draws them: kind, label, help, choices and the starting value."""
    out = []
    for name, field in form.fields.items():
        if isinstance(field, dj_forms.DateField):
            kind = "date"
        elif isinstance(field, (dj_forms.IntegerField, dj_forms.DecimalField)):
            kind = "number"
        elif isinstance(field, (dj_forms.ChoiceField, dj_forms.ModelChoiceField)):
            kind = "radio" if isinstance(field.widget, dj_forms.RadioSelect) else "select"
        else:
            kind = "text"
        value = form.initial.get(name, field.initial)
        item = {"name": name, "label": str(field.label), "kind": kind, "required": field.required,
                "help": str(field.help_text or ""), "value": "" if value is None else str(value)}
        if kind in ("select", "radio"):
            item["choices"] = [[str(k), str(v)] for k, v in field.choices]
        for attr, key in (("min_value", "min"), ("max_value", "max")):
            if getattr(field, attr, None) is not None:
                item[key] = int(getattr(field, attr))
        out.append(item)
    return out


def _json_body(request) -> dict:
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _form_errors(form) -> JsonResponse:
    general = [str(m) for m in form.errors.get("__all__", [])]
    return JsonResponse({"error": "invalid", "message": " ".join(general) or _("Please correct the highlighted fields."),
                         "fields": {k: [str(m) for m in v] for k, v in form.errors.items() if k != "__all__"},
                         "form_errors": [str(m) for m in form.errors.get("__all__", [])]}, status=422)


def _clash(exc: ValidationError, form) -> JsonResponse:
    if hasattr(exc, "error_dict"):
        for field, errors in exc.message_dict.items():
            form.add_error(field if field in form.fields else None, errors)
    else:
        form.add_error(None, exc.messages)
    return _form_errors(form)


def _values(body: dict) -> dict:
    values = body.get("values")
    if not isinstance(values, dict):
        return {}
    return {k: v for k, v in values.items() if isinstance(v, (str, int, float))}


class RoomsCreateDataView(MemberJSONView):
    """GET the fields for adding a run of rooms; POST {"values": {...}} makes them."""

    def get(self, request, public_id):
        prop = _hostel_property(request, public_id)
        _need(request, "units.manage", prop)
        form = forms.RoomsForm(property=prop)
        return JsonResponse({"property": {"id": str(prop.public_id), "name": prop.name}, "fields": _describe(form),
                             "post_url": reverse("properties:web_rooms_new", args=[prop.public_id]),
                             "rooms_url": reverse("properties:rooms", args=[prop.public_id])})

    def post(self, request, public_id):
        prop = _hostel_property(request, public_id)
        _need(request, "units.manage", prop)
        form = forms.RoomsForm(_values(_json_body(request)), property=prop)
        if not form.is_valid():
            return _form_errors(form)
        d = form.cleaned_data
        try:
            made = hostel.add_rooms(request.membership, prop, prefix=d["prefix"], first=d["first"], count=d["count"],
                                    capacity=d["capacity"], let_whole=d["let"] == forms.RoomsForm.LET_WHOLE,
                                    gender=d["gender"], building=d.get("building"), price=d["price"], request=request)
        except ValidationError as exc:
            return _clash(exc, form)
        return JsonResponse({"message": ngettext("%(n)s room added.", "%(n)s rooms added.", len(made)) % {"n": len(made)}, "done": "added",
                             "redirect": reverse("properties:rooms", args=[prop.public_id])})


def _room(request, public_id) -> Room:
    m = request.membership
    props = visible_properties(m, Property.all_objects.all())
    room = (Room.all_objects.filter(organization=m.organization, property__in=props, public_id=public_id)
            .select_related("property", "building").first())
    if room is None or m.organization.org_type != "HOSTEL":
        raise Http404
    _need(request, "units.manage", room.property)
    return room


class RoomEditDataView(MemberJSONView):
    """GET a room's fields; POST {"values": {...}} saves them, or {"action": "archive"} archives the room."""

    def get(self, request, public_id):
        room = _room(request, public_id)
        return JsonResponse({"room": {"code": room.code},
                             "property": {"id": str(room.property.public_id), "name": room.property.name},
                             "fields": _describe(forms.RoomEditForm(room=room)),
                             "post_url": reverse("properties:web_room", args=[room.public_id]),
                             "rooms_url": reverse("properties:rooms", args=[room.property.public_id])})

    def post(self, request, public_id):
        room = _room(request, public_id)
        body = _json_body(request)
        rooms_url = reverse("properties:rooms", args=[room.property.public_id])
        if body.get("action") == "archive":
            try:
                hostel.archive_room(request.membership, room, request=request)
            except ValidationError as exc:
                return JsonResponse({"error": "invalid", "message": " ".join(exc.messages)}, status=422)
            return JsonResponse({"message": _("Room archived."), "done": "archived", "redirect": rooms_url})
        form = forms.RoomEditForm(_values(body), room=room)
        if not form.is_valid():
            return _form_errors(form)
        try:
            hostel.update_room(request.membership, room, gender=form.cleaned_data["gender"],
                               capacity=form.cleaned_data["capacity"], request=request)
        except ValidationError as exc:
            return _clash(exc, form)
        return JsonResponse({"message": _("Room updated."), "done": "updated", "redirect": rooms_url})


def _student(lease) -> str:
    person = lease.primary_tenant
    return person.name if person else ""


def _term(request, prop: Property) -> dict:
    return {
        "property": {"id": str(prop.public_id), "name": prop.name},
        "fields": _describe(RolloverForm()),
        "students": [{"id": str(c["lease"].public_id), "name": _student(c["lease"]), "unit": c["lease"].unit.code,
                      "ends": c["lease"].end_date.isoformat(), "drafted": bool(c["has_draft"])}
                     for c in rollover.candidates(prop)],
        "drafts": [{"id": str(d.public_id), "name": _student(d), "unit": d.unit.code,
                    "starts": d.start_date.isoformat(), "ends": d.end_date.isoformat() if d.end_date else ""}
                   for d in rollover.drafts(prop)],
        "can_activate": can(request.membership, "leases.activate", prop),
        "post_url": reverse("properties:web_rollover", args=[prop.public_id]),
        "rooms_url": reverse("properties:rooms", args=[prop.public_id]),
    }


class RolloverDataView(MemberJSONView):
    """GET next term's students and waiting drafts. POST {"action": "draft", "values": {...}, "leases": [...]} drafts
    the renewals; {"action": "activate", "leases": [...]} activates drafts. Each is tried on its own."""

    def get(self, request, public_id):
        prop = _hostel_property(request, public_id)
        _need(request, "leases.draft", prop)
        return JsonResponse(_term(request, prop))

    def post(self, request, public_id):
        prop = _hostel_property(request, public_id)
        _need(request, "leases.draft", prop)
        body = _json_body(request)
        raw = body.get("leases")
        ids = [i for i in (uuid_or_none(v) for v in (raw if isinstance(raw, list) else [])) if i]
        if not ids:
            return JsonResponse({"error": "invalid", "message": _("Tick at least one.")}, status=422)
        if body.get("action") == "activate":
            _need(request, "leases.activate", prop)
            done, skipped = rollover.activate_drafts(request.membership, prop,
                                                     leases=list(rollover.drafts(prop).filter(public_id__in=ids)),
                                                     request=request)
            message = ngettext("%(n)s booking activated.", "%(n)s bookings activated.", len(done)) % {"n": len(done)}
        else:
            form = RolloverForm(_values(body))
            if not form.is_valid():
                return _form_errors(form)
            d = form.cleaned_data
            leases = list(Lease.objects.filter(unit__property=prop, public_id__in=ids, status=Lease.Status.ACTIVE)
                          .select_related("unit"))
            done, skipped = rollover.roll_over(request.membership, prop, leases=leases, start_date=d["start_date"],
                                               end_date=d["end_date"], rent=d["rent"], request=request)
            message = ngettext("%(n)s renewal draft made.", "%(n)s renewal drafts made.", len(done)) % {"n": len(done)}
        return JsonResponse({"message": message, "skipped": [f"{x.unit.code}: {why}" for x, why in skipped],
                             "term": _term(request, prop)})
