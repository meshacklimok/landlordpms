"""Staff → Webhooks (D-083 item 2)."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views import View

from accounts.mixins import CapabilityRequiredMixin

from . import webhooks
from .forms import WebhookEventsForm, WebhookForm
from .models import WebhookDelivery

RECENT_DELIVERIES = 50


def _no_store(response):
    response["Cache-Control"] = "no-store"
    return response


class WebhookListView(CapabilityRequiredMixin, View):
    template_name = "api/webhooks.html"
    required_capability = webhooks.MANAGE

    def _render(self, request, form, secret=None, created=None):
        endpoints = list(webhooks.visible_endpoints(request.membership))
        for endpoint in endpoints:
            endpoint.blocked = webhooks.blocked_reason(endpoint)
        return _no_store(render(request, self.template_name, {
            "form": form, "secret": secret, "created": created, "endpoints": endpoints,
            "max_endpoints": webhooks.MAX_ENDPOINTS}))

    def get(self, request):
        return self._render(request, WebhookForm())

    def post(self, request):
        form = WebhookForm(request.POST)
        if form.is_valid():
            try:
                endpoint, secret = webhooks.create_endpoint(request.membership, request=request,
                                                            **form.cleaned_data)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                return self._render(request, WebhookForm(), secret=secret, created=endpoint)
        return self._render(request, form)


class WebhookDetailView(CapabilityRequiredMixin, View):
    template_name = "api/webhook_detail.html"
    required_capability = webhooks.MANAGE

    def get(self, request, public_id, form=None):
        endpoint = get_object_or_404(webhooks.visible_endpoints(request.membership), public_id=public_id)
        return _no_store(render(request, self.template_name, {
            "endpoint": endpoint, "blocked": webhooks.blocked_reason(endpoint),
            "form": form or WebhookEventsForm(initial={"events": endpoint.events}),
            "deliveries": endpoint.deliveries.all()[:RECENT_DELIVERIES],
            "auto_pause_after": webhooks.AUTO_PAUSE_AFTER}))

    def post(self, request, public_id):
        endpoint = get_object_or_404(webhooks.visible_endpoints(request.membership), public_id=public_id)
        form = WebhookEventsForm(request.POST)
        if form.is_valid():
            webhooks.update_events(request.membership, endpoint, form.cleaned_data["events"], request=request)
            messages.success(request, _("Events saved."))
            return redirect("api:webhook", public_id=endpoint.public_id)
        return self.get(request, public_id, form=form)


class WebhookActionView(CapabilityRequiredMixin, View):
    required_capability = webhooks.MANAGE
    ACTIONS = {"pause", "resume", "ping", "delete"}

    def post(self, request, public_id, action):
        if action not in self.ACTIONS:
            raise Http404
        endpoint = get_object_or_404(webhooks.visible_endpoints(request.membership), public_id=public_id)
        member = request.membership
        try:
            if action == "delete":
                webhooks.delete_endpoint(member, endpoint, request=request)
                messages.success(request, _("Webhook deleted."))
                return redirect("api:webhooks")
            if action == "pause":
                webhooks.pause(member, endpoint, request=request)
                messages.success(request, _("Webhook paused. Events are kept and sent when you resume."))
            elif action == "resume":
                webhooks.resume(member, endpoint, request=request)
                messages.success(request, _("Webhook resumed."))
            else:
                delivery = webhooks.ping(member, endpoint, request=request)
                if delivery.status == WebhookDelivery.Status.SENT:
                    messages.success(request, _("Test event delivered (%(code)s).")
                                     % {"code": delivery.last_status_code})
                else:
                    messages.error(request, _("Test event failed: %(error)s") % {"error": delivery.last_error})
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect("api:webhook", public_id=endpoint.public_id)


class RedeliverView(CapabilityRequiredMixin, View):
    required_capability = webhooks.MANAGE

    def post(self, request, public_id):
        delivery = get_object_or_404(
            WebhookDelivery.objects.filter(organization=request.membership.organization)
            .select_related("endpoint"), public_id=public_id)
        try:
            delivery = webhooks.redeliver(request.membership, delivery, request=request)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            if delivery.status == WebhookDelivery.Status.SENT:
                messages.success(request, _("Delivered."))
            else:
                messages.error(request, _("Still failing: %(error)s. It will be tried again.")
                               % {"error": delivery.last_error})
        return redirect("api:webhook", public_id=delivery.endpoint.public_id)
