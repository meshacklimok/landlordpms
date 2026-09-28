"""Daraja callbacks (D-045 item 3): /hooks/c2b/<token>/<confirm|validate|stk>/.

Daraja does not sign its requests, so the per-account token in the URL (and, optionally,
MPESA_ALLOWED_IPS) is the proof of origin. The path avoids words Daraja refuses, such as "mpesa".
"""

import json
import logging

from django.conf import settings
from django.http import Http404, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from core.net import client_ip

from . import c2b
from .models import DarajaCredentials

logger = logging.getLogger(__name__)

ACCEPTED = {"ResultCode": 0, "ResultDesc": "Accepted"}


@method_decorator(csrf_exempt, name="dispatch")
class DarajaHookView(View):
    http_method_names = ["post"]
    kinds = ("validate", "confirm")

    def post(self, request, token, kind):
        allowed = getattr(settings, "MPESA_ALLOWED_IPS", [])
        if allowed and client_ip(request) not in allowed:
            logger.warning("Daraja callback from %s refused", client_ip(request))
            raise Http404
        creds = DarajaCredentials.objects.filter(callback_token=token).select_related("payment_account").first()
        if creds is None or kind not in self.kinds:
            raise Http404
        try:
            payload = json.loads(request.body.decode() or "{}")
        except (ValueError, UnicodeDecodeError):
            return JsonResponse({"ResultCode": "C2B00016", "ResultDesc": "Rejected"}, status=400)
        if not isinstance(payload, dict):
            return JsonResponse({"ResultCode": "C2B00016", "ResultDesc": "Rejected"}, status=400)
        if kind == "confirm":
            try:
                c2b.receive(creds, payload)
            except c2b.BadCallback:
                logger.warning("Daraja confirmation without TransID for %s", creds.payment_account_id)
                return JsonResponse({"ResultCode": "C2B00016", "ResultDesc": "Rejected"}, status=400)
            # Stored (or already stored): Safaricom must not send it again.
            return JsonResponse(ACCEPTED)
        # Validation is enabled only on request from Safaricom; we accept every payment and
        # sort out wrong references afterwards (D-045 item 3).
        return JsonResponse(ACCEPTED)
