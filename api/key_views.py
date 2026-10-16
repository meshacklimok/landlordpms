"""Staff → API keys (D-082 item 2)."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views import View

from accounts.mixins import CapabilityRequiredMixin

from . import services
from .forms import ApiKeyForm


class KeyListView(CapabilityRequiredMixin, View):
    template_name = "api/keys.html"
    required_capability = services.MANAGE

    def _render(self, request, form, secret=None, created=None):
        keys = list(services.visible_keys(request.membership))
        response = render(request, self.template_name, {
            "form": form, "secret": secret, "created": created, "keys": keys,
            "live_count": sum(1 for k in keys if k.is_live), "max_keys": services.MAX_LIVE_KEYS,
            "api_base": request.build_absolute_uri("/api/v1/")})
        response["Cache-Control"] = "no-store"
        return response

    def get(self, request):
        return self._render(request, ApiKeyForm())

    def post(self, request):
        form = ApiKeyForm(request.POST)
        if form.is_valid():
            try:
                key, secret = services.create_key(request.membership, request=request, **form.cleaned_data)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                return self._render(request, ApiKeyForm(), secret=secret, created=key)
        return self._render(request, form)


class KeyRevokeView(CapabilityRequiredMixin, View):
    required_capability = services.MANAGE

    def post(self, request, public_id):
        key = get_object_or_404(services.visible_keys(request.membership), public_id=public_id)
        services.revoke_key(request.membership, key, request=request)
        messages.success(request, _("Key “%(name)s” revoked. It no longer works.") % {"name": key.name})
        return redirect("api:keys")
