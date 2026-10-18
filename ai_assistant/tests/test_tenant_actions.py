"""Tenants act through the assistant, and ask in the portal (D-094): nothing is done until they confirm."""
# ruff: noqa: F811  (fixtures shared with the D-093 tests)

import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from ai_assistant import tenant
from ai_assistant.client import FakeClient
from ai_assistant.models import Question
from ai_assistant.portal_views import CARDS_KEY
from ai_assistant.tests.test_tenant import ask, phone_of, texts
from audit.models import AuditEvent
from maintenance.models import MaintenanceRequest
from mpesa.daraja import FakeDarajaClient
from mpesa.models import StkRequest
from notifications.providers.whatsapp import MemoryWhatsAppSender
from portal.models import UssdPin
from portal.tests.test_portal import join
from portal.tests.test_ussd import make_paybill

pytestmark = pytest.mark.django_db

# The fixtures owner, prop and lease come from the D-093 tests.
from ai_assistant.tests.test_tenant import lease, owner, prop, wa  # noqa: E402, F401  isort: skip


@pytest.fixture
def paybill(owner, settings):
    settings.SITE_URL = "https://pms.example.com"
    return make_paybill(owner)


def repair_script(what="Kitchen tap leaking", **extra):
    return [FakeClient.tool("propose_repair", {"what": what, "kind": "PLUMBING", **extra}),
            FakeClient.text("I can report that for you.")]


# ---------------------------------------------------------------------------
# WhatsApp: reply YES (items 5-6)
# ---------------------------------------------------------------------------


def test_a_repair_is_reported_after_yes(lease):
    FakeClient.script = repair_script()
    question = ask(lease, "My kitchen tap is leaking, please report it")
    assert not MaintenanceRequest.objects.exists()
    [(_to, body)] = texts()
    assert body.startswith("I can report that for you.\n\n")
    assert "Kitchen tap leaking" in body and body.endswith("Reply YES to confirm.")
    question.refresh_from_db()
    assert question.proposal["state"] == "open" and question.proposal["kind"] == tenant.REPAIR

    assert ask(lease, "yes", "wamid.yes").pk == question.pk
    req = MaintenanceRequest.objects.get()
    assert (req.source, req.unit, req.tenant, req.description) == (
        MaintenanceRequest.Source.ASSISTANT, lease.unit, lease.primary_tenant, "Kitchen tap leaking")
    assert texts()[-1][1] == f"Reported as {req.number}. We will keep you posted."
    question.refresh_from_db()
    assert question.proposal["state"] == "done"
    event = AuditEvent.objects.get(action="assistant.tenant_confirmed")
    assert event.changes["via"] == [None, "whatsapp"]
    # The same YES again (a retry), or a second YES, does not report it twice.
    assert ask(lease, "yes", "wamid.yes") is None
    FakeClient.script = [FakeClient.text("Nothing to confirm.")]
    ask(lease, "YES", "wamid.yes2")
    assert MaintenanceRequest.objects.count() == 1


def test_another_message_ends_the_proposal(lease):
    FakeClient.script = repair_script() + [FakeClient.text("Sure."), FakeClient.text("Confirm what?")]
    first = ask(lease, "Report my tap")
    ask(lease, "Actually, what do I owe?", "wamid.2")
    first.refresh_from_db()
    assert first.proposal["state"] == "ended"
    yes = ask(lease, "ndiyo", "wamid.3")
    assert yes.pk != first.pk and yes.question == "ndiyo"
    assert not MaintenanceRequest.objects.exists()


def test_yes_after_ten_minutes_does_nothing(lease):
    FakeClient.script = repair_script() + [FakeClient.text("Please ask again.")]
    first = ask(lease, "Report my tap")
    Question.objects.filter(pk=first.pk).update(created_at=timezone.now() - datetime.timedelta(minutes=11))
    first.proposal["created"] = (timezone.now() - datetime.timedelta(minutes=11)).isoformat()
    Question.objects.filter(pk=first.pk).update(proposal=first.proposal)
    ask(lease, "YES", "wamid.late")
    assert not MaintenanceRequest.objects.exists()


