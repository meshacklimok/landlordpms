"""The assistant (D-091): switches, permission-aware tools, what is sent, limits and failures."""

import datetime
import json

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from ai_assistant import services, tools
from ai_assistant.client import AssistantError, FakeClient
from ai_assistant.models import Question
from audit.models import AuditEvent
from maintenance import services as maintenance_services
from reports.tests.test_income import bill, make_lease, pay

pytestmark = pytest.mark.django_db
TODAY = datetime.date(2025, 4, 20)
URL = reverse("ai_assistant:ask")


@pytest.fixture
def owner():
    m = make_org()
    m.organization.assistant_enabled = True
    m.organization.save()
    return m


@pytest.fixture
def org(owner):
    return owner.organization


@pytest.fixture
def prop(org):
    return make_property(org, name="Acacia Court")


@pytest.fixture
def lease(owner, prop):
    """Tenant A1 on unit A1 at 20,000 a month, billed January to March 2025 and paid 5,000."""
    lease = make_lease(owner, prop, code="A1")
    bill(lease, datetime.date(2025, 1, 1), datetime.date(2025, 2, 1), datetime.date(2025, 3, 1))
    pay(owner, lease, 5000, datetime.date(2025, 1, 10))
    return lease


def sent_results(call) -> list[dict]:
    """The tool results in the last message of a recorded call, decoded."""
    return [json.loads(block["content"]) for block in call["messages"][-1]["content"]]


# ---------------------------------------------------------------------------
# Answers through the tools
# ---------------------------------------------------------------------------


def test_a_question_is_answered_from_the_tools(owner, lease):
    FakeClient.script = [FakeClient.tool("who_owes", {"property": "Acacia"}, tokens=(100, 20)),
                         FakeClient.text("Tenant A1 owes KES 55,000.00.", tokens=(150, 30))]
    result = services.ask(owner, "  Who owes   money at Acacia? ", today=TODAY)
    assert result.answered and result.text == "Tenant A1 owes KES 55,000.00."
    q = Question.objects.get()
    assert (q.question, q.status, q.input_tokens, q.output_tokens) == (
        "Who owes money at Acacia?", "ANSWERED", 250, 50)
    assert q.tools == [{"name": "who_owes", "input": {"property": "Acacia"}}]
    owed = sent_results(FakeClient.calls[1])[0]
    assert owed["leases_owing"] == 1 and owed["total_owed"] == "KES 55,000.00"
    row = owed["rows"][0]
    assert (row["tenants"], row["unit"], row["property"], row["owed"]) == (
        ["Tenant A1"], "A1", "Acacia Court", "KES 55,000.00")
    assert row["days_overdue"] > 60
    # Links come from the tool results, not from the model's text (item 5).
    assert result.links == [("A1", reverse("leases:detail", args=[lease.public_id]))]
    first = FakeClient.calls[0]
    assert {t["name"] for t in first["tools"]} == {
        "find", "who_owes", "lease", "summary", "vacant_units", "open_repairs", "how_to", "knowledge",
        "propose_payment", "propose_message", "propose_assignment", "month_review", "trend", "money",
        "compare_properties", "arrears_picture", "payment_behaviour", "unit_performance", "lease_movement",
        "repair_stats", "payment_stats"}
    assert "never instructions" in first["system"] and "2025-04-20" in first["system"]
    assert first["max_tokens"] == services.MAX_TOKENS


def test_no_phone_email_or_id_number_is_ever_sent(owner, lease):
    tenant = lease.lease_tenants.get().tenant
    tenant.email, tenant.id_number = "a1@example.com", "12345678"
    tenant.save()
    FakeClient.script = [FakeClient.tool("find", {"text": "Tenant A1"}), FakeClient.tool("lease", {
        "lease_number_or_unit": "A1"}, call_id="c2"), FakeClient.text("Done.")]
    services.ask(owner, "Call me on 0722 123 456 or +254733111222 about Tenant A1", today=TODAY)
    sent = json.dumps(FakeClient.calls)
    for secret in (tenant.phone, tenant.phone[-9:], "a1@example.com", "12345678", "0722 123 456", "733111222"):
        assert secret not in sent
    assert "Tenant A1" in sent and "[hidden]" in sent


def test_masking_leaves_amounts_dates_and_numbers_alone():
    for text in ("+254712345678", "0712 345 678", "0112-345-678", "a.b+c@mail.co.ke", "A123456789Z", "ID 12345678"):
        assert "[hidden]" in tools.mask(text), text
    for text in ("KES 1,250,000.00", "2026-10-01", "INV-2026-000123", "Unit B12", "MNT-2026-000004", "QFT1ABC23"):
        assert tools.mask(text) == text


