"""What the organization wants the assistant to know (D-119): written by an owner, read by both assistants."""

import datetime

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from ai_assistant import knowledge, services, tenant
from ai_assistant.client import FakeClient
from ai_assistant.models import KnowledgeEntry
from audit.models import AuditEvent
from core.phone import normalize_phone
from reports.tests.test_income import make_lease

pytestmark = pytest.mark.django_db

TODAY = datetime.date(2025, 4, 20)
URL = reverse("ai_assistant:knowledge")


@pytest.fixture
def owner():
    m = make_org()
    m.organization.assistant_enabled = m.organization.tenant_assistant_enabled = True
    m.organization.save()
    return m


@pytest.fixture
def org(owner):
    return owner.organization


@pytest.fixture
def prop(org):
    return make_property(org, name="Acacia Court")


def add(m, title, answer, *, prop=None, for_tenants=False):
    return knowledge.save(m, KnowledgeEntry(title=title, answer=answer, property=prop, for_tenants=for_tenants))


def test_an_owner_adds_changes_and_removes_entries(client, owner, org, prop):
    login(client, owner)
    assert client.get(URL).status_code == 200
    response = client.post(URL, {"title": "  Office   hours ", "answer": "Monday to Friday, 8am to 5pm.",
                                 "property": "", "for_tenants": "on"})
    assert response.status_code == 302
    entry = KnowledgeEntry.objects.get()
    assert (entry.title, entry.for_tenants, entry.property, entry.created_by) == (
        "Office hours", True, None, owner.user)
    page = client.get(reverse("ai_assistant:knowledge_entry", args=[entry.public_id]))
    assert page.status_code == 200 and b"Monday to Friday" in page.content
    client.post(reverse("ai_assistant:knowledge_entry", args=[entry.public_id]),
                {"action": "save", "title": "Office hours", "answer": "Weekdays 8 to 5, Saturday 9 to 1.",
                 "property": prop.pk})
    entry.refresh_from_db()
    assert entry.answer.startswith("Weekdays") and entry.property == prop and not entry.for_tenants
    assert b"1 entry" in client.get(reverse("ai_assistant:ask")).content
    client.post(reverse("ai_assistant:knowledge_entry", args=[entry.public_id]), {"action": "delete"})
    assert not KnowledgeEntry.objects.exists()
    assert list(AuditEvent.objects.filter(action__startswith="assistant.knowledge").values_list(
        "action", flat=True).order_by("pk")) == [
        "assistant.knowledge_added", "assistant.knowledge_changed", "assistant.knowledge_removed"]


def test_only_owners_manage_and_only_their_own_entries(client, owner, org, prop):
    accountant = add_member(org, "accountant", all_properties=True)
    with pytest.raises(PermissionDenied):
        add(accountant, "Deposit", "One month's rent.")
    login(client, accountant)
    assert client.get(URL).status_code == 403
    other = make_org(name="Other")
    theirs = add(other, "Their rule", "Secret to them.")
    login(client, owner)
    assert client.get(reverse("ai_assistant:knowledge_entry", args=[theirs.public_id])).status_code == 404
    response = client.post(URL, {"title": "Gate", "answer": "Closes at 10pm.",
                                 "property": make_property(other.organization).pk})
    assert response.status_code == 400 and not KnowledgeEntry.objects.filter(organization=org).exists()


def test_phone_numbers_and_emails_are_refused(owner):
    with pytest.raises(ValidationError) as exc:
        add(owner, "Caretaker", "Call John on 0712 345 678 or john@example.com.")
    assert "answer" in exc.value.message_dict
    with pytest.raises(ValidationError):
        add(owner, "Caretaker", "   ")
    assert add(owner, "Caretaker", "John lives in A1 and is in from 7am to 7pm.").pk


def test_there_is_a_limit(owner, monkeypatch):
    monkeypatch.setattr(knowledge, "MAX_ENTRIES", 2)
    add(owner, "One", "First.")
    add(owner, "Two", "Second.")
    with pytest.raises(ValidationError):
        add(owner, "Three", "Third.")


def test_staff_see_entries_on_their_properties(owner, org, prop):
    other_prop = make_property(org, name="Baobab Flats")
    add(owner, "Office hours", "Weekdays 8 to 5.")
    add(owner, "Acacia gate", "Closes at 10pm.", prop=prop)
    add(owner, "Baobab parking", "Two cars per unit.", prop=other_prop)
    assert [e["title"] for e in knowledge.for_staff(owner, {"text": "x"}, TODAY)["entries"]] == [
        "Acacia gate", "Baobab parking", "Office hours"]
    caretaker = add_member(org, "caretaker", properties=[prop])
    seen = knowledge.for_staff(caretaker, {"text": "x"}, TODAY)["entries"]
    assert [(e["title"], e["property"]) for e in seen] == [("Acacia gate", "Acacia Court"),
                                                           ("Office hours", "all properties")]
    assert "nothing" in knowledge.for_staff(make_org(name="Empty"), {"text": "x"}, TODAY)["note"]


def test_many_entries_are_searched_by_words(owner, monkeypatch):
    monkeypatch.setattr(knowledge, "SEND_ALL_UNDER", 10)
    add(owner, "Deposit refunds", "Paid back within 30 days of moving out, less repairs.")
    add(owner, "Office hours", "Weekdays 8 to 5.")
    add(owner, "Rubbish", "Collected on Tuesdays.")
    found = knowledge.for_staff(owner, {"text": "when is the deposit refunded after moving"}, TODAY)
    assert [e["title"] for e in found["entries"]] == ["Deposit refunds"] and found["only_best_matches"]
    missed = knowledge.for_staff(owner, {"text": "parking"}, TODAY)
    assert missed["entries"] == [] and missed["titles"] == ["Deposit refunds", "Office hours", "Rubbish"]


def test_the_staff_assistant_answers_from_the_entries(owner):
    add(owner, "Office hours", "Weekdays 8 to 5.")
    FakeClient.script = [FakeClient.tool("knowledge", {"text": "office hours"}),
                         FakeClient.text("Weekdays 8 to 5.")]
    result = services.ask(owner, "When is the office open?", today=TODAY)
    assert result.answered
    assert "knowledge" in {t["name"] for t in FakeClient.calls[0]["tools"]}
    assert "call knowledge" in FakeClient.calls[0]["system"]
    assert "Weekdays 8 to 5." in FakeClient.calls[1]["messages"][-1]["content"][0]["content"]


def test_tenants_get_only_entries_for_tenants_on_their_own_properties(owner, org, prop):
    lease = make_lease(owner, prop, code="A1")
    add(owner, "Rubbish", "Collected on Tuesdays.", for_tenants=True)
    add(owner, "Acacia gate", "Closes at 10pm.", prop=prop, for_tenants=True)
    add(owner, "Baobab parking", "Two cars.", prop=make_property(org, name="Baobab Flats"), for_tenants=True)
    add(owner, "Arrears policy", "Call after 5 days late.")
    add(make_org(name="Other"), "Their rule", "Not for this tenant.", for_tenants=True)
    caller = tenant.whatsapp_caller(normalize_phone(lease.primary_tenant.phone), today=TODAY)
    notes = tenant.run_tool(caller, "landlord_notes", {"text": "rules"})
    assert [e["title"] for e in notes["entries"]] == ["Acacia gate", "Rubbish"]
    assert "landlord_notes" in tenant.system_prompt(caller)
    KnowledgeEntry.objects.update(for_tenants=False)
    assert "contact the landlord" in tenant.run_tool(caller, "landlord_notes", {"text": "rules"})["note"]
