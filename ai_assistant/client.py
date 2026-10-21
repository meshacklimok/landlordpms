"""Talking to the model (D-091, D-115). `ASSISTANT_BACKEND` picks the client; tests use `FakeClient`.

Every client takes and returns the Anthropic Messages shape (text, tool_use and tool_result blocks), so the
tool loop in services.converse does not care which provider answers. `OpenAIClient` translates to and from
OpenAI Chat Completions. The APIs are called over HTTPS with the standard library, as Web Push is (D-089),
so there is no extra dependency. Only the chosen provider's host is ever contacted.

`read_image` reads a photo into JSON for the meter scans (D-144); only OpenAIClient and FakeClient have it.
"""

import base64
import json
import urllib.error
import urllib.request
from decimal import Decimal

from django.conf import settings
from django.utils.module_loading import import_string

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
TIMEOUT = 30  # seconds per call (item 6)
SCAN_TIMEOUT = 60  # a photo takes longer to read


class AssistantError(Exception):
    """The provider did not give a usable answer: timeout, refusal, bad response."""


def _backend():
    return import_string(settings.ASSISTANT_BACKEND)


def enabled() -> bool:
    """Whether the platform has a provider set up (item 1, the first switch)."""
    return bool(getattr(settings, _backend().KEY_SETTING, ""))


def get_client():
    return _backend()()


def can_read_images() -> bool:
    return enabled() and hasattr(_backend(), "read_image")