def test_only_one_proposal_per_answer(lease):
    FakeClient.script = [FakeClient.tool("propose_repair", {"what": "Tap leaking"}),
                         FakeClient.tool("propose_repair", {"what": "Door broken"}),
                         FakeClient.text("Proposed.")]
    question = ask(lease, "Report the tap and the door")
    assert FakeClient.calls[2]["messages"][-1]["content"][0]["content"] == '{"error": "only one proposal per answer"}'
    question.refresh_from_db()
    assert question.proposal["what"] == "Tap leaking"


def test_the_mpesa_prompt_after_yes(lease, paybill):
    FakeClient.script = [FakeClient.tool("propose_mpesa_prompt", {"amount": 5000}),
                         FakeClient.text("I can send a prompt.")]
    ask(lease, "Send me an M-Pesa prompt for 5000")
    assert "KES 5,000.00" in texts()[-1][1]
    assert not StkRequest.objects.exists()
    ask(lease, "Yes", "wamid.yes")
    stk_request = StkRequest.objects.get()
    assert stk_request.via_assistant and not stk_request.via_ussd and stk_request.requested_by is None
    assert (stk_request.phone, stk_request.amount) == (phone_of(lease), 5000)
    assert len(FakeDarajaClient.calls) == 1
    assert texts()[-1][1] == tenant.TEXT["en"]["prompt_sent"]
    # Another one at once waits for the first.
    FakeClient.script = [FakeClient.tool("propose_mpesa_prompt", {"amount": 5000}), FakeClient.text("Again.")]
    ask(lease, "Send it again", "wamid.again")
    ask(lease, "Yes", "wamid.yes2")
    assert StkRequest.objects.count() == 1 and texts()[-1][1] == tenant.TEXT["en"]["wait"]


def test_no_prompt_without_mpesa(lease):
    caller = tenant.whatsapp_caller(phone_of(lease))
    result = tenant.run_tool(caller, "propose_mpesa_prompt", {"amount": 5000})
    assert "not possible" in result["error"] and caller.proposals == []


def test_a_pin_still_allows_a_repair(lease):
    UssdPin.objects.create(phone=phone_of(lease), pin_hash="x")
    FakeClient.script = repair_script()
    ask(lease, "Report my tap")
    ask(lease, "YES", "wamid.yes")
    assert MaintenanceRequest.objects.count() == 1


def test_bad_repair_details_are_refused(lease):
    caller = tenant.whatsapp_caller(phone_of(lease))
    assert "error" in tenant.run_tool(caller, "propose_repair", {"what": "x"})
    assert "error" in tenant.run_tool(caller, "propose_repair", {"what": "Tap leaking", "kind": "ROOF"})
    assert caller.proposals == []


# ---------------------------------------------------------------------------
# The portal Ask page (items 2-4)
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant_client(client, owner, lease):
    client.force_login(join(owner, lease))
    return client


def test_the_ask_page_needs_the_switch(tenant_client, owner, settings):
    url = reverse("portal:ask")
    assert "Ask about your rent or a repair" in tenant_client.get(reverse("portal:home")).content.decode()
    assert tenant_client.get(url).status_code == 200
    owner.organization.tenant_assistant_enabled = False
    owner.organization.save()
    assert tenant_client.get(url).status_code == 404
    assert "Ask about your rent or a repair" not in tenant_client.get(reverse("portal:home")).content.decode()
    owner.organization.tenant_assistant_enabled = True
    owner.organization.save()
    settings.OPENAI_API_KEY = ""
    assert tenant_client.get(url).status_code == 404


def test_staff_without_a_tenancy_get_no_ask_page(client, owner):
    client.force_login(owner.user)
    assert client.get(reverse("portal:ask")).status_code in (302, 403, 404)


def test_a_portal_question_is_answered(tenant_client, lease, settings):
    settings.WHATSAPP_BACKEND = ""  # the portal works without WhatsApp
    FakeClient.script = [FakeClient.tool("balance", {}), FakeClient.text("You owe KES 35,000.00.")]
    tenant_client.post(reverse("portal:ask"), {"question": "What do I owe?"})
    page = tenant_client.get(reverse("portal:ask")).content.decode()
    assert "What do I owe?" in page and "You owe KES 35,000.00." in page
    question = Question.objects.get()
    assert question.channel == Question.Channel.PORTAL and question.tenant == lease.primary_tenant
    assert "portal" in FakeClient.calls[0]["system"]
    assert MemoryWhatsAppSender.outbox == []


