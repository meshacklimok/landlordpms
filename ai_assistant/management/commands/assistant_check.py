"""Checks the assistant's provider with one made-up question (D-115): the key, the model and a tool call.

No organization data is sent and nothing is saved. It costs a fraction of a cent.
"""

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ai_assistant import client

TOOL = {"name": "rent_owed", "description": "How much rent a unit owes.",
        "input_schema": {"type": "object", "properties": {"unit": {"type": "string"}}, "required": ["unit"]}}
SYSTEM = "You answer landlords' questions using the tools. Answer in one short sentence."
QUESTION = "How much does unit A1 owe?"
FAKE_RESULT = {"unit": "A1", "owed": 12500, "currency": "KES"}


class Command(BaseCommand):
    help = "Send one made-up question to the assistant's provider and check the answer, the tool call and the cost."

    def handle(self, *args, **options):
        if not client.enabled():
            raise CommandError(f"No key: set the key for {settings.ASSISTANT_BACKEND} in .env.")
        model = client.get_client()
        messages = [{"role": "user", "content": QUESTION}]
        tokens = [0, 0]

        def call():
            try:
                out = model.create(system=SYSTEM, tools=[TOOL], messages=messages, max_tokens=256)
            except client.AssistantError as exc:
                raise CommandError(f"The provider failed: {exc}") from exc
            tokens[0] += out["usage"]["input_tokens"]
            tokens[1] += out["usage"]["output_tokens"]
            return out

        first = call()
        calls = [b for b in first["content"] if b["type"] == "tool_use"]
        if first["stop_reason"] != "tool_use" or not calls:
            raise CommandError("The model answered without using the tool: " + json.dumps(first["content"]))
        self.stdout.write(f"Tool call: {calls[0]['name']}({json.dumps(calls[0]['input'])})")
        messages.append({"role": "assistant", "content": first["content"]})
        messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": c["id"],
                                                      "content": json.dumps(FAKE_RESULT)} for c in calls]})
        answer = " ".join(b["text"] for b in call()["content"] if b["type"] == "text").strip()
        if not answer:
            raise CommandError("The model gave an empty answer after the tool result.")
        self.stdout.write(f"Answer: {answer}")
        self.stdout.write(f"Model: {settings.ASSISTANT_MODEL}. Tokens: {tokens[0]} in, {tokens[1]} out.")
        self.stdout.write(self.style.SUCCESS("The assistant's provider works."))
