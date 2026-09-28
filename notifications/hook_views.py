"""Provider callbacks. Africa's Talking (D-044 item 14) does not sign its requests, so a secret token in
the URL is the only proof of origin; Meta signs WhatsApp webhooks with the app secret (item 16)."""

import hashlib
import hmac
import json

from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from . import sms_hooks, whatsapp_hooks


@method_decorator(csrf_exempt, name="dispatch")
class AfricasTalkingHookView(View):
    http_method_names = ["post"]

    def post(self, request, token, kind):
        expected = getattr(settings, "AT_CALLBACK_TOKEN", "")
        if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
            raise Http404
        data = request.POST
        if kind == "delivery":
            sms_hooks.delivery_report(data.get("id", ""), data.get("status", ""), data.get("failureReason", ""))
        elif kind == "inbound":
            sms_hooks.inbound(data.get("from", ""), data.get("text", ""))
        elif kind == "optout":
            sms_hooks.network_opt_out(data.get("phoneNumber", ""))
        else:
            raise Http404
        return HttpResponse("OK", content_type="text/plain")


@method_decorator(csrf_exempt, name="dispatch")
class WhatsAppHookView(View):
    """Meta's webhook (D-044 item 16). GET is the one-time verification; POST must be signed with the app secret."""

    http_method_names = ["get", "post"]

    def get(self, request):
        expected = getattr(settings, "WA_VERIFY_TOKEN", "")
        token = request.GET.get("hub.verify_token", "")
        if not expected or request.GET.get("hub.mode") != "subscribe" \
                or not hmac.compare_digest(token.encode(), expected.encode()):
            raise Http404
        return HttpResponse(request.GET.get("hub.challenge", ""), content_type="text/plain")

    def post(self, request):
        secret = getattr(settings, "WA_APP_SECRET", "")
        if not secret:
            raise Http404
        expected = "sha256=" + hmac.new(secret.encode(), request.body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(request.headers.get("X-Hub-Signature-256", "").encode(), expected.encode()):
            return HttpResponseForbidden()
        try:
            payload = json.loads(request.body.decode())
        except (ValueError, UnicodeDecodeError):
            return HttpResponseBadRequest()
        if isinstance(payload, dict):
            whatsapp_hooks.process(payload)
        return HttpResponse("OK", content_type="text/plain")