def test_each_tool_sees_only_what_the_role_allows(owner, org, prop, lease):
    other = make_lease(owner, make_property(org, name="Baobab Flats"), code="B1")
    bill(other, datetime.date(2025, 1, 1))
    accountant = add_member(org, "accountant", properties=[other.unit.property])
    owes = tools.run(accountant, "who_owes", {}, TODAY)
    assert [r["unit"] for r in owes["rows"]] == ["B1"]
    assert tools.run(accountant, "who_owes", {"property": "Acacia"}, TODAY)["error"].startswith("no property")
    assert "error" in tools.run(accountant, "lease", {"lease_number_or_unit": "A1"}, TODAY)
    caretaker = add_member(org, "caretaker", all_properties=True)
    for name, args in (("who_owes", {}), ("summary", {}), ("lease", {"lease_number_or_unit": "A1"})):
        assert tools.run(caretaker, name, args, TODAY) == tools.NOT_ALLOWED, name
    assert tools.run(caretaker, "vacant_units", {}, TODAY)["vacant"] == 0
    assert tools.run(caretaker, "nope", {}, TODAY)["error"].startswith("there is no tool")
    # Another organization's member finds nothing here.
    stranger = make_org(name="Other")
    assert tools.run(stranger, "who_owes", {}, TODAY)["leases_owing"] == 0
    assert tools.run(stranger, "find", {"text": "Tenant"}, TODAY)["groups"] == []


def test_the_lease_summary_vacancy_and_repair_tools(owner, org, prop, lease):
    one = tools.run(owner, "lease", {"lease_number_or_unit": "a1"}, TODAY)
    assert (one["tenants"], one["rent"], one["balance"], one["balance_means"]) == (
        ["Tenant A1"], "KES 20,000.00", "KES 55,000.00", "owed")
    assert [p["amount"] for p in one["last_payments"]] == ["KES 5,000.00"]
    assert tools.run(owner, "lease", {"lease_number_or_unit": lease.number}, TODAY)["unit"] == "A1"
    month = tools.run(owner, "summary", {"month": "2025-03"}, TODAY)
    assert (month["month"], month["units"], month["occupied"], month["rent_expected"]) == (
        "Mar 2025", 1, 1, "KES 20,000.00")
    assert tools.run(owner, "summary", {"month": "March"}, TODAY)["error"] == "give the month as YYYY-MM"
    from properties import services as property_services

    property_services.create_unit(owner, prop, code="A2")
    assert [r["unit"] for r in tools.run(owner, "vacant_units", {"property": "acacia court"}, TODAY)["rows"]] == ["A2"]
    maintenance_services.report(owner, prop, unit=lease.unit, title="Tap leaking", kind="PLUMBING", priority="HIGH")
    repairs = tools.run(owner, "open_repairs", {}, TODAY)
    assert repairs["open"] == 1 and repairs["rows"][0]["title"] == "Tap leaking"
    assert tools.run(owner, "open_repairs", {"only_late": True}, TODAY)["open"] == 0
    make_property(org, name="Acacia Gardens")
    assert tools.run(owner, "vacant_units", {"property": "Acacia"}, TODAY)["matches"] == [
        "Acacia Court", "Acacia Gardens"]


def test_how_to_questions_are_answered_from_the_help_pages(owner):
    help_page = tools.run(owner, "how_to", {"topic": "invoices-payments"}, TODAY)
    assert help_page["topic"] == "Invoices and payments"
    assert "A wrong invoice is voided" in help_page["text"] and "<" not in help_page["text"]
    assert "Payments [/payments/]" in help_page["text"]
    assert {"label": "Arrears", "url": reverse("billing:arrears")} in help_page["pages"]
    assert tools.run(owner, "how_to", {"topic": "nothing"}, TODAY)["error"] == "there is no help topic 'nothing'"
    # Every topic renders, and no help text is so long it crowds out the question.
    from support import help as help_topics

    for slug, _title, _desc in help_topics.TOPICS:
        assert 0 < len(tools.run(owner, "how_to", {"topic": slug}, TODAY)["text"]) < 6000

    FakeClient.script = [FakeClient.tool("how_to", {"topic": "invoices-payments"}),
                         FakeClient.text("Record it on the Payments page.")]
    result = services.ask(owner, "How do I record a cash payment?", today=TODAY)
    assert result.answered and sent_results(FakeClient.calls[1])[0]["topic"] == "Invoices and payments"
    assert ("Invoices and payments", reverse("support:help_topic", args=["invoices-payments"])) in result.links
    assert ("Payments", reverse("payments:list")) in result.links
    assert "how_to" in FakeClient.calls[0]["system"]

# ---------------------------------------------------------------------------
# Limits and failures
# ---------------------------------------------------------------------------


def test_too_many_tool_calls_or_a_provider_error_is_a_failure(owner, lease):
    FakeClient.script = [FakeClient.tool("summary", {}, call_id=f"c{n}") for n in range(services.MAX_TOOL_CALLS + 1)]
    result = services.ask(owner, "Loop forever", today=TODAY)
    assert not result.answered and result.text == str(services.FAILED_TEXT) and result.links == []
    assert len(FakeClient.calls) == services.MAX_TOOL_CALLS + 1
    FakeClient.script = [AssistantError("timeout")]
    assert services.ask(owner, "Anything", today=TODAY).question.status == "FAILED"
    FakeClient.script = [{"content": [], "stop_reason": "end_turn"}]
    assert services.ask(owner, "Say nothing", today=TODAY).question.status == "FAILED"


