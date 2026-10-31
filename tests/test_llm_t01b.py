"""T01b LLM-layer additions: step registry, reasoning tokens, background resume,
variant hash, subject privacy, details parsing, config guards, cache keys (offline).

DB-backed tests use the isolated muse_test database and skip when Postgres
is unreachable; HTTP never leaves the machine (httpx2.MockTransport).
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx2
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.errors import ConfigurationError
from app.llm import calls, replies
from app.models.llm import llm_calls

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
    reasoning_tokens: int = 0,
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
            "output_tokens_details": [{"reasoning_tokens": reasoning_tokens}],
            "total_tokens": input_tokens + output_tokens,
        },
    }


def _queued(rid: str = "resp_bg") -> dict[str, Any]:
    payload = _completed(rid=rid)
    payload["status"] = "queued"
    payload["output"] = []
    return payload


def _sessions() -> tuple[async_sessionmaker[AsyncSession], async_sessionmaker[AsyncSession]]:
    from conftest import require_postgres, test_database_url

    from app.db import create_session_factory

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
) -> Any:
    from app.providers.llm import MetaProvider

    seen: list[httpx2.Request] = []

    def wrapped(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return handler(request)

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(wrapped), base_url=BASE)
    provider = MetaProvider(session, settings or _settings(), http, ledger)
    return provider, seen


def test_step_registry_covers_brief_steps() -> None:
    from app.llm.registry import STEPS, validate_step_registry

    for step in (
        "intake",
        "researcher",
        "architect",
        "block_teach",
        "block_example",
        "block_check",
        "block_practice",
        "block_test",
        "reader",
        "saboteur",
        "translator",
        "tutor",
    ):
        assert step in STEPS, f"step {step} missing from the registry"
        entry = STEPS[step]
        assert entry.effort in ("low", "medium", "high")
        assert entry.max_output_tokens > 0
    validate_step_registry()


def test_step_registry_rejects_bad_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.llm import registry
    from app.llm.registry import StepSpec, validate_step_registry

    monkeypatch.setitem(
        registry.STEPS,
        "broken",
        StepSpec(model="muse-spark-1.3", effort="huge", max_output_tokens=0),
    )
    with pytest.raises(ConfigurationError):
        validate_step_registry()


def test_request_body_carries_effort_and_tokens() -> None:
    from app.clients.meta import build_request_body

    body = build_request_body(
        model="muse-spark-1.3", prompt="hi", schema=SCHEMA, effort="high", max_output_tokens=800
    )
    assert body["reasoning"] == {"effort": "high"}
    assert body["max_output_tokens"] == 800


def test_reply_hash_covers_effort_tokens_and_variant() -> None:
    from app.clients.meta import build_request_body

    plain = build_request_body(model="muse-spark-1.3", prompt="hi", schema=SCHEMA)
    tuned = build_request_body(
        model="muse-spark-1.3", prompt="hi", schema=SCHEMA, effort="high", max_output_tokens=800
    )
    assert replies.request_hash("muse-spark-1.3", "v1", plain) != replies.request_hash(
        "muse-spark-1.3", "v1", tuned
    )
    assert replies.request_hash("muse-spark-1.3", "v1", plain, variant=1) != replies.request_hash(
        "muse-spark-1.3", "v1", plain
    )
    assert replies.request_hash("muse-spark-1.3", "v1", plain) == replies.request_hash(
        "muse-spark-1.3", "v1", plain, variant=0
    )


def test_reasoning_tokens_billed_as_output() -> None:
    from app.llm.registry import compute_cost_usd

    assert compute_cost_usd("muse-spark-1.3", 0, 0, 0, reasoning_tokens=1_000_000) == Decimal(
        "4.25"
    )
    assert compute_cost_usd("muse-spark-1.3", 0, 0, 500, reasoning_tokens=500) == compute_cost_usd(
        "muse-spark-1.3", 0, 0, 1000
    )


async def _make_user(session: AsyncSession) -> UUID:
    """One real account: subject_user_id is a foreign key to users(id)."""
    result = await session.execute(
        sa.text("INSERT INTO users (email, display_name) VALUES (:email, 't01b') RETURNING id"),
        {"email": f"t01b-{uuid.uuid4().hex}@example.invalid"},
    )
    user_id = result.scalar()
    assert isinstance(user_id, uuid.UUID)
    return user_id


async def test_log_call_stores_reasoning_variant_and_subject() -> None:
    _, ledger = _sessions()
    async with ledger() as session:
        subject = await _make_user(session)
        call_id = await calls.log_call(
            session,
            agent="t01b",
            model="muse-spark-1.3",
            prompt_version="v1",
            input_tokens=100,
            cached_tokens=0,
            output_tokens=10,
            duration_ms=5,
            request={"prompt": "hi"},
            response={"ok": True},
            reasoning_tokens=7,
            variant=2,
            subject_user_id=subject,
        )
        row = (
            (await session.execute(sa.select(llm_calls).where(llm_calls.c.id == call_id)))
            .mappings()
            .one()
        )
        assert row["tokens_reasoning"] == 7
        assert row["variant"] == 2
        assert row["subject_user_id"] == subject
        assert row["cost_usd"] != Decimal("0")
        await session.rollback()


async def test_background_resume_posts_nothing_new() -> None:
    caller, ledger = _sessions()
    prompt = f"background {uuid.uuid4().hex}"
    posts = {"n": 0}

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method == "GET":
            return httpx2.Response(200, json=_completed(rid="resp_bg"))
        posts["n"] += 1
        body = json.loads(request.content.decode())
        assert body.get("background") is True
        return httpx2.Response(200, json=_queued())

    async with caller() as session:
        provider, seen = _provider(session, ledger, handler)
        # The process "dies" between create and retrieve: only the POST runs.
        call_id = await provider.start_background(agent="tutor", prompt=prompt, schema=SCHEMA)
        assert isinstance(call_id, UUID)
        assert posts["n"] == 1
        async with ledger() as reader:
            pending = (
                (await reader.execute(sa.select(llm_calls).where(llm_calls.c.id == call_id)))
                .mappings()
                .one()
            )
            assert pending["status"] == "pending"
            assert pending["response_id"] == "resp_bg"
        # Resume polls the stored response id instead of re-posting.
        data = await provider.resume_pending(call_id)
        assert data == {"ok": True}
        assert posts["n"] == 1, "resume must not issue a second paid POST"
        async with ledger() as reader:
            rows = (
                (await reader.execute(sa.select(llm_calls).where(llm_calls.c.id == call_id)))
                .mappings()
                .all()
            )
            assert len(rows) == 1
            assert rows[0]["status"] == "completed"
            assert rows[0]["tokens_input"] == 100
        assert sum(1 for request in seen if request.method == "POST") == 1


async def test_variant_zero_reuses_store_variant_one_reposts() -> None:
    caller, ledger = _sessions()
    prompt = f"variant {uuid.uuid4().hex}"
    posts = {"n": 0}

    def handler(request: httpx2.Request) -> httpx2.Response:
        posts["n"] += 1
        return httpx2.Response(200, json=_completed())

    async with caller() as session:
        provider, _ = _provider(session, ledger, handler)
        first = await provider.generate_json(agent="tutor", prompt=prompt, schema=SCHEMA)
        second = await provider.generate_json(agent="tutor", prompt=prompt, schema=SCHEMA)
        assert second == first
        assert posts["n"] == 1
        # An explicit new draft pays for a fresh answer.
        third = await provider.generate_json(agent="tutor", prompt=prompt, schema=SCHEMA, variant=1)
        assert third == {"ok": True}
        assert posts["n"] == 2


async def test_subject_calls_skip_reply_store_and_wipe_keeps_cost() -> None:
    caller, ledger = _sessions()
    prompt = f"learner {uuid.uuid4().hex}"

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=_completed())

    async with ledger() as setup:
        subject = await _make_user(setup)
        await setup.commit()
    async with caller() as session:
        provider, _ = _provider(session, ledger, handler)
        assert await provider.generate_json(
            agent="tutor", prompt=prompt, schema=SCHEMA, subject_user_id=subject
        ) == {"ok": True}
    async with ledger() as session:
        rows = (
            (
                await session.execute(
                    sa.select(llm_calls).where(llm_calls.c.subject_user_id == subject)
                )
            )
            .mappings()
            .all()
        )
        assert len(rows) == 1
        cost_before = rows[0]["cost_usd"]
        # Learner text never lands in the shared reply store: a repeat call pays again.
        from app.clients.meta import build_request_body
        from app.llm.registry import resolve_step

        model, effort, max_tokens = resolve_step("tutor", None, None, None)
        body = build_request_body(
            model=model,
            prompt=prompt,
            schema=SCHEMA,
            effort=effort,
            max_output_tokens=max_tokens,
        )
        version = provider.prompt_version("tutor", prompt)
        assert (
            await replies.get_reply(session, replies.request_hash(model, version, body, variant=0))
            is None
        )
        wiped = await calls.wipe_subject_calls(session, subject)
        assert wiped == 1
        row = (
            (await session.execute(sa.select(llm_calls).where(llm_calls.c.id == rows[0]["id"])))
            .mappings()
            .one()
        )
        assert row["cost_usd"] == cost_before
        assert row["response"] is None
        assert row["subject_user_id"] is None
        await session.rollback()


async def test_parse_reads_object_dict_and_list_details() -> None:
    from app.clients.meta import MetaClient

    class _Usage:
        def __init__(self, input_details: Any, output_details: Any) -> None:
            self.input_tokens = 100
            self.output_tokens = 10
            self.input_tokens_details = input_details
            self.output_tokens_details = output_details

    class _Response:
        def __init__(self, usage: Any) -> None:
            self.output_text = '{"ok": true}'
            self.usage = usage

    class _Details:
        def __init__(self, **kwargs: Any) -> None:
            self.__dict__.update(kwargs)

    assert (
        MetaClient._parse(_Response(_Usage(_Details(cached_tokens=11), None))).cached_tokens == 11
    )
    assert (
        MetaClient._parse(
            _Response(_Usage({"cached_tokens": 22}, {"reasoning_tokens": 5}))
        ).cached_tokens
        == 22
    )
    parsed = MetaClient._parse(_Response(_Usage({"cached_tokens": 22}, {"reasoning_tokens": 5})))
    assert parsed.reasoning_tokens == 5
    parsed = MetaClient._parse(
        _Response(_Usage([{"cached_tokens": 33}], [{"reasoning_tokens": 6}]))
    )
    assert (parsed.cached_tokens, parsed.reasoning_tokens) == (33, 6)
    parsed = MetaClient._parse(
        _Response(_Usage([_Details(cached_tokens=44)], [_Details(reasoning_tokens=7)]))
    )
    assert (parsed.cached_tokens, parsed.reasoning_tokens) == (44, 7)


def test_config_guards_reject_unsafe_prod() -> None:
    from app.config import validate_runtime_settings

    prod_db = _settings(app_env="prod", session_secret_key="k" * 32)
    with pytest.raises(ConfigurationError, match="DATABASE_URL"):
        validate_runtime_settings(prod_db)
    prod_key = _settings(
        app_env="prod",
        database_url="postgresql+asyncpg://prod:secret@db.example.com:5432/prod",
        session_secret_key="",
    )
    with pytest.raises(ConfigurationError, match="SESSION_SECRET_KEY"):
        validate_runtime_settings(prod_key)
    prod_dev = _settings(
        app_env="prod",
        database_url="postgresql+asyncpg://prod:secret@db.example.com:5432/prod",
        session_secret_key="k" * 32,
        llm_dev_mode=True,
    )
    with pytest.raises(ConfigurationError, match="LLM_DEV_MODE"):
        validate_runtime_settings(prod_dev)
    validate_runtime_settings(_settings(app_env="local"))
    validate_runtime_settings(
        _settings(
            app_env="test",
            database_url="postgresql+asyncpg://prod:secret@db.example.com:5432/prod",
            session_secret_key="k" * 32,
        )
    )


def test_every_secret_str_reaches_redact() -> None:
    from app.redact import redact, settings_secrets

    settings = Settings(
        _env_file=None,
        model_api_key="synthetic-model-key-abc",
        model_api_base_url=BASE,
        google_oauth_client_secret="synthetic-oauth-secret-abc",
        session_secret_key="synthetic-session-secret-abc",
    )
    secrets = settings_secrets(settings)
    assert "synthetic-model-key-abc" in secrets
    assert "synthetic-oauth-secret-abc" in secrets
    assert "synthetic-session-secret-abc" in secrets
    assert "synthetic-model-key-abc" not in redact("k=synthetic-model-key-abc", secrets=secrets)


def test_prompt_cache_key_scheme_and_hygiene(tmp_path: Any) -> None:
    from app.errors import ConfigurationError
    from app.llm.prompts import cache_key, validate_prompt_files

    assert cache_key("reader", "choice", "course-1") == "reader:choice:course-1"
    good = tmp_path / "reader"
    good.mkdir()
    (good / "system.md").write_text("Stable prefix with no identifiers.\n")
    validate_prompt_files(tmp_path)
    (good / "bad.md").write_text("Generated on 2026-10-31 for run_abc123.\n")
    with pytest.raises(ConfigurationError):
        validate_prompt_files(tmp_path)


def test_migration_head_has_t01b_columns() -> None:
    from conftest import require_postgres, test_database_url

    url = test_database_url()
    require_postgres(url)
    sync = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    engine = sa.create_engine(sync)
    try:
        columns = {col["name"] for col in sa.inspect(engine).get_columns("llm_calls")}
    finally:
        engine.dispose()
    assert {"tokens_reasoning", "response_id", "status", "variant", "subject_user_id"} <= columns