def card_id(client):
    return next(iter(client.session[CARDS_KEY]))


def test_a_portal_card_confirms_once(tenant_client, lease):
    FakeClient.script = repair_script()
    tenant_client.post(reverse("portal:ask"), {"question": "My tap leaks, report it"})
    page = tenant_client.get(reverse("portal:ask")).content.decode()
    assert "Report a repair" in page and "Kitchen tap leaking" in page and "Confirm" in page
    assert not MaintenanceRequest.objects.exists()
    pid = card_id(tenant_client)
    tenant_client.post(reverse("portal:ask_confirm"), {"id": pid, "action": "confirm"})
    req = MaintenanceRequest.objects.get()
    assert req.source == MaintenanceRequest.Source.ASSISTANT and req.tenant == lease.primary_tenant
    assert f"Reported as {req.number}" in tenant_client.get(reverse("portal:ask")).content.decode()
    tenant_client.post(reverse("portal:ask_confirm"), {"id": pid, "action": "confirm"})
    assert MaintenanceRequest.objects.count() == 1
    assert AuditEvent.objects.get(action="assistant.tenant_confirmed").changes["via"] == [None, "portal"]


def test_a_portal_card_can_be_dismissed_and_expires(tenant_client, lease):
    FakeClient.script = repair_script() + repair_script()
    tenant_client.post(reverse("portal:ask"), {"question": "Report my tap"})
    pid = card_id(tenant_client)
    tenant_client.post(reverse("portal:ask_confirm"), {"id": pid, "action": "dismiss"})
    assert "Dismissed. Nothing was done." in tenant_client.get(reverse("portal:ask")).content.decode()
    tenant_client.post(reverse("portal:ask_confirm"), {"id": pid, "action": "confirm"})
    assert not MaintenanceRequest.objects.exists()

    tenant_client.post(reverse("portal:ask"), {"question": "Report my tap again", "action": "new"})
    tenant_client.post(reverse("portal:ask"), {"question": "Report my tap again"})
    session = tenant_client.session
    [pid] = session[CARDS_KEY]
    session[CARDS_KEY][pid]["created"] = (timezone.now() - datetime.timedelta(minutes=31)).isoformat()
    session.save()
    tenant_client.post(reverse("portal:ask_confirm"), {"id": pid, "action": "confirm"})
    assert not MaintenanceRequest.objects.exists()
    assert "This has expired" in tenant_client.get(reverse("portal:ask")).content.decode()


def test_a_card_from_another_session_does_nothing(tenant_client):
    tenant_client.post(reverse("portal:ask_confirm"), {"id": "not-a-card", "action": "confirm"})
    assert not MaintenanceRequest.objects.exists()


def test_the_mpesa_prompt_from_the_portal(tenant_client, lease, paybill):
    FakeClient.script = [FakeClient.tool("propose_mpesa_prompt", {"amount": 2500}), FakeClient.text("Confirm it.")]
    tenant_client.post(reverse("portal:ask"), {"question": "Send me a prompt for 2500"})
    assert "KES 2,500.00" in tenant_client.get(reverse("portal:ask")).content.decode()
    tenant_client.post(reverse("portal:ask_confirm"), {"id": card_id(tenant_client), "action": "confirm"})
    stk_request = StkRequest.objects.get()
    assert stk_request.via_assistant and stk_request.amount == 2500 and stk_request.phone == phone_of(lease)


def test_the_portal_limit(tenant_client):
    FakeClient.script = [FakeClient.text(f"Answer {n}.") for n in range(tenant.PORTAL_DAILY)]
    for n in range(tenant.PORTAL_DAILY + 1):
        tenant_client.post(reverse("portal:ask"), {"question": f"Question {n}"})
    assert Question.objects.filter(status=Question.Status.LIMITED).count() == 1
    assert "most questions allowed today" in tenant_client.get(reverse("portal:ask")).content.decode()
