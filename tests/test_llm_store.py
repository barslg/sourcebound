"""Call log + content-addressed reply store over the real tables."""

from __future__ import annotations

import uuid
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.llm import calls, replies
from app.models.llm import llm_calls


def test_request_hash_is_stable_and_scoped() -> None:
    body = {"prompt": "hello", "n": 1}
    first = replies.request_hash("muse-spark-1.3", "v1", body)
    assert first == replies.request_hash("muse-spark-1.3", "v1", body)
    assert first != replies.request_hash("muse-spark-1.3", "v2", body)
    assert first != replies.request_hash("muse-wonder-1.0", "v1", body)
    assert first != replies.request_hash("muse-spark-1.3", "v1", {"prompt": "bye"})


def test_request_hash_is_key_order_stable() -> None:
    left = replies.request_hash(
        "muse-spark-1.3", "v1", {"prompt": "hi", "schema": {"b": 1, "a": 2}}
    )
    right = replies.request_hash(
        "muse-spark-1.3", "v1", {"schema": {"a": 2, "b": 1}, "prompt": "hi"}
    )
    assert left == right


def _factory() -> async_sessionmaker[AsyncSession]:
    from conftest import require_postgres, test_database_url

    require_postgres(test_database_url())
    engine = create_async_engine(test_database_url())
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def test_log_call_records_cost_and_tokens() -> None:
    factory = _factory()
    async with factory() as session:
        call_id = await calls.log_call(
            session,
            agent="smoke",
            model="muse-spark-1.3",
            prompt_version="v1",
            input_tokens=1000,
            cached_tokens=0,
            output_tokens=500,
            duration_ms=120,
            request={"prompt": "hi"},
            response={"text": "hello"},
        )
        assert call_id is not None
        row = (
            (await session.execute(sa.select(llm_calls).where(llm_calls.c.id == call_id)))
            .mappings()
            .one()
        )
        assert row["tokens_input"] == 1000
        assert row["tokens_cached"] == 0
        assert row["tokens_output"] == 500
        # Spark: 1000 * 1.25 + 0 + 500 * 4.25, per 1M.
        assert row["cost_usd"] == Decimal("0.003375")
        assert await calls.total_spend_usd(session) >= Decimal("0.003375")
        await session.rollback()


async def test_log_call_keeps_cached_tokens_out_of_fresh_input() -> None:
    factory = _factory()
    async with factory() as session:
        call_id = await calls.log_call(
            session,
            agent="smoke",
            model="muse-spark-1.3",
            prompt_version="v1",
            input_tokens=1000,
            cached_tokens=100,
            output_tokens=500,
            duration_ms=120,
            request={"prompt": "hi"},
            response={"text": "hello"},
        )
        row = (
            (await session.execute(sa.select(llm_calls).where(llm_calls.c.id == call_id)))
            .mappings()
            .one()
        )
        assert row["tokens_cached"] == 100
        # Fresh 900 * 1.25 + cached 100 * 0.15 + out 500 * 4.25, per 1M.
        assert row["cost_usd"] == Decimal("0.003265")
        await session.rollback()


async def test_reply_store_roundtrip() -> None:
    factory = _factory()
    async with factory() as session:
        body = {"prompt": f"cache me {uuid.uuid4().hex}"}
        digest = replies.request_hash("muse-spark-1.3", "v1", body)
        assert await replies.get_reply(session, digest) is None
        await replies.put_reply(session, digest, "muse-spark-1.3", "v1", {"text": "cached"})
        assert await replies.get_reply(session, digest) == {"text": "cached"}
        await session.rollback()
