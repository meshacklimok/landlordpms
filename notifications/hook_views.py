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

from core.http import ReplyThen

from . import sms_hooks, whatsapp_hooks
from .models import WhatsAppAccount
from .whatsapp_account import platform_number


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
        shared = platform_number()
        expected = shared.verify_token if shared is not None else getattr(settings, "WA_VERIFY_TOKEN", "")
        token = request.GET.get("hub.verify_token", "")
        if not expected or request.GET.get("hub.mode") != "subscribe" \
                or not hmac.compare_digest(token.encode(), expected.encode()):
            raise Http404
        return HttpResponse(request.GET.get("hub.challenge", ""), content_type="text/plain")

    def post(self, request):
        shared = platform_number()
        secret = shared.secret("app_secret") if shared is not None and shared.has("app_secret") else \
            getattr(settings, "WA_APP_SECRET", "")
        return _signed_delivery(request, secret)


def _signed_delivery(request, secret: str, organization=None):
    """A POST from Meta, checked against the app secret, then handled for the platform number or one landlord."""
    if not secret:
        raise Http404
    expected = "sha256=" + hmac.new(secret.encode(), request.body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(request.headers.get("X-Hub-Signature-256", "").encode(), expected.encode()):
        return HttpResponseForbidden()
    try:
        payload = json.loads(request.body.decode())
    except (ValueError, UnicodeDecodeError):
        return HttpResponseBadRequest()
    questions = whatsapp_hooks.process(payload, organization) if isinstance(payload, dict) else []
    if not questions:
        return HttpResponse("OK", content_type="text/plain")
    return ReplyThen("OK", content_type="text/plain", then=lambda: _answer(questions))


@method_decorator(csrf_exempt, name="dispatch")
class OrgWhatsAppHookView(View):
    """The webhook of one landlord's own WhatsApp number: the secret in the URL says whose it is, and the
    landlord's own app secret signs what Meta sends."""

    http_method_names = ["get", "post"]

    def _account(self, token) -> WhatsAppAccount:
        account = WhatsAppAccount.objects.filter(webhook_token=token, enabled=True).select_related(
            "organization").first()
        if account is None:
            raise Http404
        return account

    def get(self, request, token):
        account = self._account(token)
        given = request.GET.get("hub.verify_token", "")
        if request.GET.get("hub.mode") != "subscribe" or not hmac.compare_digest(
                given.encode(), account.verify_token.encode()):
            raise Http404
        return HttpResponse(request.GET.get("hub.challenge", ""), content_type="text/plain")

    def post(self, request, token):
        account = self._account(token)
        return _signed_delivery(request, account.secret("app_secret"), account.organization)


def _answer(questions: list[int]) -> None:
    """Tenants' questions are answered after Meta has its 200 (D-093 item 6)."""
    from ai_assistant import tenant

    for pk in questions:
        tenant.respond(pk)
