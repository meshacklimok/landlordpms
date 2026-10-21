"""JSON for the New announcement screen drawn by Next.js (D-214).

The rules stay Django's: `forms.AnnouncementForm` validates, `announcements.preview` and `announcements.send`
decide who is reached, and the form's nonce keeps a double press from sending twice.
"""

import json
import uuid

from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.urls import reverse
from django.utils.translation import gettext as _

from accounts.permissions import can
from accounts.web_api import MemberJSONView

from . import announcements, forms

# Rows of the preview table sent to the browser; the totals above it always cover everyone.
PREVIEW_ROWS = 200


def _invalid(form) -> JsonResponse:
    fields = {k: [str(e) for e in v] for k, v in form.errors.items()}
    return JsonResponse({"error": "invalid", "fields": fields,
                         "message": " ".join(fields.get("__all__", [])) or _("Check the fields marked below.")},
                        status=422)


def _preview(preview) -> dict:
    people = [r for r in preview.recipients if not r.skip_reason]
    return {
        "sending": preview.sending, "whatsapp": preview.whatsapp, "email": preview.email, "parts": preview.parts,
        "cost": str(preview.cost), "price": str(announcements.price_per_part()),
        "large": preview.sending > announcements.BULK_INLINE,
        "skipped": [{"who": r.tenant.name, "unit": r.lease.unit.code, "reason": r.skip_label}
                    for r in preview.recipients if r.skip_reason],
        "recipients": [{"who": r.tenant.name, "unit": f"{r.lease.unit.property.name} · {r.lease.unit.code}",
                        "body": r.body, "channel": r.channel, "parts": r.parts} for r in people[:PREVIEW_ROWS]],
        "more": max(0, len(people) - PREVIEW_ROWS),
    }


class AnnouncementNewDataView(MemberJSONView):
    """GET what the form needs (who it can go to, ready-made wording, starting values); POST JSON to preview or send."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and getattr(request, "membership", None) is not None \
                and not can(request.membership, "messages.send_bulk"):
            return JsonResponse({"error": "owner_only"}, status=403)
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        m = request.membership
        template = request.GET.get("template", "")
        days = request.GET.get("days", "")
        form = forms.AnnouncementForm(membership=m)
        start = {"text": announcements.preset_text(template), "due": False, "due_within": 7, "owing": False,
                 "owing_days": 1, "ending": False, "ending_within": 60, "include_co_tenants": True, "urgent": False}
        if template == "rent_due":  # the reminder starts aimed at tenants whose rent falls due soon
            start["due"] = True
            start["due_within"] = int(days) if days.isdigit() and 1 <= int(days) <= 60 else 7
        return JsonResponse({
            "nonce": str(uuid.uuid4()), "start": start, "template": template,
            "properties": [{"id": str(p.pk), "name": p.name} for p in form.fields["properties"].queryset],
            "buildings": [{"id": str(b.pk), "name": form.fields["buildings"].label_from_instance(b)}
                          for b in form.fields["buildings"].queryset],
            "can_owing": "owing" in form.fields, "can_due": "due" in form.fields,
            "presets": [{"key": key, "label": str(label)} for key, label, _text in announcements.PRESETS],
            "fields": list(announcements.FIELDS), "org_name": request.organization.display_name,
            "inline_limit": announcements.BULK_INLINE, "post_url": reverse("notifications:web_announcement_new"),
            "list_url": reverse("notifications:announcements"),
        })

    def post(self, request):
        try:
            body = json.loads(request.body or b"{}")
        except ValueError:
            return JsonResponse({"error": "bad_request"}, status=400)
        data = {k: body.get(k) for k in ("text", "nonce", "owing_days", "due_within", "ending_within")}
        data["properties"] = [str(x) for x in body.get("properties") or []]
        data["buildings"] = [str(x) for x in body.get("buildings") or []]
        for flag in ("owing", "due", "ending", "include_co_tenants", "urgent"):
            if body.get(flag):
                data[flag] = "on"
        data = {k: v for k, v in data.items() if v is not None and v != ""}
        m = request.membership
        form = forms.AnnouncementForm(data, membership=m)
        if not form.is_valid():
            return _invalid(form)
        values = form.cleaned_data
        try:
            if body.get("action") != "send":
                return JsonResponse({"preview": _preview(announcements.preview(m, values["text"], form.audience()))})
            announcement, created = announcements.send(
                m, values["text"], form.audience(), nonce=values["nonce"], urgent=values.get("urgent", False),
                request=request)
        except ValidationError as exc:
            if hasattr(exc, "error_dict"):
                for field, errs in exc.message_dict.items():
                    for err in errs:
                        form.add_error(field if field in form.fields else None, err)
            else:
                form.add_error(None, " ".join(exc.messages))
            return _invalid(form)
        message = (_("Announcement sent to %(n)s tenants.") % {"n": announcement.recipient_count}) if created \
            else _("That announcement was already sent.")
        return JsonResponse({"message": message,
                             "redirect": reverse("notifications:announcement", args=[announcement.public_id])})
