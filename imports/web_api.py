"""JSON for the "move to NyumbaPMS" guide in the Next.js app (D-168)."""

from django.http import JsonResponse

from accounts.permissions import can
from accounts.web_api import MemberJSONView

from . import switch
from .views import _kinds


class SwitchGuideDataView(MemberJSONView):
    """The six steps of moving over, each marked done from the data, and which of them this member may use."""

    def get(self, request):
        m = request.membership
        return JsonResponse({
            "steps": [{"title": str(s.title), "text": str(s.text), "url": s.url, "action": str(s.action),
                       "done": bool(s.done)} for s in switch.steps(request.organization)],
            "can_import": bool(_kinds(m)),
            "can_announce": can(m, "messages.send_bulk"),
        })