def cost_kes(input_tokens: int, output_tokens: int) -> Decimal:
    """What a call cost us in shillings at today's prices (D-144): AI_PRICE_*_PER_M dollars times USD_TO_KES."""
    usd = (Decimal(input_tokens or 0) * Decimal(settings.AI_PRICE_INPUT_PER_M)
           + Decimal(output_tokens or 0) * Decimal(settings.AI_PRICE_OUTPUT_PER_M)) / 1_000_000
    return (usd * Decimal(settings.USD_TO_KES)).quantize(Decimal("0.0001"))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def _post(url: str, body: dict, headers: dict, timeout: int = TIMEOUT) -> dict:
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",  # noqa: S310 - the address is a fixed https endpoint, never user input
                                     headers={**headers, "content-type": "application/json"})
    try:
        with _opener.open(request, timeout=timeout) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise AssistantError(f"provider answered {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise AssistantError(type(exc).__name__) from exc
    if not isinstance(data, dict):
        raise AssistantError("unexpected response")
    return data


class OpenAIClient:
    KEY_SETTING = "OPENAI_API_KEY"

    def create(self, *, system: str, tools: list[dict], messages: list[dict], max_tokens: int) -> dict:
        # OpenAI allows function tools on Chat Completions only with reasoning off.
        body = {"model": settings.ASSISTANT_MODEL, "max_completion_tokens": max_tokens,
                "reasoning_effort": "none", "messages": [{"role": "system", "content": system}, *_to_openai(messages)]}
        if tools:
            body["tools"] = [{"type": "function", "function": {
                "name": t["name"], "description": t["description"], "parameters": t["input_schema"]}} for t in tools]
        data = _post(OPENAI_URL, body, {"authorization": f"Bearer {settings.OPENAI_API_KEY}"})
        try:
            choice = data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AssistantError("unexpected response") from exc
        if message.get("refusal"):
            raise AssistantError("refused")
        content = []
        if message.get("content"):
            content.append({"type": "text", "text": message["content"]})
        for call in message.get("tool_calls") or []:
            try:
                args = json.loads(call["function"]["arguments"] or "{}")
            except (KeyError, TypeError, ValueError) as exc:
                raise AssistantError("bad tool call") from exc
            content.append({"type": "tool_use", "id": call.get("id", ""), "name": call["function"].get("name", ""),
                            "input": args if isinstance(args, dict) else {}})
        usage = data.get("usage") or {}
        return {"content": content,
                "stop_reason": "tool_use" if choice.get("finish_reason") == "tool_calls" else "end_turn",
                "usage": {"input_tokens": usage.get("prompt_tokens", 0),
                          "output_tokens": usage.get("completion_tokens", 0)}}

    def read_image(self, *, instructions: str, image: bytes, schema: dict, max_tokens: int) -> dict:
        """Reads a JPEG into JSON matching `schema` (D-144). Returns {"data": {...}, "usage": {...}}."""
        body = {"model": settings.SCAN_MODEL, "max_completion_tokens": max_tokens, "reasoning_effort": "low",
                "messages": [{"role": "system", "content": instructions},
                             {"role": "user", "content": [
                                 {"type": "text", "text": "Read this photo."},
                                 {"type": "image_url", "image_url": {
                                     "url": "data:image/jpeg;base64," + base64.b64encode(image).decode(),
                                     "detail": "high"}}]}],
                "response_format": {"type": "json_schema",
                                    "json_schema": {"name": "scan", "strict": True, "schema": schema}}}
        data = _post(OPENAI_URL, body, {"authorization": f"Bearer {settings.OPENAI_API_KEY}"}, timeout=SCAN_TIMEOUT)
        usage = data.get("usage") or {}
        usage = {"input_tokens": usage.get("prompt_tokens", 0), "output_tokens": usage.get("completion_tokens", 0)}
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AssistantError("unexpected response") from exc
        if message.get("refusal"):
            raise AssistantError("refused")
        try:
            parsed = json.loads(message.get("content") or "")
        except ValueError as exc:
            raise AssistantError("not JSON") from exc
        if not isinstance(parsed, dict):
            raise AssistantError("not an object")
        return {"data": parsed, "usage": usage}


def _to_openai(messages: list[dict]) -> list[dict]:
    """Anthropic-shaped turns to Chat Completions turns: tool_use becomes tool_calls, tool_result a tool message."""
    out = []
    for turn in messages:
        role, content = turn["role"], turn["content"]
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        text = "\n".join(b.get("text", "") for b in content if b.get("type") == "text")
        if role == "assistant":
            message = {"role": "assistant", "content": text or None}
            calls = [{"id": b["id"], "type": "function",
                      "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})}}
                     for b in content if b.get("type") == "tool_use"]
            if calls:
                message["tool_calls"] = calls
            out.append(message)
            continue
        out.extend({"role": "tool", "tool_call_id": b["tool_use_id"], "content": b.get("content", "")}
                   for b in content if b.get("type") == "tool_result")
        if text:
            out.append({"role": "user", "content": text})
    return out


class AnthropicClient:
    KEY_SETTING = "ANTHROPIC_API_KEY"

    def create(self, *, system: str, tools: list[dict], messages: list[dict], max_tokens: int) -> dict:
        data = _post(API_URL, {"model": settings.ASSISTANT_MODEL, "max_tokens": max_tokens, "system": system,
                               "tools": tools, "messages": messages},
                     {"x-api-key": settings.ANTHROPIC_API_KEY, "anthropic-version": API_VERSION})
        if not isinstance(data.get("content"), list):
            raise AssistantError("unexpected response")
        return data


class FakeClient:
    """Plays back `script`: each item is a response dict, or a callable(messages) returning one.

    Every call is kept in `calls` so tests can check exactly what would have been sent.
    """

    KEY_SETTING = "OPENAI_API_KEY"
    script: list = []
    calls: list = []

    def create(self, *, system, tools, messages, max_tokens):
        FakeClient.calls.append({"system": system, "tools": tools, "messages": json.loads(json.dumps(messages)),
                                 "max_tokens": max_tokens})
        if not FakeClient.script:
            raise AssistantError("nothing scripted")
        step = FakeClient.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step(messages) if callable(step) else step

    def read_image(self, *, instructions, image, schema, max_tokens):
        FakeClient.calls.append({"instructions": instructions, "image": image, "schema": schema,
                                 "max_tokens": max_tokens})
        if not FakeClient.script:
            raise AssistantError("nothing scripted")
        step = FakeClient.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    @staticmethod
    def image(data: dict, tokens=(1200, 80)) -> dict:
        return {"data": data, "usage": {"input_tokens": tokens[0], "output_tokens": tokens[1]}}

    @staticmethod
    def text(answer: str, tokens=(10, 5)) -> dict:
        return {"content": [{"type": "text", "text": answer}], "stop_reason": "end_turn",
                "usage": {"input_tokens": tokens[0], "output_tokens": tokens[1]}}

    @staticmethod
    def tool(name: str, args: dict, call_id: str = "call_1", tokens=(10, 5)) -> dict:
        return {"content": [{"type": "tool_use", "id": call_id, "name": name, "input": args}],
                "stop_reason": "tool_use", "usage": {"input_tokens": tokens[0], "output_tokens": tokens[1]}}
