"""Tenants ask on WhatsApp (D-093): their own leases only, read-only, answered after the webhook returns."""

import datetime
import hashlib
import hmac
import json

import pytest
from django.urls import reverse

from accounts.tests.factories import make_org, make_property
from accounts.tests.test_isolation import login
from ai_assistant import tenant
from ai_assistant.client import FakeClient
from ai_assistant.models import Question
from audit.models import AuditEvent
from core.phone import normalize_phone
from maintenance import services as maintenance_services
from notifications.providers.whatsapp import MemoryWhatsAppSender
from portal.models import UssdPin
from reports.tests.test_income import bill, make_lease, pay

pytestmark = pytest.mark.django_db
SECRET = "app-secret"


@pytest.fixture(autouse=True)
def wa(settings):
    settings.WHATSAPP_BACKEND = "notifications.providers.whatsapp.MemoryWhatsAppSender"
    settings.WA_APP_SECRET = SECRET
    MemoryWhatsAppSender.outbox.clear()


@pytest.fixture
def owner():
    m = make_org()
    m.organization.tenant_assistant_enabled = True
    m.organization.save()
    return m


@pytest.fixture
def prop(owner):
    return make_property(owner.organization, name="Acacia Court")


@pytest.fixture
def lease(owner, prop):
    lease = make_lease(owner, prop, code="A1")
    bill(lease, datetime.date(2025, 1, 1), datetime.date(2025, 2, 1))
    pay(owner, lease, 5000, datetime.date(2025, 1, 10))
    return lease


def phone_of(lease):
    return normalize_phone(lease.primary_tenant.phone)


def ask(lease, text="What do I owe?", wamid="wamid.1"):
    question = tenant.receive(phone_of(lease), text, wamid)
    if question is not None:
        tenant.respond(question.pk)
    return question


def texts():
    return [(to, parts[0]) for to, kind, _name, parts in MemoryWhatsAppSender.outbox if kind == "text"]


def results(call):
    return [json.loads(block["content"]) for block in call["messages"][-1]["content"]]


# ---------------------------------------------------------------------------
# Who gets an answer (items 1-2)
# ---------------------------------------------------------------------------


def test_a_tenant_gets_an_answer_about_their_own_balance(lease):
    FakeClient.script = [FakeClient.tool("balance", {}), FakeClient.text("You owe KES 35,000.00.")]
    question = ask(lease)
    assert texts() == [(phone_of(lease), "You owe KES 35,000.00.")]
    [result] = results(FakeClient.calls[1])
    assert result["home"] == "Acacia Court A1" and result["owed"] == "KES 35,000.00"
    question.refresh_from_db()
    assert (question.status, question.channel, question.user, question.tenant.name) == (
        Question.Status.ANSWERED, Question.Channel.WHATSAPP, None, "Tenant A1")
    # Only the tenant tools are offered, and the prompt is the tenant one.
    assert {t["name"] for t in FakeClient.calls[0]["tools"]} == {
        "my_leases", "balance", "how_to_pay", "payments", "repairs", "landlord_notes", "propose_repair",
        "propose_mpesa_prompt"}
    assert "tenant" in FakeClient.calls[0]["system"] and "Acacia" not in FakeClient.calls[0]["system"]


def test_no_reply_when_the_switch_is_off_or_the_number_is_unknown(owner, lease, settings):
    owner.organization.tenant_assistant_enabled = False
    owner.organization.save()
    assert ask(lease) is None
    owner.organization.tenant_assistant_enabled = True
    owner.organization.save()
    assert tenant.receive("+254799999999", "What do I owe?", "wamid.2") is None
    settings.WHATSAPP_BACKEND = ""
    assert ask(lease, wamid="wamid.3") is None
    settings.WHATSAPP_BACKEND = "notifications.providers.whatsapp.MemoryWhatsAppSender"
    settings.OPENAI_API_KEY = ""
    assert ask(lease, wamid="wamid.4") is None
    assert texts() == [] and not Question.objects.exists()


def test_tools_see_only_the_askers_leases(owner, prop, lease):
    make_lease(owner, prop, code="B2")
    other = make_org()
    other.organization.tenant_assistant_enabled = True
    other.organization.save()
    make_lease(other, make_property(other.organization, name="Elsewhere"), code="C3")
    caller = tenant.Caller(phone_of(lease), [lease], pin_set=False, today=datetime.date(2025, 2, 20))
    homes = tenant.my_leases(caller, {})["homes"]
    assert [h["home"] for h in homes] == ["Acacia Court A1"]
    assert tenant.balance(caller, {"unit": "B2"})["home"] == "Acacia Court A1"


def test_several_homes_ask_which_one(owner, prop, lease):
    second = make_lease(owner, prop, code="A2")
    caller = tenant.Caller(phone_of(lease), [lease, second], pin_set=False, today=datetime.date(2025, 2, 20))
    assert tenant.balance(caller, {})["homes"] == ["Acacia Court A1", "Acacia Court A2"]
    assert tenant.balance(caller, {"unit": "a2"})["home"] == "Acacia Court A2"


def test_payments_how_to_pay_and_repairs(owner, prop, lease):
    maintenance_services.report(owner, prop, title="Leaking tap", unit=lease.unit, from_tenant=True)
    caller = tenant.Caller(phone_of(lease), [lease], pin_set=False, today=datetime.date(2025, 2, 20))
    [payment] = tenant.payments(caller, {})["payments"]
    assert payment["amount"] == "KES 5,000.00" and payment["receipt"]
    assert "ways" in tenant.how_to_pay(caller, {})
    [repair] = tenant.repairs(caller, {})["repairs"]
    assert repair["what"] == "Leaking tap"


