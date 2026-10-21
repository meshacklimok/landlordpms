"""JSON for the bank screens drawn by Next.js (D-215): import a statement, match its columns, check and apply it, and
the bank inbox.

The rules stay in `banking.services` and `banking.inbox`; these views only read the request and shape the answer.
Every view needs `mpesa.match`, as the Django pages do, and a member without it gets the same answer as a page
that is not there.
"""

import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, JsonResponse
from django.urls import reverse
from django.utils.translation import gettext as _

from accounts.permissions import can
from accounts.web_api import MemberJSONView
from core.money import format_money
from leases.models import Lease

from . import inbox, scan, services
from .models import StatementImport
from .views import FIELDS, _uuid

Status = StatementImport.Status
INBOX_ROWS = 200
PREVIEW_ROWS = 500


def _errors(exc: ValidationError) -> JsonResponse:
    fields = {k: [str(m) for m in v] for k, v in exc.message_dict.items()} if hasattr(exc, "error_dict") else {}
    message = " ".join(m for v in fields.values() for m in v) if fields else " ".join(exc.messages)
    return JsonResponse({"error": "invalid", "fields": fields, "message": message}, status=422)


def _json(request) -> dict | None:
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


class BankJSONView(MemberJSONView):
    """Signed-in staff who may match bank and M-Pesa lines."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and getattr(request, "membership", None) is not None \
                and not can(request.membership, "mpesa.match"):
            return JsonResponse({"error": "owner_only"}, status=403)
        return super().dispatch(request, *args, **kwargs)

    def batch(self, request, public_id) -> StatementImport:
        batch = services.visible_imports(request.membership).filter(public_id=public_id).first()
        if batch is None:
            raise Http404
        return batch


def _summary(batch: StatementImport) -> dict:
    return {
        "id": str(batch.public_id), "file_name": batch.file_name or batch.get_kind_display(),
        "kind": batch.kind, "kind_label": batch.get_kind_display(), "status": batch.status,
        "status_label": batch.get_status_display(), "account": batch.payment_account.display_name,
        "created_at": batch.created_at.isoformat(), "created_by": str(batch.created_by),
        "new": batch.new_count, "matched": batch.matched_count, "scanned": batch.source == StatementImport.Source.SCAN,
        "url": _where(batch),
    }


def _where(batch: StatementImport) -> str:
    name = "banking:import_map" if batch.status == Status.MAPPING and not services.is_expired(batch) \
        else "banking:import_detail"
    return reverse(name, args=[batch.public_id])


class ImportDataView(BankJSONView):
    """GET the accounts and recent imports; POST a file (multipart) to read it."""

    def get(self, request):
        m = request.membership
        accounts = services.importable_accounts(m)
        return JsonResponse({
            "accounts": [{"id": a.pk, "name": f"{a.display_name} ({a.get_type_display()})", "bank": a.type == "BANK"}
                         for a in accounts],
            "imports": [_summary(b) for b in services.visible_imports(m)[:20]],
            "can_add_bank": can(m, "payment_accounts.manage"), "accounts_url": reverse("banking:accounts"),
            "scan_available": scan.available(m), "post_url": reverse("banking:web_import"),
        })

    def post(self, request):
        m = request.membership
        account = services.importable_accounts(m).filter(pk=_pk(request.POST.get("account"))).first()
        upload = request.FILES.get("file")
        problems = {}
        if account is None:
            problems["account"] = [_("Choose an account.")]
        if upload is None:
            problems["file"] = [_("Choose a statement file.")]
        if problems:
            return JsonResponse({"error": "invalid", "fields": problems,
                                 "message": _("Check the fields marked below.")}, status=422)
        try:
            batch = services.preview(m, account, upload, scanned=request.POST.get("scanned") in ("on", "true", "1"),
                                     request=request)
        except ValidationError as e:
            return JsonResponse({"error": "invalid", "fields": {"file": [str(x) for x in e.messages]},
                                 "message": " ".join(e.messages)}, status=422)
        return JsonResponse({"id": str(batch.public_id), "status": batch.status,
                             "map": batch.status == Status.MAPPING, "url": _where(batch)})


def _pk(value) -> int:
    return int(value) if str(value or "").isdigit() else 0


class ImportDetailDataView(BankJSONView):
    """GET one import with its lines and what could not be read; POST {action: apply|discard}."""

    def get(self, request, public_id):
        batch = self.batch(request, public_id)
        expired = services.is_expired(batch)
        waiting = batch.status == Status.MAPPING and not expired
        return JsonResponse({
            **_summary(batch), "waiting_for_columns": waiting, "map_url": _where(batch) if waiting else "",
            "expired": expired, "currency": request.organization.currency, "duplicates": batch.duplicate_count,
            "skipped": batch.skipped_count, "unmatched": batch.unmatched_count,
            "errors": [{"line": e[0], "message": str(e[1])} for e in batch.errors],
            "rows": [{"line": r.get("line"), "date": r.get("posted_on") or str(r.get("paid_at", ""))[:16],
                      "description": r.get("description") or r.get("receipt") or "",
                      "reference": r.get("reference") or r.get("bill_ref") or "", "payer": r.get("payer_name") or "",
                      "amount": format_money(r.get("amount"), request.organization.currency)}
                     for r in batch.rows[:PREVIEW_ROWS]],
            "more": max(0, len(batch.rows) - PREVIEW_ROWS),
            "inbox_url": reverse("banking:inbox" if batch.kind == StatementImport.Kind.BANK else "mpesa:inbox"),
            "import_url": reverse("banking:import"),
            "post_url": reverse("banking:web_import_detail", args=[batch.public_id]),
        })

    def post(self, request, public_id):
        batch = self.batch(request, public_id)
        body = _json(request)
        if body is None:
            return JsonResponse({"error": "bad_request"}, status=400)
        try:
            if body.get("action") == "discard":
                services.discard(request.membership, batch)
                return JsonResponse({"message": _("Discarded. Nothing was imported."),
                                     "redirect": reverse("banking:import")})
            batch = services.apply(request.membership, batch, request=request)
        except PermissionDenied:
            raise Http404 from None
        except ValidationError as e:
            return _errors(e)
        return JsonResponse({"message": _("Imported %(n)s lines: %(m)s matched and receipted, %(u)s waiting in the "
                                          "inbox.") % {"n": batch.new_count, "m": batch.matched_count,
                                                        "u": batch.unmatched_count}})


class ImportMapDataView(BankJSONView):
    """GET the headers and sample rows of a statement waiting for its columns; POST {field: header} to read it."""

    def get(self, request, public_id):
        batch = self.batch(request, public_id)
        try:
            page = services.mapping_page(request.membership, batch)
        except ValidationError as e:
            return _errors(e)
        return JsonResponse({
            "id": str(batch.public_id), "file_name": batch.file_name, "account": batch.payment_account.display_name,
            "headers": [h for h in page["headers"]], "samples": [[str(c) for c in row] for row in page["samples"]],
            "fields": [{"key": k, "label": str(label), "help": str(help_text), "value": page["saved"].get(k, "")}
                       for k, label, help_text in FIELDS],
            "import_url": reverse("banking:import"),
            "post_url": reverse("banking:web_import_map", args=[batch.public_id]),
        })

    def post(self, request, public_id):
        batch = self.batch(request, public_id)
        body = _json(request)
        if body is None:
            return JsonResponse({"error": "bad_request"}, status=400)
        chosen = {key: str(body.get(key) or "") for key, _label, _help in FIELDS}
        try:
            batch = services.map_columns(request.membership, batch, chosen, request=request)
        except PermissionDenied:
            raise Http404 from None
        except ValidationError as e:
            return _errors(e)
        return JsonResponse({"redirect": reverse("banking:import_detail", args=[batch.public_id])})


class InboxDataView(BankJSONView):
    """GET the unmatched bank lines; POST {line, action: accept|match|ignore, lease?, reason?}."""

    def get(self, request):
        items = inbox.inbox(request.membership)
        rows = list(items[:INBOX_ROWS])
        currency = request.organization.currency
        count = items.count()
        return JsonResponse({
            "count": count, "more": count > len(rows), "post_url": reverse("banking:web_inbox"),
            "lines_url": reverse("banking:lines"), "import_url": reverse("banking:import"),
            "rows": [{
                "id": str(line.public_id), "description": line.description or line.reference or "",
                "date": line.posted_on.isoformat(), "account": line.payment_account.display_name,
                "reference": line.reference, "note": line.note, "amount": format_money(line.amount, currency),
                "url": reverse("banking:line", args=[line.public_id]),
                "suggested": None if line.suggested_lease is None else {
                    "lease": line.suggested_lease.number, "unit": line.suggested_lease.unit.code,
                    "property": line.suggested_lease.unit.property.name},
            } for line in rows],
        })

    def post(self, request):
        m = request.membership
        body = _json(request)
        if body is None:
            return JsonResponse({"error": "bad_request"}, status=400)
        line = inbox.inbox(m).filter(public_id=_uuid(str(body.get("line", "")))).first()
        if line is None:
            raise Http404
        action = body.get("action")
        try:
            if action == "accept":
                payment = inbox.accept_suggestion(m, line, request=request)
            elif action == "match":
                lease = Lease.objects.filter(organization=m.organization,
                                             public_id=_uuid(str(body.get("lease", "")))).first()
                if lease is None:
                    raise Http404
                payment = inbox.match(m, line, lease, request=request)
            elif action == "ignore":
                inbox.ignore(m, line, reason=str(body.get("reason", "")), request=request)
                return JsonResponse({"message": _("Ignored. It stays in the list of bank lines.")})
            else:
                return JsonResponse({"error": "bad_request"}, status=400)
        except PermissionDenied:
            raise Http404 from None
        except ValidationError as e:
            return _errors(e)
        return JsonResponse({"message": _("Matched to %(lease)s, confirmed and receipted.")
                             % {"lease": payment.lease.number}})
