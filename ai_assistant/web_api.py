"""JSON for the Ask page in the Next.js app (D-168): what the Django page showed, and its four actions."""

import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.urls import reverse
from django.utils.translation import gettext as _

from accounts.permissions import can
from accounts.web_api import MemberJSONView

from . import client, knowledge, services
from .models import StarterPrompt
from .views import _with_cards, ask_question, new_conversation, settle_proposal, switch_assistant


def _state(request) -> dict:
    m = request.membership
    org = m.organization
    return {
        "platform_on": client.enabled(), "is_on": services.is_on(org),
        "can_switch": can(m, "organization.manage"),
        "tenants_available": services.tenants_available(), "tenants_whatsapp": services.whatsapp_available(org),
        "tenants_on": org.tenant_assistant_enabled,
        "max_length": services.MAX_QUESTION, "user_daily": services.USER_DAILY,
        "knowledge_count": knowledge.entries(m).count(),
        "knowledge_url": reverse("ai_assistant:knowledge"),
        "turns": [{"q": t["q"], "a": t["a"], "ok": bool(t.get("ok")),
                   "links": [{"label": label, "url": url} for label, url in t.get("links") or []],
                   "cards": [{"id": c["id"], "kind": c["kind"], "title": c.get("title", ""), "lines": c["lines"],
                              "state": c.get("state", "open"), "text": c.get("text", ""),
                              "result": c.get("result", ""), "url": c.get("url", ""),
                              "expired": bool(c.get("expired"))} for c in t["cards"]]}
                  for t in _with_cards(request)],
        "recent": [{"question": q.question, "at": q.created_at.isoformat(),
                    "status": "" if q.status == "ANSWERED" else q.get_status_display()}
                   for q in services.recent(m)],
        "starters": list(StarterPrompt.objects.filter(is_active=True).values_list("text", flat=True)),
        "post_url": reverse("ai_assistant:web_ask"),
    }


class AskDataView(MemberJSONView):
    """GET the conversation and the switches. POST one of: ask, new, confirm, dismiss, switch."""

    def get(self, request):
        return JsonResponse(_state(request))

    def post(self, request):
        try:
            body = json.loads(request.body or b"{}")
        except ValueError:
            return JsonResponse({"error": "bad_request"}, status=400)
        action = body.get("action")
        message = ""
        try:
            if action == "ask":
                ask_question(request, body.get("question"))
            elif action == "new":
                new_conversation(request)
            elif action in ("confirm", "dismiss"):
                message = settle_proposal(request, body.get("id"), action, body.get("text"))
            elif action == "switch":
                if not can(request.membership, "organization.manage"):
                    return JsonResponse({"error": "owner_only"}, status=403)
                which = "tenants" if body.get("which") == "tenants" else "staff"
                message = switch_assistant(request, which, bool(body.get("on")))
            else:
                return JsonResponse({"error": "bad_request"}, status=400)
        except ValidationError as exc:
            return JsonResponse({"error": "invalid", "message": " ".join(exc.messages), "fields": {}}, status=422)
        except LookupError as exc:
            return JsonResponse({"error": "gone", "message": str(exc), "fields": {}}, status=422)
        except PermissionDenied:
            return JsonResponse({"error": "forbidden", "message": _("Your role cannot do that."), "fields": {}},
                                status=403)
        return JsonResponse({**_state(request), "message": message})
