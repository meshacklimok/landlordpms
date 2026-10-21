"""JSON for the platform owner's notification settings in the Next.js app (D-168).

The console is not an organization's screen, so these views do not use MemberJSONView: they need a console seat, the
two-step login for this session, and a superuser (as OwnerMixin does), and they answer with status codes, not redirects.
"""

import json

from django.http import Http404, JsonResponse
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.cache import never_cache

from accounts import mfa
from audit import services as audit
from notifications import catalog
from notifications.models import NotificationTypeSetting

from . import access, forms


@method_decorator(never_cache, name="dispatch")
class OwnerJSONView(View):
    def dispatch(self, request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated or not user.phone_verified:
            return JsonResponse({"error": "signed_out", "login_url": reverse("accounts:login")}, status=401)
        if access.seat(user) is None:
            raise Http404
        if not mfa.is_enabled(user) or not mfa.session_verified(request):
            return JsonResponse({"error": "two_step", "login_url": reverse("accounts:login_mfa")}, status=403)
        if not user.is_superuser:
            return JsonResponse({"error": "owner_only"}, status=403)
        return super().dispatch(request, *args, **kwargs)


def _type(t, changed: set[str]) -> dict:
    return {"codename": t.codename, "label": str(t.label), "audience": str(t.audience),
            "channels": list(t.channels), "offsets": list(t.offsets), "enabled": t.enabled,
            "mandatory": t.mandatory, "urgent": t.urgent, "changed": t.codename in changed}


class NotificationTypesDataView(OwnerJSONView):
    def get(self, request):
        changed = set(NotificationTypeSetting.objects.values_list("type", flat=True))
        return JsonResponse({"types": [_type(t, changed) for t in catalog.types()]})


class NotificationTypeDataView(OwnerJSONView):
    """One type: GET its settings, POST {enabled, channels, offsets, mandatory, urgent} or {reset: true}."""

    @staticmethod
    def _base(codename):
        base = catalog.BY_CODENAME.get(codename)
        if base is None:
            raise Http404
        return base

    def get(self, request, codename):
        base = self._base(codename)
        changed = set(NotificationTypeSetting.objects.filter(type=codename).values_list("type", flat=True))
        return JsonResponse({"type": _type(catalog.get(codename), changed),
                             "available_channels": list(base.channels), "placeholders": list(base.placeholders)})

    def post(self, request, codename):
        base = self._base(codename)
        try:
            body = json.loads(request.body or b"{}")
        except ValueError:
            return JsonResponse({"error": "bad_request"}, status=400)
        if body.get("reset"):
            NotificationTypeSetting.objects.filter(type=codename).delete()
            catalog.clear_platform_settings()
            audit.record("platform.notification_type", actor=request.user, request=request,
                         changes={"type": codename, "reset": True})
            return JsonResponse({"message": _("Back to the built-in defaults.")})
        data = {"channels": ", ".join(body.get("channels") or []), "offsets": ", ".join(str(o) for o in body.get("offsets") or [])}
        for flag in ("enabled", "mandatory", "urgent"):
            if body.get(flag):
                data[flag] = "on"
        form = forms.NotificationTypeForm(base, data)
        if not form.is_valid():
            return JsonResponse({"error": "invalid", "fields": {k: [str(e) for e in v] for k, v in form.errors.items()}},
                                status=422)
        d = form.cleaned_data
        NotificationTypeSetting.objects.update_or_create(type=codename, defaults={
            "enabled": d["enabled"], "channels": d["channels"], "offsets": d["offsets"],
            "mandatory": d["mandatory"], "urgent": d["urgent"], "updated_by": request.user})
        catalog.clear_platform_settings()
        audit.record("platform.notification_type", actor=request.user, request=request,
                     changes={"type": codename, "enabled": d["enabled"], "channels": d["channels"],
                              "offsets": d["offsets"], "mandatory": d["mandatory"], "urgent": d["urgent"]})
        return JsonResponse({"message": _("Saved. It applies to the next message.")})
