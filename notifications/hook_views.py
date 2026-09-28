"""Callbacks from Africa's Talking (D-044 item 14). No login and no CSRF: the secret token in the URL
is the only proof of origin, because the provider does not sign its requests."""

import hmac

from django.conf import settings
from django.http import Http404, HttpResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from . import sms_hooks


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
