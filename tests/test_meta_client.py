"""Meta client: a background reply that ran out of output budget is a paid
truncation (usage kept, re-ask possible), not a dead model service.

Probe for the Muse API course run: block_exercise m5-test-b2 moved to the
background after a 504, ended `incomplete` (max_output_tokens) and the run
stopped with "The model service did not answer" — the foreground path would
have re-asked with twice the budget.
"""

from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest

from app.clients.meta import MetaClient
from app.config import Settings
from app.errors import LLMError, UnparseableReplyError

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"heading": {"type": "string"}},
    "required": ["heading"],
    "additionalProperties": False,
}


def _response(status: str, text: str, output_tokens: int) -> dict[str, Any]:
    return {
        "id": "resp_bg",
        "object": "response",
        "created_at": 1761878400,
        "status": status,
        "model": "muse-spark-1.3",
        "output": [
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "status": status,
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "usage": {
            "input_tokens": 100,
            "output_tokens": output_tokens,
            "input_tokens_details": [{"cached_tokens": 0}],
            "output_tokens_details": [{"reasoning_tokens": output_tokens - 5}],
            "total_tokens": 100 + output_tokens,
        },
    }


def _client(final_status: str, text: str, output_tokens: int) -> MetaClient:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method == "POST":
            body = json.loads(request.content)
            if not body.get("background"):
                return httpx2.Response(504, json={"error": {"message": "gateway timeout"}})
            return httpx2.Response(200, json=_response("queued", "", 0))
        return httpx2.Response(200, json=_response(final_status, text, output_tokens))

    settings = Settings(_env_file=None, model_api_key="synthetic-test-key")  # type: ignore[arg-type]
    return MetaClient(settings, httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))


async def test_background_reply_cut_at_the_budget_keeps_its_usage() -> None:
    client = _client("incomplete", '{"heading": "Rain', 12000)
    with pytest.raises(UnparseableReplyError) as caught:
        await client.respond_json(
            model="muse-spark-1.3", prompt="p", schema=SCHEMA, max_output_tokens=12000
        )
    assert caught.value.output_tokens == 12000
    assert caught.value.reasoning_tokens == 11995


async def test_background_reply_that_failed_is_a_model_error() -> None:
    client = _client("failed", "", 0)
    with pytest.raises(LLMError, match="ended as failed"):
        await client.respond_json(
            model="muse-spark-1.3", prompt="p", schema=SCHEMA, max_output_tokens=12000
        )
