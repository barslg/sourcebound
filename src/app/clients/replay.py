"""Replay client: serves recorded model replies from stored ledgers, pays nothing.

A ledger is the JSON list the live acceptances export (`agent`, `model`,
`request`, `response` per call). A request is looked up by the same
content digest the reply store uses (`llm.replies.request_hash`), with
the prompt version pinned to a constant and the course-specific cache key
left out: the recorded request body already
embeds the complete system prompt, so any edit to a prompt changes the
digest and the request is a miss. A miss fails loudly with the step name
and never reaches the network. Caller: providers/llm.py, selected by
MODEL_REPLAY_LEDGER.
"""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.clients.body import build_body
from app.clients.meta import LLMResponse
from app.errors import ConfigurationError, ReplayMissError
from app.llm.replies import request_hash

REPLAY_PROMPT_VERSION = "ledger"


def replay_digest(model: str, body: dict[str, Any]) -> str:
    """The reply-store digest of one request body, prompt version pinned.

    `prompt_cache_key` is a billing hint that carries the course id, which
    differs on every fresh database; it never shapes the reply, so it is
    left out of the replay key.
    """
    content = {key: value for key, value in body.items() if key != "prompt_cache_key"}
    return request_hash(model, REPLAY_PROMPT_VERSION, content)


@lru_cache(maxsize=4)
def load_ledgers(paths: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """Digest -> recorded reply over every ledger file; a later file wins on a repeat."""
    replies: dict[str, dict[str, Any]] = {}
    for raw in paths:
        path = Path(raw)
        if not path.is_file():
            raise ConfigurationError(f"MODEL_REPLAY_LEDGER file not found: {raw}")
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise ConfigurationError(f"replay ledger {raw} is not a list of calls")
        for row in rows:
            request, response = row.get("request"), row.get("response")
            if isinstance(request, dict) and isinstance(response, dict) and row.get("model"):
                replies[replay_digest(str(row["model"]), request)] = response
    return replies


def parse_paths(value: str) -> tuple[str, ...]:
    """Comma-separated ledger paths from the setting."""
    return tuple(item.strip() for item in value.split(",") if item.strip())


class ReplayClient:
    """MetaClient look-alike over recorded replies; every call is free."""

    def __init__(self, ledger_paths: str) -> None:
        self._replies = load_ledgers(parse_paths(ledger_paths))

    async def respond_json(
        self,
        *,
        model: str,
        prompt: str,
        schema: dict[str, Any],
        background: bool = False,
        prompt_cache_key: str | None = None,
        effort: str = "medium",
        max_output_tokens: int = 4096,
    ) -> LLMResponse:
        """The recorded reply for this exact request, or ReplayMissError."""
        body = build_body(
            model=model,
            prompt=prompt,
            schema=schema,
            background=background,
            prompt_cache_key=prompt_cache_key,
            effort=effort,
            max_output_tokens=max_output_tokens,
        )
        digest = replay_digest(model, body)
        recorded = self._replies.get(digest)
        if recorded is None:
            raise ReplayMissError(
                f"replay mode: no recorded reply for this request (model {model}, request"
                f" {digest[:12]}); the demo plays only requests recorded in the ledger"
            )
        return LLMResponse(
            data=copy.deepcopy(recorded),
            input_tokens=0,
            cached_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
        )

    async def start_background(self, **_: Any) -> str:
        """Background calls are never recorded in a ledger."""
        raise ReplayMissError("replay mode: background calls are not recorded in the ledger")

    async def retrieve_background(self, response_id: str) -> LLMResponse:
        """Background calls are never recorded in a ledger."""
        raise ReplayMissError(f"replay mode: no background response {response_id}")
