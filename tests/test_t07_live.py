"""T07 live translation: demo course topics en -> nb and en -> uk.

Marked live: needs MODEL_API_KEY and spends real money under the
guarded spend ceiling. Translates the two demo topics (reading, choice,
spot_the_error) through the run executor, then asserts structure
parity, glossary use, the planted-error invariant, and zero spend on a
second identical run.
"""

from __future__ import annotations

import os
from typing import Any
from uuid import UUID

import demo_t05
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.live


def _live_settings(key: str) -> Any:
    from app.config import Settings

    return Settings(
        _env_file=None,
        model_api_key=key,
        source_store_path="/tmp/muse-t07-live-sources",
        llm_spend_ceiling_usd=25.0,
    )


def _provider_factory(settings: Any, ledger: Any) -> Any:
    from transport_guard import guarded_client

    from app.providers.llm import MetaProvider

    http = guarded_client(settings)

    def make(session: AsyncSession) -> MetaProvider:
        return MetaProvider(session, settings, http, ledger)

    return make


def _demo_blocks(passages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from app.course.errorspot import plant_error

    literacy = next(item for item in passages if "literacy" in item["text"])
    prohibition = next(item for item in passages if "prohibited" in item["text"])
    planted = plant_error(
        verified=[{"sentence": prohibition["text"], "passage": prohibition["id"]}],
        reader_holds=[],
        seed=3,
    )
    return [
        {
            "plan_node_id": "t1-b1",
            "block_type": "reading",
            "content": {
                "body": literacy["text"],
                "citations": [{"passage_id": literacy["id"], "quote": "AI literacy"}],
            },
            "answer_key": {"note": "key"},
        },
        {
            "plan_node_id": "t1-b2",
            "block_type": "choice",
            "content": {
                "stem": "Who must ensure AI literacy at work?",
                "options": ["Providers and deployers", "Nobody"],
            },
            "answer_key": {"correct": 0},
        },
        {
            "plan_node_id": "t2-b1",
            "block_type": "reading",
            "content": {
                "body": prohibition["text"],
                "citations": [{"passage_id": prohibition["id"], "quote": "prohibited"}],
            },
            "answer_key": {"note": "key"},
        },
        {
            "plan_node_id": "t2-b2",
            "block_type": "spot_the_error",
            "content": {
                "sentences": list(planted["sentences"]),
                "planted_index": int(planted["planted_index"]),
                "kind": str(planted["kind"]),
            },
            "answer_key": {
                "planted_sentence": str(planted["planted_sentence"]),
                "true_sentence": str(planted["true_sentence"]),
            },
        },
    ]


async def test_live_translation_nb_and_uk(t03_session: AsyncSession) -> None:
    from conftest import test_database_url as _db_url

    from app.db import create_session_factory
    from app.runs import executor
    from app.services import courses
    from app.translation.checks import verify_spot_error
    from app.translation.pipeline import translate_course

    key = os.environ.get("MODEL_API_KEY")
    if not key:
        pytest.skip("MODEL_API_KEY is not set")
    settings = _live_settings(key)
    demo = await demo_t05.setup_demo(
        t03_session, settings=settings, email=demo_t05.fresh_email("t07live")
    )
    course_id: UUID = demo["course_id"]
    version_id = await courses.snapshot_version(t03_session, course_id=course_id)
    await t03_session.commit()
    blocks = _demo_blocks(demo["passages"])
    by_node = {block["plan_node_id"]: block for block in blocks}
    glossary = {"deployer": "deployer", "literacy": "literacy"}

    url = _db_url()
    ledger = create_session_factory(url)
    make = _provider_factory(settings, ledger)
    handlers = {
        "translate_block": executor.translate_block_step,
        "translate_glossary": executor.translate_glossary_step,
    }

    async def driver(run_id: UUID) -> Any:
        return await executor.drive_run(
            create_session_factory(url),
            run_id=run_id,
            provider_factory=make,
            handlers=handlers,
        )

    run_id = await translate_course(
        t03_session,
        settings,
        course_id=course_id,
        version_id=version_id,
        source_language="en",
        target_languages=["nb", "uk"],
        blocks=blocks,
        glossary=glossary,
        driver=driver,
    )
    await t03_session.commit()
    for language in ("nb", "uk"):
        rows = (
            await t03_session.execute(
                sa.text(
                    "SELECT plan_node_id, status, reason, content FROM translation_blocks"
                    " JOIN course_translations ON translation_id = course_translations.id"
                    " WHERE course_version_id = :version AND language = :lang"
                ),
                {"version": str(version_id), "lang": language},
            )
        ).all()
        assert len(rows) == 4, language
        for node_id, status, reason, content in rows:
            assert status == "ok", f"block {node_id} [{language}] held/failed: {reason}"
            source = by_node[str(node_id)]["content"]
            if str(node_id) != "t2-b2":
                assert str(content) != str(source), f"{node_id} [{language}] untranslated"
        translated_spot = next(row[3] for row in rows if str(row[0]) == "t2-b2")
        assert verify_spot_error(by_node["t2-b2"]["content"], translated_spot, {}) == [], language
        stored_glossary = (
            await t03_session.execute(
                sa.text(
                    "SELECT glossary FROM course_translations"
                    " WHERE course_version_id = :version AND language = :lang"
                ),
                {"version": str(version_id), "lang": language},
            )
        ).scalar()
        assert set(stored_glossary or {}) == set(glossary), language
        assert any(value != key for key, value in (stored_glossary or {}).items()), (
            f"glossary unused [{language}]"
        )

    paid_first = (
        await t03_session.execute(
            sa.text("SELECT count(*) FROM llm_calls WHERE run_id = :run AND cost_usd > 0"),
            {"run": str(run_id)},
        )
    ).scalar()
    assert int(paid_first or 0) > 0

    run_id2 = await translate_course(
        t03_session,
        settings,
        course_id=course_id,
        version_id=version_id,
        source_language="en",
        target_languages=["nb", "uk"],
        blocks=blocks,
        glossary=glossary,
        driver=driver,
    )
    await t03_session.commit()
    paid_second = (
        await t03_session.execute(
            sa.text("SELECT count(*) FROM llm_calls WHERE run_id = :run AND cost_usd > 0"),
            {"run": str(run_id2)},
        )
    ).scalar()
    assert int(paid_second or 0) == 0
