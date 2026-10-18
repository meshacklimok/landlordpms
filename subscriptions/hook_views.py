"""Callbacks for money paid to our own accounts (D-126): /hooks/plat/<token>/<confirm|validate|stk|ps>/.

Daraja does not sign, so PLATFORM_CALLBACK_TOKEN in the URL (and, optionally, MPESA_ALLOWED_IPS) is the
proof of origin. Paystack signs with our secret key, which is checked as well. The path avoids words
Daraja refuses, such as "mpesa".
"""

import hmac
import json
import logging

from django.conf import settings
from django.http import Http404, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from core.net import client_ip
from mpesa.c2b import BadCallback as MpesaBadCallback
from subscriptions import paystack

from . import collect

logger = logging.getLogger(__name__)

ACCEPTED = {"ResultCode": 0, "ResultDesc": "Accepted"}
REJECTED = {"ResultCode": "C2B00016", "ResultDesc": "Rejected"}


@method_decorator(csrf_exempt, name="dispatch")
class PlatformHookView(View):
    http_method_names = ["post"]
    kinds = ("confirm", "validate", "stk", "ps")

    def post(self, request, token, kind):
        expected = settings.PLATFORM_CALLBACK_TOKEN
        if not expected or kind not in self.kinds or not hmac.compare_digest(token.encode(), expected.encode()):
            raise Http404
        allowed = settings.PAYSTACK_ALLOWED_IPS if kind == "ps" else settings.MPESA_ALLOWED_IPS
        if allowed and client_ip(request) not in allowed:
            logger.warning("Platform payment callback (%s) from %s refused", kind, client_ip(request))
            raise Http404
        if kind == "ps":
            return self._paystack(request)
        try:
            payload = json.loads(request.body.decode() or "{}")
        except (ValueError, UnicodeDecodeError):
            payload = None
        if not isinstance(payload, dict):
            return JsonResponse(REJECTED, status=400)
        try:
            if kind == "confirm":
                collect.receive_c2b(payload)
            elif kind == "stk":
                collect.receive_stk(payload)
        except (collect.BadCallback, MpesaBadCallback):
            logger.warning("Platform %s callback could not be read", kind)
            return JsonResponse(REJECTED, status=400)
        # Validation is on only if Safaricom turns it on; every payment is accepted and sorted out after.
        return JsonResponse(ACCEPTED)

    def _paystack(self, request):
        if not paystack.signature_ok(settings.PLATFORM_PAYSTACK_SECRET_KEY, request.body,
                                     request.headers.get("x-paystack-signature", "")):
            logger.warning("Platform Paystack webhook with a bad signature")
            return JsonResponse({"status": "bad signature"}, status=401)
        try:
            payload = json.loads(request.body.decode() or "{}")
        except (ValueError, UnicodeDecodeError):
            payload = None
        if not isinstance(payload, dict):
            return JsonResponse({"status": "not json"}, status=400)
        if payload.get("event") in ("charge.success", "charge.failed"):
            try:
                collect.receive_paystack(payload.get("data"), payload)
            except collect.BadCallback:
                logger.warning("Platform Paystack event without a transaction")
                return JsonResponse({"status": "rejected"}, status=400)
        return JsonResponse({"status": "ok"})
