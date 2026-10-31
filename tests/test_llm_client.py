"""Meta client: retries, background escalation, timeouts, token math (all offline).

Every test drives MetaClient through an httpx2.MockTransport with canned
Responses payloads, so no packet ever leaves the machine; the session
guard ignores mock transports.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx2
import pytest

from app.clients.meta import MetaClient
from app.config import Settings
from app.errors import ConfigurationError, LLMError, http_status_for
from app.llm.schema import SchemaViolation

BASE = "https://api.meta.ai/v1"

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def _settings(**overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        model_api_key="synthetic-test-key",
        model_api_base_url=BASE,
        **overrides,
    )


def _completed(
    *,
    text: str = '{"ok": true}',
    rid: str = "resp_1",
    input_tokens: int = 100,
    cached_tokens: int = 0,
    output_tokens: int = 10,
) -> dict[str, Any]:
    return {
        "id": rid,
        "object": "response",
        "created_at": 1761878400,
        "status": "completed",
        "model": "muse-spark-1.3",
        "output": [
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "input_tokens_details": [{"cached_tokens": cached_tokens}],
            "output_tokens_details": [{"reasoning_tokens": 0}],
            "total_tokens": input_tokens + output_tokens,
        },
    }


def _queued(rid: str = "resp_bg") -> dict[str, Any]:
    payload = _completed(rid=rid)
    payload["status"] = "queued"
    payload["output"] = []
    return payload


def _client(
    handler: Any, settings: Settings | None = None
) -> tuple[MetaClient, list[httpx2.Request]]:
    seen: list[httpx2.Request] = []

    def wrapped(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return handler(request)

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(wrapped), base_url=BASE)
    return MetaClient(settings or _settings(), http), seen


def _error(status: int, retry_after: str | None = None) -> httpx2.Response:
    headers = {"retry-after": retry_after} if retry_after else {}
    request = httpx2.Request("POST", f"{BASE}/responses")
    return httpx2.Response(
        status, json={"error": {"message": "busy"}}, headers=headers, request=request
    )


def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return slept


async def test_success_returns_parsed_json() -> None:
    client, seen = _client(lambda request: httpx2.Response(200, json=_completed()))
    answered = await client.respond_json(model="muse-spark-1.3", prompt="hi", schema=SCHEMA)
    assert answered.data == {"ok": True}
    assert (answered.input_tokens, answered.cached_tokens, answered.output_tokens) == (100, 0, 10)
    assert len(seen) == 1


async def test_client_retries_a_503_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_sleep(monkeypatch)
    calls = {"n": 0}

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return _error(503)
        return httpx2.Response(200, json=_completed())

    client, _ = _client(handler)
    answered = await client.respond_json(model="muse-spark-1.3", prompt="hi", schema=SCHEMA)
    assert answered.data == {"ok": True}
    assert calls["n"] == 2


async def test_client_honours_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    slept = _no_sleep(monkeypatch)

    def handler(request: httpx2.Request) -> httpx2.Response:
        if not hasattr(handler, "hit"):
            handler.hit = True  # type: ignore[attr-defined]
            return _error(429, retry_after="30")
        return httpx2.Response(200, json=_completed())

    client, _ = _client(handler)
    await client.respond_json(model="muse-spark-1.3", prompt="hi", schema=SCHEMA)
    assert slept and slept[0] >= 30.0


async def test_exhausted_policy_raises_after_four_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_sleep(monkeypatch)
    calls = {"n": 0}

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls["n"] += 1
        return _error(500)

    client, _ = _client(handler)
    with pytest.raises(LLMError, match="4 attempt"):
        await client.respond_json(model="muse-spark-1.3", prompt="hi", schema=SCHEMA)
    assert calls["n"] == 4


async def test_client_errors_are_never_retried() -> None:
    calls = {"n": 0}

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls["n"] += 1
        return _error(400)

    client, _ = _client(handler)
    with pytest.raises(LLMError):
        await client.respond_json(model="muse-spark-1.3", prompt="hi", schema=SCHEMA)
    assert calls["n"] == 1


async def test_gateway_timeout_escalates_to_background() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method == "GET":
            return httpx2.Response(200, json=_completed(rid="resp_bg"))
        body = json.loads(request.content.decode())
        if body.get("background"):
            return httpx2.Response(200, json=_queued())
        return _error(504)

    client, seen = _client(handler)
    answered = await client.respond_json(model="muse-spark-1.3", prompt="hi", schema=SCHEMA)
    assert answered.data == {"ok": True}
    posts = [json.loads(request.content.decode()) for request in seen if request.method == "POST"]
    assert len(posts) == 2
    assert posts[1].get("background") is True


async def test_connection_error_goes_background_instead_of_reposting() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method == "GET":
            return httpx2.Response(200, json=_completed(rid="resp_bg"))
        body = json.loads(request.content.decode())
        if body.get("background"):
            return httpx2.Response(200, json=_queued())
        raise httpx2.ConnectError("dropped", request=request)

    client, seen = _client(handler)
    answered = await client.respond_json(model="muse-spark-1.3", prompt="hi", schema=SCHEMA)
    assert answered.data == {"ok": True}
    posts = [json.loads(request.content.decode()) for request in seen if request.method == "POST"]
    assert len(posts) == 2
    assert "background" not in posts[0]
    assert posts[1].get("background") is True


async def test_background_poll_failure_retries_the_poll_not_the_post() -> None:
    gets = {"n": 0}

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method == "GET":
            gets["n"] += 1
            if gets["n"] == 1:
                return _error(503)
            return httpx2.Response(200, json=_completed(rid="resp_bg"))
        body = json.loads(request.content.decode())
        if body.get("background"):
            return httpx2.Response(200, json=_queued())
        return _error(504)

    client, seen = _client(handler)
    answered = await client.respond_json(model="muse-spark-1.3", prompt="hi", schema=SCHEMA)
    assert answered.data == {"ok": True}
    assert sum(1 for request in seen if request.method == "POST") == 2
    assert gets["n"] == 2


async def test_client_keeps_cached_tokens() -> None:
    client, _ = _client(lambda request: httpx2.Response(200, json=_completed(cached_tokens=222)))
    answered = await client.respond_json(model="muse-spark-1.3", prompt="hi", schema=SCHEMA)
    assert answered.cached_tokens == 222
    assert answered.input_tokens == 100


async def test_invalid_schema_never_reaches_http() -> None:
    def explode(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("invalid schema must not reach HTTP")

    client, seen = _client(explode)
    bad = {
        "type": "object",
        "properties": {"x": {"oneOf": [{"type": "string"}]}},
        "required": ["x"],
        "additionalProperties": False,
    }
    with pytest.raises(SchemaViolation, match="oneOf"):
        await client.respond_json(model="muse-spark-1.3", prompt="hi", schema=bad)
    assert seen == []


def test_missing_key_is_a_configuration_error() -> None:
    with pytest.raises(ConfigurationError, match="MODEL_API_KEY"):
        MetaClient(Settings(_env_file=None, model_api_key=None))
    assert http_status_for(ConfigurationError("x")) == 500
