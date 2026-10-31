"""MetaProvider: store-first reads, committed writes, per-step model (offline).

Every test drives the provider through an httpx2.MockTransport with a
canned Responses payload plus the real test-database tables, so the
reply-store short-circuit and the committed ledger writes are proved
without spending a call.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

import httpx2
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.clients.meta import build_request_body
from app.config import Settings
from app.db import create_session_factory
from app.errors import ForbiddenError
from app.llm import replies
from app.llm.prompts import version_of
from app.llm.registry import UnknownModelError
from app.models.llm import llm_calls
from app.providers.llm import MetaProvider

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


def _completed(*, text: str = '{"ok": true}') -> dict[str, Any]:
    return {
        "id": "resp_1",
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
            "input_tokens": 100,
            "output_tokens": 10,
            "input_tokens_details": [{"cached_tokens": 0}],
            "output_tokens_details": [{"reasoning_tokens": 0}],
            "total_tokens": 110,
        },
    }


def _sessions() -> tuple[async_sessionmaker[AsyncSession], async_sessionmaker[AsyncSession]]:
    from conftest import require_postgres, test_database_url

    url = test_database_url()
    require_postgres(url)
    engine = create_async_engine(url)
    caller = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return caller, create_session_factory(url)


def _provider(
    session: AsyncSession,
    ledger: async_sessionmaker[AsyncSession],
    handler: Any,
    settings: Settings | None = None,
) -> tuple[MetaProvider, list[httpx2.Request]]:
    seen: list[httpx2.Request] = []

    def wrapped(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return handler(request)

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(wrapped), base_url=BASE)
    return MetaProvider(session, settings or _settings(), http, ledger), seen


def _digest(agent: str, prompt: str, model: str = "muse-spark-1.3") -> tuple[str, str]:
    version = MetaProvider.prompt_version(agent, prompt)
    body = build_request_body(model=model, prompt=prompt, schema=SCHEMA)
    return replies.request_hash(model, version, body), version


async def test_provider_serves_a_seeded_reply_without_http() -> None:
    caller, ledger = _sessions()
    agent, prompt = "reader", f"seeded {uuid.uuid4().hex}"
    digest, version = _digest(agent, prompt)
    async with ledger() as writer:
        await replies.put_reply(writer, digest, "muse-spark-1.3", version, {"ok": True})
        await writer.commit()

    def explode(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("provider must not call HTTP on a store hit")

    async with caller() as session:
        provider, seen = _provider(session, ledger, explode)
        assert await provider.generate_json(agent=agent, prompt=prompt, schema=SCHEMA) == {
            "ok": True
        }
    assert seen == []


async def test_provider_writes_reply_and_call_log() -> None:
    caller, ledger = _sessions()
    agent, prompt = "author", f"fresh {uuid.uuid4().hex}"
    provider_calls = {"n": 0}

    def handler(request: httpx2.Request) -> httpx2.Response:
        provider_calls["n"] += 1
        return httpx2.Response(200, json=_completed())

    async with caller() as session:
        provider, _ = _provider(session, ledger, handler)
        assert await provider.generate_json(agent=agent, prompt=prompt, schema=SCHEMA) == {
            "ok": True
        }
    assert provider_calls["n"] == 1

    digest, _ = _digest(agent, prompt)
    async with ledger() as reader:
        assert await replies.get_reply(reader, digest) == {"ok": True}
        rows = (
            (await reader.execute(sa.select(llm_calls).where(llm_calls.c.agent == agent)))
            .mappings()
            .all()
        )
        match = [row for row in rows if row["request"].get("input") == prompt]
        assert len(match) == 1
        assert (match[0]["tokens_input"], match[0]["tokens_output"]) == (100, 10)
        assert match[0]["cost_usd"] != Decimal("0")


async def test_repeat_call_is_free_but_logged() -> None:
    caller, ledger = _sessions()
    agent, prompt = "author", f"repeat {uuid.uuid4().hex}"
    hits = {"n": 0}

    def handler(request: httpx2.Request) -> httpx2.Response:
        hits["n"] += 1
        return httpx2.Response(200, json=_completed())

    async with caller() as session:
        provider, _ = _provider(session, ledger, handler)
        first = await provider.generate_json(agent=agent, prompt=prompt, schema=SCHEMA)
        second = await provider.generate_json(agent=agent, prompt=prompt, schema=SCHEMA)
        assert second == first
    assert hits["n"] == 1
    async with ledger() as reader:
        stored = (
            (await reader.execute(sa.select(llm_calls).where(llm_calls.c.agent == agent)))
            .mappings()
            .all()
        )
        match = [row for row in stored if row["request"].get("input") == prompt]
        assert len(match) == 2
        assert match[1]["cost_usd"] == Decimal("0")


async def test_per_step_model_is_used_and_checked() -> None:
    caller, ledger = _sessions()
    prompt = f"wonder {uuid.uuid4().hex}"
    bodies: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content.decode()))
        payload = _completed()
        payload["model"] = "muse-wonder-1.0"
        return httpx2.Response(200, json=payload)

    async with caller() as session:
        provider, _ = _provider(session, ledger, handler)
        await provider.generate_json(
            agent="architect", prompt=prompt, schema=SCHEMA, model="muse-wonder-1.0"
        )
        with pytest.raises(UnknownModelError):
            await provider.generate_json(
                agent="architect", prompt=prompt, schema=SCHEMA, model="gpt-4o"
            )
        with pytest.raises(ForbiddenError):
            await provider.generate_json(
                agent="architect",
                prompt=prompt,
                schema=SCHEMA,
                model="muse-spark-1.3-contributor",
            )
    assert bodies and bodies[0]["model"] == "muse-wonder-1.0"
    async with ledger() as reader:
        models = set((await reader.execute(sa.select(llm_calls.c.model))).scalars().all())
    assert "muse-wonder-1.0" in models


async def test_prompt_cache_key_is_sent_and_scoped() -> None:
    caller, ledger = _sessions()
    prompt = f"cached {uuid.uuid4().hex}"
    bodies: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content.decode()))
        return httpx2.Response(200, json=_completed())

    async with caller() as session:
        provider, _ = _provider(session, ledger, handler)
        await provider.generate_json(
            agent="reader", prompt=prompt, schema=SCHEMA, cache_key="reader-v1"
        )
        await provider.generate_json(
            agent="reader", prompt=prompt, schema=SCHEMA, cache_key="reader-v2"
        )
    assert len(bodies) == 2
    assert bodies[0]["prompt_cache_key"] == "reader-v1"
    assert bodies[1]["prompt_cache_key"] == "reader-v2"


def test_prompt_version_uses_the_shared_hasher() -> None:
    assert MetaProvider.prompt_version("reader", "hello") == version_of("reader:hello")


async def test_failed_step_keeps_its_paid_call() -> None:
    caller, ledger = _sessions()
    prompt = f"doomed {uuid.uuid4().hex}"

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=_completed())

    async with ledger() as reader:
        before = await reader.execute(sa.select(sa.func.count()).select_from(llm_calls))
        rows_before = int(before.scalar() or 0)
    async with caller() as session:
        provider, _ = _provider(session, ledger, handler)
        await provider.generate_json(agent="author", prompt=prompt, schema=SCHEMA)
        await session.rollback()  # the caller flow fails after a paid call
    async with ledger() as reader:
        after = await reader.execute(sa.select(sa.func.count()).select_from(llm_calls))
        assert int(after.scalar() or 0) - rows_before == 1
        row = (
            (await reader.execute(sa.select(llm_calls).where(llm_calls.c.agent == "author")))
            .mappings()
            .all()
        )
        match = [r for r in row if r["request"].get("input") == prompt]
        assert len(match) == 1
        assert match[0]["cost_usd"] != Decimal("0")
