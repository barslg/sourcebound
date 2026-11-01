"""Live smoke: real Meta Model API through the guarded transport.

Skipped without MODEL_API_KEY. Runs against the isolated test database
(migrated by the session fixture). The key is only read for presence;
it is never printed or logged.
"""

from __future__ import annotations

import uuid as uuid_lib
from decimal import Decimal

import httpx2
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from transport_guard import GuardedTransport

from app.config import get_settings
from app.db import create_session_factory
from app.models.llm import llm_calls, llm_replies
from app.providers.llm import MetaProvider

live = pytest.mark.live

SOURCE = """\
The glass gardens of Ferron were planted in 1911 by the harbour guild to
shelve seedlings from sea wind. Gardeners watered the beds with collected
rain and recorded each planting in the blue ledger kept at the gatehouse.

In 1934 the guild added the night house, a hall of dark glass where
moonlight seedlings were raised for the winter market. Entry cost two
copper tokens, and visitors were forbidden to touch the glass panes.

The gardens closed during the storm winter of 1962 and reopened in 1965
under the care of the Ferron school, which still keeps the blue ledger
in the gatehouse for visitors to sign.
"""


def _strict_object(properties: dict) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": sorted(properties),
        "additionalProperties": False,
    }


@live
async def test_live_structured_grounded_and_cached() -> None:
    from conftest import require_postgres, test_database_url

    settings = get_settings()
    if settings.model_api_key is None or not settings.model_api_key.get_secret_value():
        pytest.skip("no MODEL_API_KEY; live smoke needs the real Model API")
    url = test_database_url()
    require_postgres(url)
    assert settings.llm_default_model == "muse-spark-1.3"

    transport = GuardedTransport(settings)
    http_client = httpx2.AsyncClient(
        transport=transport, trust_env=False, base_url=settings.model_api_base_url
    )
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    ledger = create_session_factory(url)
    try:
        async with factory() as session:
            provider = MetaProvider(session, settings, http_client, ledger)
            # A per-run nonce keeps the reply store isolated across runs.
            nonce = uuid_lib.uuid4().hex[:8]
            block_prompt = f"Write a two-sentence text block headed 'Rain {nonce}'."

            async def count(table: sa.Table) -> int:
                result = await session.execute(sa.select(sa.func.count()).select_from(table))
                return int(result.scalar() or 0)

            rows_before = await count(llm_calls)
            stored_before = await count(llm_replies)

            async def spend_total() -> Decimal:
                result = await session.execute(
                    sa.select(sa.func.coalesce(sa.func.sum(llm_calls.c.cost_usd), 0))
                )
                return Decimal(str(result.scalar()))

            spend_before = await spend_total()

            # (a) One text block as strict JSON by a small schema.
            block_schema = _strict_object(
                {
                    "heading": {"type": "string"},
                    "body": {"type": "string"},
                }
            )
            first = await provider.generate_json(
                agent="smoke",
                prompt=block_prompt,
                schema=block_schema,
                cache_key="smoke-agent",
            )
            assert isinstance(first["heading"], str) and first["heading"]
            assert isinstance(first["body"], str) and first["body"]

            # (b) A question whose answer is absent from the materials.
            verdict_schema = _strict_object(
                {
                    "answer": {"type": "string"},
                    "found_in_materials": {"type": "boolean"},
                }
            )
            verdict = await provider.generate_json(
                agent="smoke",
                prompt=(
                    "Answer ONLY from the materials below. If the answer is "
                    "not in the materials, set found_in_materials to false "
                    "and say in answer that it is not in the materials.\n\n"
                    "Question: what did entry to the night house cost in "
                    f"silver coins (run {nonce})?\n\nMaterials:\n{SOURCE}"
                ),
                schema=verdict_schema,
                cache_key="smoke-agent",
            )
            assert verdict["found_in_materials"] is False
            assert "material" in verdict["answer"].lower()

            # (c) The identical call hits llm_replies and skips the API.
            calls_before = transport.calls
            repeat = await provider.generate_json(
                agent="smoke",
                prompt=block_prompt,
                schema=block_schema,
                cache_key="smoke-agent",
            )
            assert repeat == first
            assert transport.calls == calls_before

            # (d) Two questions over one long identical prefix reuse cache.
            # Meta caches long stable prefixes (measured 2026-10-31: an
            # identical ~1.7k-token prefix sent twice under one
            # prompt_cache_key reported 1605 cached_tokens on the second
            # call); a 19-token prompt never caches.
            long_materials = "\n\n".join([SOURCE] * 16)
            shared_prefix = (
                "Answer ONLY from the materials below. If the answer is "
                "not in the materials, set found_in_materials to false "
                "and say in answer that it is not in the materials.\n\n"
                f"Materials:\n{long_materials}\n\nQuestion: "
            )
            first_long_prompt = (
                f"{shared_prefix}what did entry to the night house cost (run {nonce})?"
            )
            first_long = await provider.generate_json(
                agent="smoke",
                prompt=first_long_prompt,
                schema=verdict_schema,
                cache_key="smoke-agent",
            )
            assert first_long["found_in_materials"] is True
            second_long_prompt = f"{shared_prefix}who keeps the blue ledger today (run {nonce})?"
            second_long = await provider.generate_json(
                agent="smoke",
                prompt=second_long_prompt,
                schema=verdict_schema,
                cache_key="smoke-agent",
            )
            assert second_long["found_in_materials"] is True
            row = (
                (
                    await session.execute(
                        sa.select(llm_calls).where(
                            llm_calls.c.request["input"].astext == second_long_prompt
                        )
                    )
                )
                .mappings()
                .one()
            )
            assert row["tokens_cached"] > 0

            assert transport.calls == 4
            assert await count(llm_calls) - rows_before == 5
            assert await count(llm_replies) - stored_before == 4
            assert await spend_total() - spend_before > 0
    finally:
        await engine.dispose()
        await http_client.aclose()
