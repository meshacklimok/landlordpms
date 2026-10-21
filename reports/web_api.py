"""JSON for the hostel report in the Next.js app (D-196); the figures come from reports.hostel."""

from django.http import Http404, JsonResponse
from django.urls import reverse

from accounts.permissions import can
from accounts.web_api import MemberJSONView

from . import hostel
from .views import _finance_filters


class HostelReportDataView(MemberJSONView):
    """GET the hostel report, optionally for one property (?property=<public id>)."""

    def get(self, request):
        m = request.membership
        if m.organization.org_type != "HOSTEL" or not can(m, "reports.view_financial"):
            raise Http404
        prop, choices = _finance_filters(request)
        names = can(m, "tenants.view")
        rows = hostel.hostel_report(m, prop)
        return JsonResponse({
            "currency": getattr(m.organization, "currency", "KES"),
            "ending_within": hostel.ENDING_WITHIN,
            "show_names": names,
            "properties": [{"id": str(p.public_id), "name": p.name} for p in choices],
            "selected": str(prop.public_id) if prop else "",
            "csv_url": (reverse("reports:hostel") + f"?format=csv{'&property=' + str(prop.public_id) if prop else ''}")
            if can(m, "reports.export") else "",
            "rows": [{
                "property": {"id": str(r.prop.public_id), "name": r.prop.name},
                "rooms": r.rooms, "beds": r.beds, "taken": r.taken, "free": r.free,
                "occupancy": float(r.occupancy) if r.occupancy is not None else None,
                "billed": str(r.billed), "paid": str(r.paid), "owed": str(r.owed),
                "waiting": r.waiting_total, "ready": r.ready,
                "by_gender": [{"gender": g, "beds": c[0], "taken": c[1], "waiting": c[2]}
                              for g, c in r.by_gender.items()],
                "owing": [{"student": o.student if names else "", "guardian": o.guardian if names else "",
                           "guardian_phone": o.guardian_phone if names else "", "unit": o.unit,
                           "owed": str(o.owed), "days_late": o.days_late} for o in r.owing],
                "ending": [{"student": who if names else "", "unit": unit, "ends": end.isoformat(), "drafted": bool(d)}
                           for who, unit, end, d in r.ending],
                "rollover_url": reverse("properties:rollover", args=[r.prop.public_id]),
            } for r in rows],
        })
