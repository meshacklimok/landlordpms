"""Public status, health and security pages (D-061); help and support requests (D-063); Terms and Privacy (D-095)."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import redirect_to_login
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.cache import never_cache

from accounts import identity
from accounts.mixins import VerifiedUserRequiredMixin, no_seat_home
from ops import health

from . import help as help_topics
from . import legal, services, status
from .forms import SupportRequestForm


@method_decorator(never_cache, name="dispatch")
class HealthzView(View):
    """For the load balancer and uptime monitor. Names failing checks and nothing else."""

    def get(self, request):
        checks = health.checks()
        ok = all(checks.values())
        return JsonResponse({"status": "ok" if ok else "fail", "checks": {k: "ok" if v else "fail"
                                                                          for k, v in checks.items()}},
                            status=200 if ok else 503)


class StatusView(View):
    def get(self, request):
        return render(request, "support/status.html", status.page())


class SecurityView(View):
    def get(self, request):
        return render(request, "support/security.html", {
            "dpo_email": getattr(settings, "DATA_PROTECTION_EMAIL", ""),
            "hosting": getattr(settings, "HOSTING_LOCATION", ""),
        })


class TermsView(View):
    def get(self, request):
        return render(request, "support/terms.html", legal.details())


class PrivacyView(View):
    def get(self, request):
        return render(request, "support/privacy.html", legal.details())


class DeleteAccountView(View):
    """What deleting an account means, open to everyone (app stores ask for this page). Signed-in users can ask for
    it here; the request goes to staff, who confirm with the person and delete. Nothing is erased on the spot,
    because some records must be kept (Privacy Policy, section 6)."""

    def get(self, request):
        return render(request, "support/delete_account.html", self._context(request))

    def post(self, request):
        if not request.user.is_authenticated:
            return redirect_to_login(request.path)
        if request.POST.get("confirm", "").strip().upper() != "DELETE":
            messages.error(request, _("Type DELETE in the box to confirm."))
            return render(request, "support/delete_account.html", self._context(request))
        membership = getattr(request, "membership", None)
        item = services.create_request(
            request.user, membership.organization if membership else None, kind="DELETION",
            subject=_("Delete my account"),
            message=request.POST.get("reason", "")[:1000] or _("Please delete my account."),
            user_agent=request.META.get("HTTP_USER_AGENT", ""), request=request)
        messages.success(request, _("Your request number is %(n)s. We will contact you to confirm before anything "
                                    "is deleted.") % {"n": item.number})
        return redirect("support:delete_account")

    @staticmethod
    def _context(request):
        return {"dpo_email": getattr(settings, "DATA_PROTECTION_EMAIL", ""),
                "support_email": getattr(settings, "SUPPORT_EMAIL", ""),
                "is_owner": bool(getattr(request, "membership", None))}


class AcceptTermsView(LoginRequiredMixin, View):
    """The "I accept" button on the notice about new Terms (D-095 item 5)."""

    def post(self, request):
        identity.accept_terms(request.user, request=request)
        messages.success(request, _("Thank you. Your acceptance is recorded."))
        target = request.POST.get("next", "")
        if not url_has_allowed_host_and_scheme(target, {request.get_host()}, require_https=request.is_secure()):
            target = "/"
        return redirect(target)


def _support_context():
    whatsapp = "".join(ch for ch in getattr(settings, "SUPPORT_WHATSAPP", "") if ch.isdigit())
    return {"support_whatsapp": whatsapp, "support_email": getattr(settings, "SUPPORT_EMAIL", ""),
            "ussd_code": getattr(settings, "USSD_CODE", "")}


class HelpIndexView(VerifiedUserRequiredMixin, View):
    def get(self, request):
        return render(request, "support/help_index.html", {"topics": help_topics.TOPICS, **_support_context()})


class HelpTopicView(VerifiedUserRequiredMixin, View):
    def get(self, request, slug):
        topic = help_topics.topic(slug)
        if topic is None:
            raise Http404
        return render(request, f"support/help/{slug}.html", {"topic_slug": slug, "topic_title": topic[1],
                                                         "topics": help_topics.TOPICS, **_support_context()})


class ContactView(VerifiedUserRequiredMixin, View):
    """For members of an organization, even a frozen or read-only one: that is when they need us most.
    Tenants in the portal contact their landlord instead."""

    template_name = "support/contact.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.phone_verified and request.membership is None:
            return redirect(no_seat_home(request.user))
        return super().dispatch(request, *args, **kwargs)

    def _page(self, request):
        came_from = request.GET.get("from") or request.META.get("HTTP_REFERER", "")
        if came_from and url_has_allowed_host_and_scheme(came_from, {request.get_host()}, request.is_secure()):
            return came_from[:300]
        return ""

    def get(self, request):
        initial = {"page": self._page(request)}
        if request.GET.get("kind") in dict(services.SupportRequest.Kind.choices):
            initial["kind"] = request.GET["kind"]
        return render(request, self.template_name, {"form": SupportRequestForm(initial=initial),
                                                    **_support_context()})

    def post(self, request):
        form = SupportRequestForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, **_support_context()})
        data = form.cleaned_data
        item = services.create_request(
            request.user, request.membership.organization, kind=data["kind"], subject=data["subject"],
            message=data["message"], attachment=data.get("attachment"), page=data.get("page", ""),
            user_agent=request.META.get("HTTP_USER_AGENT", ""), request=request)
        messages.success(request, _("Thank you. Your request number is %(n)s; we reply by phone or email.")
                         % {"n": item.number})
        return redirect("support:contact")