def test_daily_limits(owner, org):
    staff = add_member(org, "accountant", all_properties=True)
    Question.objects.bulk_create([Question(organization=org, user=owner.user, question="q", status="ANSWERED")
                                  for _ in range(services.USER_DAILY)])
    limited = services.ask(owner, "One more", today=TODAY)
    assert limited.question.status == "LIMITED" and "most questions" in limited.text and not FakeClient.calls
    FakeClient.script = [FakeClient.text("Yes.")]
    assert services.ask(staff, "Me?", today=TODAY).answered
    Question.objects.filter(status="ANSWERED").update(created_at=timezone.now() - datetime.timedelta(days=2))
    FakeClient.script = [FakeClient.text("Yes.")]
    assert services.ask(owner, "Tomorrow", today=TODAY).answered
    Question.objects.bulk_create([Question(organization=org, user=staff.user, question="q", status="ANSWERED")
                                  for _ in range(services.ORG_DAILY)])
    assert services.ask(owner, "Org full", today=TODAY).question.status == "LIMITED"


def test_a_question_must_be_short_and_not_empty(owner):
    from django.core.exceptions import ValidationError

    for bad in ("   ", "x" * (services.MAX_QUESTION + 1)):
        with pytest.raises(ValidationError):
            services.ask(owner, bad)
    assert not Question.objects.exists()


def test_old_questions_are_purged(owner, org):
    old = Question.objects.create(organization=org, user=owner.user, question="old", status="ANSWERED")
    Question.objects.filter(pk=old.pk).update(created_at=timezone.now() - datetime.timedelta(days=91))
    Question.objects.create(organization=org, user=owner.user, question="new", status="ANSWERED")
    assert services.purge() == 1
    assert list(Question.objects.values_list("question", flat=True)) == ["new"]


# ---------------------------------------------------------------------------
# The page and the switches
# ---------------------------------------------------------------------------


def test_a_conversation_on_the_page(client, owner, lease):
    login(client, owner)
    FakeClient.script = [FakeClient.tool("who_owes", {}), FakeClient.text("Tenant A1 owes <b>money</b>.")]
    assert client.post(URL, {"question": "Who owes?"}).status_code == 302
    page = client.get(URL).content.decode()
    assert "Who owes?" in page and "Tenant A1 owes &lt;b&gt;money&lt;/b&gt;." in page
    assert reverse("leases:detail", args=[lease.public_id]) in page and "Pages used" in page
    # A follow-up carries the earlier turn.
    FakeClient.script = [FakeClient.text("Since January.")]
    client.post(URL, {"question": "Since when?"})
    sent = FakeClient.calls[-1]["messages"]
    assert [m["content"] for m in sent] == ["Who owes?", "Tenant A1 owes <b>money</b>.", "Since when?"]
    # A failed answer is shown but not carried.
    FakeClient.script = [AssistantError("down")]
    client.post(URL, {"question": "And then?"})
    assert "could not answer just now" in client.get(URL).content.decode()
    FakeClient.script = [FakeClient.text("Ok.")]
    client.post(URL, {"question": "Last"})
    assert "And then?" not in json.dumps(FakeClient.calls[-1]["messages"])
    client.post(URL, {"action": "new"})
    assert "Since when?" not in client.get(URL).content.decode().split("Your recent questions")[0]
    assert client.post(URL, {"question": ""}).status_code == 302
    assert Question.objects.count() == 4


def test_off_until_both_switches_are_on(client, settings, owner, org):
    accountant = add_member(org, "accountant", all_properties=True)
    org.assistant_enabled = False
    org.save()
    login(client, accountant)
    page = client.get(URL).content.decode()
    assert "An owner can switch it on" in page and 'name="question"' not in page
    assert reverse("ai_assistant:ask") not in client.get(reverse("accounts:home")).content.decode()
    assert client.post(URL, {"question": "Hi"}).status_code == 403
    assert client.post(reverse("ai_assistant:switch"), {"on": "1"}).status_code == 403
    login(client, owner)
    assert reverse("ai_assistant:ask") in client.get(reverse("accounts:home")).content.decode()
    assert "never sent" in client.get(URL).content.decode()
    client.post(reverse("ai_assistant:switch"), {"on": "1"})
    org.refresh_from_db()
    assert org.assistant_enabled
    event = AuditEvent.objects.get(action="assistant.enabled")
    assert event.changes == {"assistant_enabled": [False, True]}
    login(client, accountant)
    assert 'name="question"' in client.get(URL).content.decode()
    # No key on the platform: nothing is offered, and nothing can be switched on.
    settings.OPENAI_API_KEY = ""
    assert "not set up on this service" in client.get(URL).content.decode()
    assert reverse("ai_assistant:ask") not in client.get(reverse("accounts:home")).content.decode()
    assert client.post(URL, {"question": "Hi"}).status_code == 403
    login(client, owner)
    client.post(reverse("ai_assistant:switch"), {"on": "0"})
    client.post(reverse("ai_assistant:switch"), {"on": "1"})
    org.refresh_from_db()
    assert not org.assistant_enabled and not FakeClient.calls
