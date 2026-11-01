"""Anthropic test provider: schema -> structured-output request, reply -> parsed object.

Guards money (token usage becomes registry cost, cache reads billed at the
cached rate) and the tier map. The reply below is written by hand from the
Messages API response shape in the SDK docs; it is not a recorded call.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import httpx2
import pytest

from app.clients.anthropic import AnthropicClient, build_anthropic_body
from app.config import Settings
from app.errors import ConfigurationError, LLMError, UnparseableReplyError
from app.llm.registry import compute_cost_usd, serving_model

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "heading": {"type": "string", "minLength": 1},
        "kind": {"type": "string", "enum": ["a", "b"]},
    },
    "required": ["heading", "kind"],
    "additionalProperties": False,
}

# Hand-written from the documented Messages response shape (not a recorded call).
HAND_WRITTEN_REPLY: dict[str, Any] = {
    "id": "msg_handwritten",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-5-5",
    "content": [{"type": "text", "text": '{"heading": "Rain", "kind": "a"}'}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {
        "input_tokens": 100,
        "cache_creation_input_tokens": 50,
        "cache_read_input_tokens": 400,
        "output_tokens": 30,
    },
}


def _client(
    reply: dict[str, Any], seen: list[dict[str, Any]], status: int = 200
) -> AnthropicClient:
    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        return httpx2.Response(status, json=reply)

    settings = Settings(_env_file=None, anthropic_api_key="synthetic-test-key")  # type: ignore[arg-type]
    return AnthropicClient(settings, httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))


async def test_schema_becomes_structured_output_and_reply_parses() -> None:
    body = build_anthropic_body(
        model="claude-sonnet-5-5", prompt="p", schema=SCHEMA, prompt_cache_key="k", effort="low"
    )
    sent_schema = body["output_config"]["format"]["schema"]
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert sent_schema["type"] == "object" and sent_schema["additionalProperties"] is False
    assert sorted(sent_schema["required"]) == ["heading", "kind"]
    assert sent_schema["properties"]["kind"]["enum"] == ["a", "b"]
    assert "minLength" not in sent_schema["properties"]["heading"]
    assert body["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert (
        "cache_control"
        not in build_anthropic_body(model="claude-sonnet-5-5", prompt="p", schema=SCHEMA)[
            "messages"
        ][0]["content"][0]
    )

    seen: list[dict[str, Any]] = []
    answered = await _client(HAND_WRITTEN_REPLY, seen).respond_json(
        model="claude-sonnet-5-5", prompt="p", schema=SCHEMA, effort="low", max_output_tokens=500
    )
    assert seen[0]["model"] == "claude-sonnet-5-5" and seen[0]["max_tokens"] == 500
    assert answered.data == {"heading": "Rain", "kind": "a"}
    assert (answered.input_tokens, answered.cached_tokens, answered.output_tokens) == (550, 400, 30)
    # 150 fresh input at $2, 400 cached at $0.20, 30 output at $10 per 1M tokens.
    assert compute_cost_usd(
        "claude-sonnet-5-5", answered.input_tokens, answered.cached_tokens, answered.output_tokens
    ) == Decimal("0.000680")

    unparseable = {**HAND_WRITTEN_REPLY, "content": [{"type": "text", "text": "not json"}]}
    with pytest.raises(UnparseableReplyError) as bad:
        await _client(unparseable, []).respond_json(
            model="claude-sonnet-5-5", prompt="p", schema=SCHEMA
        )
    assert (bad.value.raw_text, bad.value.output_tokens) == ("not json", 30)
    refused = {**HAND_WRITTEN_REPLY, "stop_reason": "refusal"}
    with pytest.raises(LLMError):
        await _client(refused, []).respond_json(
            model="claude-sonnet-5-5", prompt="p", schema=SCHEMA
        )

    assert serving_model("muse-spark-1.3", "anthropic") == "claude-sonnet-5-5"
    assert serving_model("muse-wonder-1.0", "anthropic") == "claude-opus-5-5"
    assert serving_model("muse-spark-1.3", "meta") == "muse-spark-1.3"
    with pytest.raises(ConfigurationError):
        serving_model("muse-image-1.0", "anthropic")
