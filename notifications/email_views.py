"""The public "stop these emails" page behind the link in every tenant email (D-076).

No login: the signed token is the key. The POST also takes the one-click request that mail apps
send for the List-Unsubscribe-Post header (RFC 8058), which carries no CSRF token.
"""

from django.http import HttpResponse
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from core import ratelimit
from core.net import client_ip

from . import catalog, emails, services

RATE_LIMIT = 30  # per IP per minute


def _page(request, template, context=None, status=200):
    response = render(request, template, context or {}, status=status)
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Referrer-Policy"] = "no-referrer"
    return response


@method_decorator(csrf_exempt, name="dispatch")
class EmailStopView(View):
    template_name = "notifications/email_stop.html"

    def dispatch(self, request, *args, **kwargs):
        if not ratelimit.hit(f"email_stop:ip:{client_ip(request)}", RATE_LIMIT, 60):
            return HttpResponse(_("Too many requests. Try again in a minute."), status=429)
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, token):
        tenant = emails.tenant_for_token(token)
        if tenant is None:
            return _page(request, self.template_name, {"unknown": True}, status=404)
        return _page(request, self.template_name, {
            "organization": tenant.organization.display_name,
            "stopped": not services.channel_allowed(tenant, catalog.EMAIL)})

    def post(self, request, token):
        tenant = emails.tenant_for_token(token)
        if tenant is None:
            return _page(request, self.template_name, {"unknown": True}, status=404)
        services.stop_email_by_link(tenant)
        return _page(request, self.template_name, {"organization": tenant.organization.display_name,
                                                   "stopped": True, "done": True})
