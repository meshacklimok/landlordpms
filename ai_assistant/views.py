"""The Ask page (D-091): a conversation kept in the session, answers from the assistant's tools.

Proposals (D-092) are kept in the session too, by id; the browser only ever sends that id back.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views import View

from accounts.mixins import CapabilityRequiredMixin, OrgMemberRequiredMixin
from accounts.permissions import can
from properties.views import _apply_errors

from . import actions, client, forms, knowledge, services

SESSION_KEY = "assistant_turns"
PROPOSALS_KEY = "assistant_proposals"


def turns(request) -> list[dict]:
    value = request.session.get(SESSION_KEY)
    return value if isinstance(value, list) else []


def proposals(request) -> dict:
    value = request.session.get(PROPOSALS_KEY)
    return value if isinstance(value, dict) else {}


def _with_cards(request) -> list[dict]:
    stored, out = proposals(request), []
    for turn in turns(request):
        cards = [{**stored[pid], "expired": stored[pid].get("state") == "open" and actions.is_expired(stored[pid])}
                 for pid in turn.get("proposals") or [] if pid in stored]
        out.append({**turn, "cards": cards})
    return out


def new_conversation(request) -> None:
    request.session.pop(SESSION_KEY, None)
    request.session.pop(PROPOSALS_KEY, None)


def ask_question(request, question) -> None:
    """Put one question to the assistant and keep the turn. ValidationError carries what to tell the member."""
    m = request.membership
    if not services.is_on(m.organization):
        raise PermissionDenied("The assistant is off.")
    past = turns(request)
    result = services.ask(m, question, history=[{"q": t["q"], "a": t["a"]} for t in past if t.get("ok")])
    past.append({"q": result.question.question, "a": result.text, "ok": result.answered,
                 "links": [list(link) for link in result.links],
                 "proposals": [p["id"] for p in result.proposals]})
    past = past[-services.HISTORY_TURNS:]
    kept = {pid for t in past for pid in t.get("proposals") or []}
    stored = {pid: p for pid, p in proposals(request).items() if pid in kept}
    stored.update({p["id"]: p for p in result.proposals})
    request.session[SESSION_KEY] = past
    request.session[PROPOSALS_KEY] = stored


def settle_proposal(request, proposal_id, action, text=None) -> str:
    """Confirm or dismiss one proposal (D-092 items 2-4); returns what to tell the member.

    LookupError when the proposal is gone, ValidationError or PermissionDenied when it cannot be done.
    """
    m = request.membership
    if not services.is_on(m.organization):
        raise PermissionDenied("The assistant is off.")
    stored = proposals(request)
    proposal = stored.get(str(proposal_id or ""))
    if proposal is None:
        raise LookupError(_("That proposal is no longer available. Ask again."))
    message = ""
    if action == "dismiss":
        if proposal.get("state") == "open":
            proposal["state"] = "dismissed"
    else:
        text = text if proposal.get("kind") == actions.MESSAGE else None
        done, url = actions.confirm(m, proposal, text=text, request=request)
        proposal.update(state="done", result=done, url=url)
        if text is not None:
            proposal["text"] = text.strip()
        message = done
    request.session[PROPOSALS_KEY] = stored
    return message


def switch_assistant(request, which: str, on: bool) -> str:
    """Turn the assistant for staff, or for tenants, on or off; returns what to tell the member."""
    if which == "tenants":
        if on and not services.tenants_available():
            raise ValidationError(_("Answering tenants is not set up on this service yet."))
        services.set_tenant_enabled(request.membership, on, request=request)
        return _("The assistant now answers tenants.") if on else _("The assistant no longer answers tenants.")
    if on and not client.enabled():
        raise ValidationError(_("The assistant is not set up on this service yet."))
    services.set_enabled(request.membership, on, request=request)
    return _("The assistant is on.") if on else _("The assistant is off.")


class AskView(OrgMemberRequiredMixin, View):
    template_name = "ai_assistant/ask.html"

    def get(self, request):
        m = request.membership
        return render(request, self.template_name, {
            "platform_on": client.enabled(), "is_on": services.is_on(m.organization),
            "tenants_available": services.tenants_available(),
            "tenants_whatsapp": services.whatsapp_available(m.organization),
            "tenants_on": m.organization.tenant_assistant_enabled,
            "can_switch": can(m, "organization.manage"), "turns": _with_cards(request),
            "recent": services.recent(m), "max_length": services.MAX_QUESTION,
            "user_daily": services.USER_DAILY, "knowledge_count": knowledge.entries(m).count(),
        })

    def post(self, request):
        if request.POST.get("action") == "new":
            new_conversation(request)
            return redirect("ai_assistant:ask")
        try:
            ask_question(request, request.POST.get("question"))
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect("ai_assistant:ask")


class ConfirmView(OrgMemberRequiredMixin, View):
    """Confirm or dismiss one proposal (D-092 items 2-4)."""

    def post(self, request):
        try:
            done = settle_proposal(request, request.POST.get("id"), request.POST.get("action"),
                                   request.POST.get("text"))
        except LookupError as exc:
            messages.error(request, str(exc))
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        except PermissionDenied as exc:
            if str(exc) == "The assistant is off.":
                raise
            messages.error(request, _("Your role cannot do that."))
        else:
            if done:
                messages.success(request, done)
        return redirect("ai_assistant:ask")


class SwitchView(CapabilityRequiredMixin, View):
    required_capability = "organization.manage"

    def post(self, request):
        which = "tenants" if request.POST.get("which") == "tenants" else "staff"
        try:
            messages.success(request, switch_assistant(request, which, request.POST.get("on") == "1"))
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect("ai_assistant:ask")


class KnowledgeView(CapabilityRequiredMixin, View):
    """What the organization wants the assistant to know (D-119): the list, and adding an entry."""

    template_name = "ai_assistant/knowledge.html"
    required_capability = "organization.manage"

    def render_page(self, request, form, status=200):
        m = request.membership
        return render(request, self.template_name, {
            "entries": knowledge.entries(m), "form": form, "max_entries": knowledge.MAX_ENTRIES,
            "tenants_on": m.organization.tenant_assistant_enabled}, status=status)

    def get(self, request):
        return self.render_page(request, forms.KnowledgeForm(membership=request.membership))

    def post(self, request):
        form = forms.KnowledgeForm(request.POST, membership=request.membership)
        if form.is_valid():
            try:
                knowledge.save(request.membership, form.instance, request=request)
            except ValidationError as exc:
                _apply_errors(form, exc)
            else:
                messages.success(request, _("Added. The assistant uses it from the next question."))
                return redirect("ai_assistant:knowledge")
        return self.render_page(request, form, status=400)


class KnowledgeEntryView(CapabilityRequiredMixin, View):
    template_name = "ai_assistant/knowledge_entry.html"
    required_capability = "organization.manage"

    def entry(self, request, public_id):
        return get_object_or_404(knowledge.entries(request.membership), public_id=public_id)

    def get(self, request, public_id):
        entry = self.entry(request, public_id)
        return render(request, self.template_name, {
            "entry": entry, "form": forms.KnowledgeForm(instance=entry, membership=request.membership)})

    def post(self, request, public_id):
        entry = self.entry(request, public_id)
        if request.POST.get("action") == "delete":
            knowledge.delete(request.membership, entry, request=request)
            messages.success(request, _("Removed. The assistant no longer uses it."))
            return redirect("ai_assistant:knowledge")
        form = forms.KnowledgeForm(request.POST, instance=entry, membership=request.membership)
        if form.is_valid():
            try:
                knowledge.save(request.membership, form.instance, request=request)
            except ValidationError as exc:
                _apply_errors(form, exc)
            else:
                messages.success(request, _("Saved."))
                return redirect("ai_assistant:knowledge")
        return render(request, self.template_name, {"entry": entry, "form": form}, status=400)
