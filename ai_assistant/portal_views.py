"""The tenant portal's Ask page (D-094 items 2-4). Served under /portal/ with the portal's own login checks."""

from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from django.views import View

from core.money import format_money
from portal.views import PortalRequiredMixin, _no_store

from . import tenant
from .models import Question

TURNS_KEY = "tenant_assistant_turns"
CARDS_KEY = "tenant_assistant_cards"


def _caller(request) -> tenant.Caller:
    caller = tenant.portal_caller(request.user)
    if caller is None:
        raise Http404
    return caller


def _text(result: tenant.PortalResult) -> str:
    q = result.question
    if q.status == Question.Status.ANSWERED:
        return q.answer
    if q.status == Question.Status.LIMITED:
        return _("You have asked the most questions allowed today. Please try again tomorrow.")
    return _("Sorry, I could not answer just now. Please try again later or contact your landlord.")


def outcome_text(outcome: dict) -> str:
    if outcome.get("ok"):
        if outcome["kind"] == tenant.REPAIR:
            return _("Reported as %(n)s. We will keep you posted.") % {"n": outcome["number"]}
        return _("Check your phone for the M-Pesa prompt and enter your M-Pesa PIN to pay.")
    return {
        "wait": _("An M-Pesa prompt was sent less than two minutes ago. Please wait for it."),
        "too_many": _("You have asked for the most M-Pesa prompts allowed today. Please pay by Paybill instead."),
        "repair_limit": _("You have reported the most repairs allowed today. Please call the office."),
    }.get(outcome.get("why"), _("Sorry, that could not be done. Please contact your landlord."))


def _card(p: dict) -> dict:
    if p["kind"] == tenant.REPAIR:
        title = _("Report a repair")
        language = "sw" if (get_language() or "").startswith("sw") else "en"
        lines = [(_("Home"), p["home"]), (_("What is wrong"), p["what"]),
                 (_("Kind"), tenant.kind_label(p["repair_kind"], language)),
                 (_("Emergency"), _("Yes") if p["emergency"] else _("No"))]
    else:
        title = _("M-Pesa prompt")
        lines = [(_("Home"), p["home"]), (_("Amount"), format_money(Decimal(p["amount"]))), (_("To phone"), p["phone"])]
    return {**p, "title": title, "lines": lines, "expired": p.get("state") == "open" and tenant.expired(p)}


class TenantAskView(PortalRequiredMixin, View):
    template_name = "portal/ask.html"

    def get(self, request):
        _caller(request)
        cards = request.session.get(CARDS_KEY) or {}
        turns = [{**t, "card": _card(cards[t["card"]]) if t.get("card") in cards else None}
                 for t in request.session.get(TURNS_KEY) or []]
        return _no_store(render(request, self.template_name, {
            "turns": turns, "max_length": tenant.MAX_QUESTION, "daily": tenant.PORTAL_DAILY}))

    def post(self, request):
        if request.POST.get("action") == "new":
            request.session.pop(TURNS_KEY, None)
            request.session.pop(CARDS_KEY, None)
            return redirect("portal:ask")
        caller = _caller(request)
        past = request.session.get(TURNS_KEY) or []
        try:
            result = tenant.ask_portal(request.user, caller, request.POST.get("question"),
                                       history=[{"q": t["q"], "a": t["a"]} for t in past if t.get("ok")])
        except ValidationError:
            messages.error(request, _("Type a question of at most %(n)s characters.") % {"n": tenant.MAX_QUESTION})
            return redirect("portal:ask")
        proposal = result.proposal
        past.append({"q": result.question.question, "a": _text(result), "ok": result.answered,
                     "card": proposal["id"] if proposal else None})
        past = past[-tenant.HISTORY_TURNS:]
        kept = {t["card"] for t in past if t.get("card")}
        cards = {pid: p for pid, p in (request.session.get(CARDS_KEY) or {}).items() if pid in kept}
        if proposal:
            cards[proposal["id"]] = proposal
        request.session[TURNS_KEY] = past
        request.session[CARDS_KEY] = cards
        return redirect("portal:ask")


class TenantConfirmView(PortalRequiredMixin, View):
    """Confirm or dismiss the card (D-094 item 4). Used once; the details come from the session, not the form."""

    def post(self, request):
        caller = _caller(request)
        cards = request.session.get(CARDS_KEY) or {}
        proposal = cards.get(str(request.POST.get("id", "")))
        if proposal is None or proposal.get("state") != "open":
            messages.error(request, _("That is no longer available. Ask again."))
            return redirect("portal:ask")
        if request.POST.get("action") == "dismiss":
            proposal["state"] = "dismissed"
        elif tenant.expired(proposal):
            messages.error(request, _("This has expired. Ask again if it is still needed."))
        else:
            proposal["state"] = "running"
            request.session[CARDS_KEY] = cards
            request.session.save()
            outcome = tenant.confirm(caller, proposal)
            proposal["state"] = "done" if outcome["ok"] else "failed"
            proposal["result"] = outcome_text(outcome)
            (messages.success if outcome["ok"] else messages.error)(request, proposal["result"])
        request.session[CARDS_KEY] = cards
        return redirect("portal:ask")
