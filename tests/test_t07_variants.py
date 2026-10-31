"""T07 course variants on the guarded mock transport (no spend).

One course version translated en -> nb through the run executor:
per-block ok/held/failed rows, planted sentences absent from translation
memory, learner fallback with a visible notice, and a second identical
run with zero new paid calls and zero new HTTP.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import demo_t05
import httpx2
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


def _settings(**overrides: Any) -> Any:
    from app.config import Settings

    return Settings(
        _env_file=None,
        model_api_key="synthetic-test-key",
        model_api_base_url="https://api.meta.ai/v1",
        llm_spend_ceiling_usd=50.0,
        source_store_path="/tmp/muse-t07-demo",
        **overrides,
    )


def mock_translation_handler(language: str, *, break_planted: bool = False) -> Any:
    """Translate each segment as `<text> [<lang>]` with glossary applied."""

    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        from demo_t05 import _completed

        body = json.loads(request.content.decode("utf-8"))
        prompt = str(body.get("input", ""))
        seen.append(prompt[:20])
        if "## Terms\n" in prompt:
            terms = json.loads(prompt.split("## Terms\n", 1)[1].split("\n\n## ")[0])
            reply = {"segments": [{"id": term, "text": f"{term}·{language}"} for term in terms]}
            return httpx2.Response(200, json=_completed(json.dumps(reply)))
        assert "# Translator" in prompt, prompt[:60]
        glossary = json.loads(prompt.split("## Glossary\n", 1)[1].split("\n\n## ")[0])
        raw = prompt.split("## Segments\n", 1)[1]
        segments = json.loads(raw)
        out = []
        for seg in segments:
            text = str(seg["text"])
            for source, target in glossary.items():
                if source.lower() in text.lower():
                    text = text.replace(source, target)
            if break_planted and seg["id"].startswith("sentences["):
                text = "Anvendere må lære opp ansatte."
            else:
                text = f"{text} [{language}]"
            out.append({"id": seg["id"], "text": text})
        return httpx2.Response(200, json=_completed(json.dumps({"segments": out})))

    handler.seen = seen  # type: ignore[attr-defined]
    return handler


def _blocks(passages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from app.course.errorspot import plant_error

    prohibition = next(item for item in passages if "prohibited" in item["text"])
    planted = plant_error(
        verified=[
            {"sentence": prohibition["text"], "passage": prohibition["id"]},
            {"sentence": "Providers shall keep records.", "passage": prohibition["id"]},
        ],
        reader_holds=[],
        seed=3,
        kind="negation",
    )
    return [
        {
            "plan_node_id": "b1",
            "block_type": "reading",
            "content": {
                "body": "Providers and deployers must ensure AI literacy of staff.",
                "citations": [{"passage_id": prohibition["id"], "quote": "prohibited"}],
            },
            "answer_key": {"note": "key"},
        },
        {
            "plan_node_id": "b2",
            "block_type": "choice",
            "content": {
                "stem": "Who must ensure AI literacy at work?",
                "options": ["Providers and deployers", "Nobody"],
            },
            "answer_key": {"correct": 0},
        },
        {
            "plan_node_id": "b3",
            "block_type": "spot_the_error",
            "content": {"sentences": list(planted["sentences"])},
            "answer_key": {
                "planted_sentence": str(planted["planted_sentence"]),
                "planted_index": int(planted["planted_index"]),
                "kind": str(planted["kind"]),
                "true_sentence": str(planted["true_sentence"]),
            },
        },
    ]


async def _drive(run_id: UUID, handler: Any, settings: Any, handlers: dict[str, Any]) -> Any:
    from conftest import test_database_url as _db_url
    from transport_guard import guarded_client

    from app.db import create_session_factory
    from app.providers.llm import MetaProvider
    from app.runs import executor

    url = _db_url()
    ledger = create_session_factory(url)
    http = guarded_client(settings, inner=httpx2.MockTransport(handler))

    def make(session: AsyncSession) -> MetaProvider:
        return MetaProvider(session, settings, http, ledger)

    return await executor.drive_run(
        create_session_factory(url),
        run_id=run_id,
        provider_factory=make,
        handlers=handlers,
    )


async def _paid_calls(session: AsyncSession, *, run_id: UUID) -> int:
    rows = (
        await session.execute(
            sa.text("SELECT count(*) FROM llm_calls WHERE run_id = :run AND cost_usd > 0"),
            {"run": str(run_id)},
        )
    ).scalar()
    return int(rows or 0)


async def test_variants_statuses_memory_fallback_and_free_rerun(
    t03_session: AsyncSession,
) -> None:
    from app.runs import executor
    from app.services import courses
    from app.translation.pipeline import translate_course
    from app.translation.variants import resolve_learner_block

    settings = _settings()
    demo = await demo_t05.setup_demo(
        t03_session, settings=settings, email=demo_t05.fresh_email("t07")
    )
    course_id: UUID = demo["course_id"]
    version_id = await courses.snapshot_version(t03_session, course_id=course_id)
    await t03_session.commit()
    blocks = _blocks(demo["passages"])
    glossary = {"deployer": "deployer", "literacy": "literacy"}

    handler = mock_translation_handler("nb")
    run_id = await translate_course(
        t03_session,
        settings=settings,
        course_id=course_id,
        version_id=version_id,
        source_language="en",
        target_languages=["nb"],
        blocks=blocks,
        glossary=glossary,
        driver=lambda rid: _drive(
            rid,
            handler,
            settings,
            {
                "translate_block": executor.translate_block_step,
                "translate_glossary": executor.translate_glossary_step,
            },
        ),
    )
    await t03_session.commit()

    statuses = (
        await t03_session.execute(
            sa.text(
                "SELECT plan_node_id, tb.status FROM translation_blocks tb"
                " JOIN course_translations ON translation_id = course_translations.id"
                " WHERE course_version_id = :version AND language = 'nb'"
                " ORDER BY plan_node_id"
            ),
            {"version": str(version_id)},
        )
    ).all()
    assert [(row[0], row[1]) for row in statuses] == [
        ("b1", "ok"),
        ("b2", "ok"),
        ("b3", "ok"),
    ]
    body = (
        await t03_session.execute(
            sa.text(
                "SELECT content FROM translation_blocks"
                " JOIN course_translations ON translation_id = course_translations.id"
                " WHERE course_version_id = :version AND language = 'nb'"
                " AND plan_node_id = 'b1'"
            ),
            {"version": str(version_id)},
        )
    ).scalar()
    assert "deployer·nb" in str(body) and "[nb]" in str(body)

    planted_source = blocks[2]["content"]["sentences"][0]
    tm_hit = (
        await t03_session.execute(
            sa.text(
                "SELECT count(*) FROM translation_memory"
                " WHERE source_text = :text AND target_language = 'nb'"
            ),
            {"text": planted_source},
        )
    ).scalar()
    assert int(tm_hit or 0) == 0
    companion = blocks[2]["content"]["sentences"][1]
    tm_companion = (
        await t03_session.execute(
            sa.text(
                "SELECT translated_text FROM translation_memory"
                " WHERE source_text = :text AND target_language = 'nb'"
            ),
            {"text": companion},
        )
    ).scalar()
    assert tm_companion and "[nb]" in str(tm_companion)

    resolved = await resolve_learner_block(
        t03_session,
        course_id=course_id,
        version_id=version_id,
        language="uk",
        plan_node_id="b1",
        source_content=blocks[0]["content"],
    )
    assert resolved["language"] == "en"
    assert resolved["content"] == blocks[0]["content"]
    assert resolved["fallback_notice"]

    resolved_nb = await resolve_learner_block(
        t03_session,
        course_id=course_id,
        version_id=version_id,
        language="nb",
        plan_node_id="b1",
        source_content=blocks[0]["content"],
    )
    assert resolved_nb["language"] == "nb"
    assert resolved_nb["fallback_notice"] is None

    paid_first = await _paid_calls(t03_session, run_id=run_id)
    assert paid_first > 0
    seen_first = list(handler.seen)
    assert seen_first

    handler2 = mock_translation_handler("nb")
    run_id2 = await translate_course(
        t03_session,
        settings=settings,
        course_id=course_id,
        version_id=version_id,
        source_language="en",
        target_languages=["nb"],
        blocks=blocks,
        glossary=glossary,
        driver=lambda rid: _drive(
            rid,
            handler2,
            settings,
            {
                "translate_block": executor.translate_block_step,
                "translate_glossary": executor.translate_glossary_step,
            },
        ),
    )
    await t03_session.commit()
    assert await _paid_calls(t03_session, run_id=run_id2) == 0
    assert handler2.seen == []


async def test_fixed_planted_error_holds_the_block(t03_session: AsyncSession) -> None:
    import sqlalchemy as sa

    from app.runs import executor
    from app.services import courses
    from app.translation.pipeline import translate_course

    settings = _settings()
    demo = await demo_t05.setup_demo(
        t03_session, settings=settings, email=demo_t05.fresh_email("t07hold")
    )
    course_id: UUID = demo["course_id"]
    version_id = await courses.snapshot_version(t03_session, course_id=course_id)
    await t03_session.commit()
    blocks = _blocks(demo["passages"])
    handler = mock_translation_handler("nb", break_planted=True)
    await translate_course(
        t03_session,
        settings=settings,
        course_id=course_id,
        version_id=version_id,
        source_language="en",
        target_languages=["nb"],
        blocks=blocks,
        glossary={},
        driver=lambda rid: _drive(
            rid,
            handler,
            settings,
            {
                "translate_block": executor.translate_block_step,
                "translate_glossary": executor.translate_glossary_step,
            },
        ),
    )
    await t03_session.commit()
    status = (
        await t03_session.execute(
            sa.text(
                "SELECT tb.status, reason FROM translation_blocks tb"
                " JOIN course_translations ON translation_id = course_translations.id"
                " WHERE course_version_id = :version AND language = 'nb'"
                " AND plan_node_id = 'b3'"
            ),
            {"version": str(version_id)},
        )
    ).first()
    assert status is not None and status[0] == "held" and status[1]
