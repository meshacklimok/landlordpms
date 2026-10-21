"""JSON for the garbage collector's day sheet in the Next.js app (D-168, D-161); the rules live in leases.pickups."""

import datetime
import json

from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.permissions import can, visible_properties
from accounts.web_api import MemberJSONView
from properties.models import Property

from . import pickups
from .models import Pickup


def _day(value) -> datetime.date:
    try:
        return datetime.date.fromisoformat(value or "")
    except (TypeError, ValueError):
        return timezone.localdate()


def _route(request, public_id) -> Property:
    prop = (visible_properties(request.membership, Property.objects.all()).filter(public_id=public_id)
            .select_related("organization").first())
    if prop is None or prop.organization.org_type != "WASTE" or not can(request.membership, "meters.record", prop):
        raise Http404
    return prop


def _sheet(prop: Property, day: datetime.date) -> dict:
    today = timezone.localdate()
    marked = pickups.outcomes(prop, day)
    first = today.replace(day=1)
    return {
        "route": {"name": str(prop), "id": str(prop.public_id)},
        "words": {"unit": str(prop.organization.vocab["unit"]), "tenant": str(prop.organization.vocab["tenant"])},
        "day": day.isoformat(),
        "today": today.isoformat(),
        "month_start": first.isoformat(),
        "look_back_days": pickups.LOOK_BACK_DAYS,
        "rows": [{
            "id": lease.pk,
            "unit": lease.unit.code,
            "who": getattr(lease.primary_tenant, "name", "") or lease.number or "",
            "outcome": marked[lease.pk].outcome if lease.pk in marked else "",
        } for lease in pickups.route_leases(prop, day)],
        "missed": [{"id": lease.pk, "unit": lease.unit.code,
                    "who": getattr(lease.primary_tenant, "name", "") or lease.number or "",
                    "missed": missed, "done": done}
                   for lease, missed, done in pickups.missed_by_contract(prop, first, today)],
        "post_url": reverse("leases:web_pickups", args=[prop.public_id]),
    }


class PickupsDataView(MemberJSONView):
    """GET the sheet for a route and day; POST the marks. The capability and organization checks are in pickups.record."""

    def get(self, request, public_id):
        return JsonResponse(_sheet(_route(request, public_id), _day(request.GET.get("day"))))

    def post(self, request, public_id):
        prop = _route(request, public_id)
        try:
            body = json.loads(request.body or b"{}")
            day = datetime.date.fromisoformat(body["day"])
            marks = {int(k): str(v) for k, v in dict(body.get("marks", {})).items()}
        except (ValueError, KeyError, TypeError):
            return JsonResponse({"error": "bad_request", "message": _("That sheet could not be read.")}, status=400)
        if body.get("all_done"):
            marks = {lease.pk: marks.get(lease.pk) or Pickup.Outcome.DONE for lease in pickups.route_leases(prop, day)}
        try:
            counts = pickups.record(request.membership, prop, day, marks, request=request)
        except ValidationError as exc:
            return JsonResponse({"error": "invalid", "message": " ".join(exc.messages)}, status=422)
        message = _("Saved: %(done)s collected, %(missed)s missed.") % {"done": counts["DONE"], "missed": counts["MISSED"]}
        return JsonResponse({"message": message, "sheet": _sheet(prop, day)})