def test_a_ussd_pin_keeps_balances_and_payments_private(lease):
    UssdPin.objects.create(phone=phone_of(lease), pin_hash="x")
    FakeClient.script = [FakeClient.tool("balance", {}), FakeClient.text("Balances are private here.")]
    ask(lease)
    [result] = results(FakeClient.calls[1])
    assert result == tenant.NOT_ALLOWED_PIN
    caller = tenant.Caller(phone_of(lease), [lease], pin_set=True, today=datetime.date(2025, 2, 20))
    assert tenant.payments(caller, {}) == tenant.NOT_ALLOWED_PIN
    assert "homes" in tenant.my_leases(caller, {})


# ---------------------------------------------------------------------------
# Once, within limits, and failures (items 6-8)
# ---------------------------------------------------------------------------


def test_the_same_message_is_answered_once(lease):
    FakeClient.script = [FakeClient.text("Hello."), FakeClient.text("Again.")]
    assert ask(lease) is not None
    assert ask(lease) is None
    assert len(texts()) == 1


def test_the_limit_is_said_once_then_silence(lease):
    FakeClient.script = [FakeClient.text(f"Answer {n}.") for n in range(tenant.PHONE_DAILY)]
    for n in range(tenant.PHONE_DAILY):
        ask(lease, wamid=f"wamid.{n}")
    limited = ask(lease, wamid="wamid.over")
    assert limited.status == Question.Status.LIMITED
    assert ask(lease, wamid="wamid.over2") is None
    sent = texts()
    assert len(sent) == tenant.PHONE_DAILY + 1 and sent[-1][1] == tenant.TEXT["en"]["limited"]


def test_a_failure_sends_the_sorry_text(lease):
    FakeClient.script = []  # nothing scripted: the fake fails
    question = ask(lease)
    question.refresh_from_db()
    assert question.status == Question.Status.FAILED
    assert texts() == [(phone_of(lease), tenant.TEXT["en"]["failed"])]


def test_history_is_the_same_numbers_recent_answers(lease):
    FakeClient.script = [FakeClient.text("You owe 35,000."), FakeClient.text("Pay by Paybill.")]
    ask(lease, "What do I owe?", "wamid.a")
    ask(lease, "How do I pay it?", "wamid.b")
    sent = FakeClient.calls[1]["messages"]
    assert [m["content"] for m in sent] == ["What do I owe?", "You owe 35,000.", "How do I pay it?"]


def test_long_questions_are_cut(lease):
    FakeClient.script = [FakeClient.text("Ok.")]
    question = ask(lease, "x" * 1000)
    assert len(question.question) == tenant.MAX_QUESTION


# ---------------------------------------------------------------------------
# The webhook and the switch
# ---------------------------------------------------------------------------


def post(client, payload):
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return client.post(reverse("hook_whatsapp"), body, content_type="application/json",
                       HTTP_X_HUB_SIGNATURE_256=signature)


def message(lease, text, wamid="wamid.hook"):
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "messages": [{"from": phone_of(lease).lstrip("+"), "id": wamid, "type": "text", "text": {"body": text}}]}}]}]}


def test_the_webhook_answers_after_replying(client, lease):
    FakeClient.script = [FakeClient.text("You owe KES 35,000.00.")]
    response = post(client, message(lease, "What do I owe?"))
    assert response.status_code == 200 and response.content == b"OK"
    assert texts() == [(phone_of(lease), "You owe KES 35,000.00.")]
    post(client, message(lease, "What do I owe?"))  # Meta retries the same message
    assert len(texts()) == 1


def test_stop_and_start_words_are_not_answered(client, owner, lease):
    post(client, message(lease, "STOP"))
    post(client, message(lease, "anza", "wamid.start"))
    assert texts() == [] and not Question.objects.exists()


def test_owner_switches_tenant_answers(client, settings):
    owner = make_org()
    login(client, owner)
    switch = reverse("ai_assistant:switch")
    page = client.get(reverse("ai_assistant:ask")).content.decode()
    assert "Answer tenants" in page and "messaging your WhatsApp number" in page
    client.post(switch, {"which": "tenants", "on": "1"})
    owner.organization.refresh_from_db()
    assert owner.organization.tenant_assistant_enabled and not owner.organization.assistant_enabled
    assert AuditEvent.objects.filter(action="assistant.tenants_enabled").exists()
    client.post(switch, {"which": "tenants", "on": "0"})
    owner.organization.refresh_from_db()
    assert not owner.organization.tenant_assistant_enabled
    # Without WhatsApp the switch still covers the portal (D-094 item 1); without the key it is not offered.
    settings.WHATSAPP_BACKEND = ""
    page = client.get(reverse("ai_assistant:ask")).content.decode()
    assert "Answer tenants" in page and "messaging your WhatsApp number" not in page
    settings.OPENAI_API_KEY = ""
    client.post(switch, {"which": "tenants", "on": "1"})
    owner.organization.refresh_from_db()
    assert not owner.organization.tenant_assistant_enabled


def test_staff_do_not_see_tenant_questions(client, owner, lease):
    FakeClient.script = [FakeClient.text("Hello tenant.")]
    ask(lease, "Secret tenant question")
    owner.organization.assistant_enabled = True
    owner.organization.save()
    login(client, owner)
    assert "Secret tenant question" not in client.get(reverse("ai_assistant:ask")).content.decode()
