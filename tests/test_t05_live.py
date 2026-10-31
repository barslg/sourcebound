"""T05 live demo: "AI literacy at work: the EU AI Act" on real Meta models.

Marked live: needs MODEL_API_KEY and spends real money under the
guarded spend ceiling. Builds the demo course from
tests/fixtures/eurlex_excerpt.md (1 module x 2 topics, blocks on
muse-spark-1.3, the reader on muse-wonder-1.0), then verifies every
block carries at least one verified citation, the spot_the_error block
holds exactly one planted sentence, and the run resumes after an
injected failure with zero new paid calls for done steps. The mock
twin in tests/test_t05_demo.py proves the same path spends nothing.
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
        source_store_path="/tmp/muse-t05-live-sources",
        llm_spend_ceiling_usd=25.0,
    )


def _provider_factory(settings: Any, ledger: Any) -> Any:
    from transport_guard import guarded_client

    from app.providers.llm import MetaProvider

    http = guarded_client(settings)

    def make(session: AsyncSession) -> MetaProvider:
        return MetaProvider(session, settings, http, ledger)

    return make


async def test_demo_course_ai_literacy_at_work(t03_session: AsyncSession) -> None:
    """Live generation: Spark blocks, Wonder reader, citations, resume."""
    from conftest import test_database_url as _db_url

    from app.db import create_session_factory
    from app.runs import executor
    from app.runs.executor import verify_citations

    key = os.environ.get("MODEL_API_KEY")
    if not key:
        pytest.skip("MODEL_API_KEY is not set")
    assert demo_t05.LICENCE_NOTE in demo_t05.EXCERPT
    settings = _live_settings(key)
    demo = await demo_t05.setup_demo(
        t03_session, settings=settings, email=demo_t05.fresh_email("live")
    )
    course_id: UUID = demo["course_id"]
    passages = demo["passages"]
    assert len(passages) >= 3
    tree = demo_t05.demo_tree()
    dossier = demo_t05.demo_dossier()
    planted = demo_t05.plant_spot_error(passages)
    dag = demo_t05.demo_dag(
        course_id=course_id,
        tree=tree,
        sources=demo_t05.demo_sources(passages),
        dossier=dossier,
        planted=planted,
    )
    run_id = await executor.new_run(t03_session, course_id=course_id, kind="generation", dag=dag)
    await t03_session.commit()

    url = _db_url()
    factory = create_session_factory(url)
    provider_factory = _provider_factory(settings, create_session_factory(url))
    partial = {
        name: handler
        for name, handler in demo_t05.generation_handlers().items()
        if name != "read_topic"
    }
    stopped = await executor.drive_run(
        factory, run_id=run_id, provider_factory=provider_factory, handlers=partial
    )
    assert stopped["status"] == "stopped"
    paid_before = await demo_t05.paid_by_step(t03_session, run_id=run_id)
    assert sum(slot["paid"] for slot in paid_before.values()) >= 1

    await t03_session.execute(
        sa.text("UPDATE runs SET status = 'running', stop_marker = NULL WHERE id = :run"),
        {"run": str(run_id)},
    )
    await t03_session.commit()
    final = await executor.drive_run(
        factory,
        run_id=run_id,
        provider_factory=provider_factory,
        handlers=demo_t05.generation_handlers(),
    )
    assert final["status"] == "done"
    paid_after = await demo_t05.paid_by_step(t03_session, run_id=run_id)
    for step_key, slot in paid_before.items():
        assert paid_after[step_key] == slot, f"resume repaid done step {step_key}"

    models = await demo_t05.model_by_agent(t03_session, run_id=run_id)
    assert models["block_teach"] == "muse-spark-1.3"
    assert models["reader"] == "muse-wonder-1.0"

    by_id = {item["id"]: item["text"] for item in passages}
    outputs = await demo_t05.block_outputs(t03_session, run_id=run_id)
    assert len(outputs) == 4
    for node_id, output in outputs.items():
        citations = output.get("citations", [])
        assert citations, f"block {node_id} has no citation"
        quoted = []
        for item in citations:
            assert item["passage_id"] in by_id, f"block {node_id} cites no passage"
            text = by_id[item["passage_id"]]
            if item["quote"] not in text:
                continue
            start = text.index(item["quote"])
            quoted.append(
                {
                    "passage_id": item["passage_id"],
                    "claim_path": f"blocks.{node_id}",
                    "quote": item["quote"],
                    "quote_start": start,
                    "quote_end": start + len(item["quote"]),
                }
            )
        assert quoted, f"block {node_id} has no verified citation"
        assert verify_citations(quoted, by_id) == []
    assert planted["sentences"].count(planted["planted_sentence"]) == 1

    second_id = await executor.new_run(t03_session, course_id=course_id, kind="generation", dag=dag)
    await t03_session.commit()
    second = await executor.drive_run(
        factory,
        run_id=second_id,
        provider_factory=provider_factory,
        handlers=demo_t05.generation_handlers(),
    )
    assert second["status"] == "done"
    paid_second = await demo_t05.paid_by_step(t03_session, run_id=second_id)
    assert sum(slot["paid"] for slot in paid_second.values()) == 0
