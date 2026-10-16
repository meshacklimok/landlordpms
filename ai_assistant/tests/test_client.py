"""The OpenAI client (D-115): what is sent, and how the answer is turned back into the shape the tool loop reads."""

import io
import json
import urllib.error

import pytest

from ai_assistant import client
from ai_assistant.client import AssistantError, OpenAIClient

TOOLS = [{"name": "arrears", "description": "Who owes rent.", "input_schema": {"type": "object", "properties": {}}}]


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture
def sent(settings, monkeypatch):
    settings.OPENAI_API_KEY = "sk-test"
    settings.ASSISTANT_MODEL = "gpt-5.6-terra"
    box = {"requests": [], "replies": []}

    def fake_open(request, timeout):
        box["requests"].append(request)
        reply = box["replies"].pop(0)
        if isinstance(reply, Exception):
            raise reply
        return _Response(json.dumps(reply).encode())

    monkeypatch.setattr(client._opener, "open", fake_open)
    return box


def _reply(message, finish="stop", usage=(120, 30)):
    return {"choices": [{"message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]}}


def test_text_answer_and_request(sent):
    sent["replies"].append(_reply({"role": "assistant", "content": "Two tenants owe rent."}))
    out = OpenAIClient().create(system="Be brief.", tools=TOOLS, max_tokens=1024,
                                messages=[{"role": "user", "content": "Who owes?"}])
    assert out == {"content": [{"type": "text", "text": "Two tenants owe rent."}], "stop_reason": "end_turn",
                   "usage": {"input_tokens": 120, "output_tokens": 30}}
    request = sent["requests"][0]
    assert request.full_url == "https://api.openai.com/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer sk-test"
    body = json.loads(request.data)
    assert body["model"] == "gpt-5.6-terra" and body["max_completion_tokens"] == 1024 and body["reasoning_effort"] == "none"
    assert body["messages"] == [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "Who owes?"}]
    assert body["tools"] == [{"type": "function", "function": {
        "name": "arrears", "description": "Who owes rent.", "parameters": {"type": "object", "properties": {}}}}]


def test_tool_call_round_trip(sent):
    sent["replies"].append(_reply({"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_9", "type": "function", "function": {"name": "arrears", "arguments": '{"min_days_overdue": 30}'}}]},
        finish="tool_calls"))
    out = OpenAIClient().create(system="s", tools=TOOLS, max_tokens=512,
                                messages=[{"role": "user", "content": "Who is a month late?"}])
    assert out["stop_reason"] == "tool_use"
    assert out["content"] == [{"type": "tool_use", "id": "call_9", "name": "arrears", "input": {"min_days_overdue": 30}}]

    # The loop then sends the call back with its result, in the Anthropic shape; it goes out as OpenAI turns.
    sent["replies"].append(_reply({"role": "assistant", "content": "One tenant."}))
    OpenAIClient().create(system="s", tools=TOOLS, max_tokens=512, messages=[
        {"role": "user", "content": "Who is a month late?"},
        {"role": "assistant", "content": out["content"]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_9", "content": '{"rows": []}'}]}])
    turns = json.loads(sent["requests"][1].data)["messages"][1:]
    assert turns == [
        {"role": "user", "content": "Who is a month late?"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call_9", "type": "function", "function": {
            "name": "arrears", "arguments": '{"min_days_overdue": 30}'}}]},
        {"role": "tool", "tool_call_id": "call_9", "content": '{"rows": []}'}]


@pytest.mark.parametrize("reply", [
    urllib.error.HTTPError("u", 429, "busy", {}, None),
    TimeoutError(),
    {"error": "nothing useful"},
    _reply({"role": "assistant", "content": None, "refusal": "I can't help with that."}),
    _reply({"role": "assistant", "tool_calls": [{"id": "c", "function": {"name": "arrears", "arguments": "{oops"}}]},
           finish="tool_calls"),
])
def test_failures_raise(sent, reply):
    sent["replies"].append(reply)
    with pytest.raises(AssistantError):
        OpenAIClient().create(system="s", tools=TOOLS, max_tokens=512, messages=[{"role": "user", "content": "q"}])


def test_enabled_follows_the_backend_key(settings):
    settings.ASSISTANT_BACKEND = "ai_assistant.client.OpenAIClient"
    settings.OPENAI_API_KEY, settings.ANTHROPIC_API_KEY = "", "sk-ant"
    assert not client.enabled()
    settings.OPENAI_API_KEY = "sk-test"
    assert client.enabled()


def test_assistant_check_command():
    from io import StringIO

    from django.core.management import call_command

    from ai_assistant.client import FakeClient

    FakeClient.calls = []
    FakeClient.script = [FakeClient.tool("rent_owed", {"unit": "A1"}), FakeClient.text("A1 owes KES 12,500.")]
    out = StringIO()
    call_command("assistant_check", stdout=out)
    text = out.getvalue()
    assert 'rent_owed({"unit": "A1"})' in text and "A1 owes KES 12,500." in text and "provider works" in text
    assert FakeClient.calls[1]["messages"][-1]["content"][0]["type"] == "tool_result"


def test_assistant_check_without_a_key(settings):
    from django.core.management import CommandError, call_command

    settings.OPENAI_API_KEY = ""
    with pytest.raises(CommandError, match="No key"):
        call_command("assistant_check")
