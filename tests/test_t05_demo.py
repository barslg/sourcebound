"""T05 demo twin: the full EU AI Act path on the guarded mock transport.

Runs the shared demo builder (tests/demo_t05.py) without spending:
1 module x 2 topics, every block with a verified citation, exactly one
planted spot-the-error sentence, resume after an injected fault with
zero new paid calls for done steps, and zero spend on a second run.
The live test repeats the same assertions against real Meta models.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import demo_t05
import httpx2
import sqlalchemy as sa
from conftest import test_database_url as _db_url
from sqlalchemy.ext.asyncio import AsyncSession


def _settings(**overrides: Any) -> Any:
    from app.config import Settings

    return Settings(
        _env_file=None,
        model_api_key="synthetic-test-key",
        model_api_base_url="https://api.meta.ai/v1",
        llm_spend_ceiling_usd=50.0,
        source_store_path="/tmp/muse-t05-demo",
        **overrides,
    )


async def _drive(
    run_id: UUID, handler: Any, settings: Any, handlers: dict[str, Any]
) -> dict[str, Any]:
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


async def _verify_demo(
    session: AsyncSession, *, run_id: UUID, planted: dict[str, Any], passages: list[Any]
) -> None:
    from app.runs.executor import verify_citations

    by_id = {item["id"]: item["text"] for item in passages}
    outputs = await demo_t05.block_outputs(session, run_id=run_id)
    assert len(outputs) == 4
    for node_id, output in outputs.items():
        citations = output.get("citations", [])
        assert citations, f"block {node_id} has no citation"
        handle_to_id = {
            f"P{i}": item["id"]
            for i, item in enumerate(
                sorted(passages, key=lambda x: (x["source_id"], x["address"])), 1
            )
        }
        for citation in citations:
            pid = handle_to_id.get(str(citation["passage_id"]), str(citation["passage_id"]))
            assert pid in by_id, f"block {node_id} cites no passage"
        quoted = []
        for item in citations:
            text = by_id[handle_to_id.get(str(item["passage_id"]), str(item["passage_id"]))]
            start = text.index(item["quote"])
            quoted.append(
                {
                    "passage_id": pid,
                    "claim_path": f"blocks.{node_id}",
                    "quote": item["quote"],
                    "quote_start": start,
                    "quote_end": start + len(item["quote"]),
                }
            )
        holds = verify_citations(quoted, by_id)
        assert holds == [], f"block {node_id}: {holds}"
    assert planted["sentences"].count(planted["planted_sentence"]) == 1


async def test_demo_twin_end_to_end(t03_session: AsyncSession) -> None:
    """Mock twin: fault injection, resume, and second-run proofs."""
    from app.runs import executor

    settings = _settings()
    demo = await demo_t05.setup_demo(
        t03_session, settings=settings, email=demo_t05.fresh_email("twin")
    )
    course_id = demo["course_id"]
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

    partial = {
        name: handler
        for name, handler in demo_t05.generation_handlers().items()
        if name != "read_topic"
    }
    stopped = await _drive(run_id, demo_t05.mock_demo_handler(), settings, partial)
    assert stopped["status"] == "stopped"
    assert "reader:t1" in stopped["stop_marker"]
    paid_before = await demo_t05.paid_by_step(t03_session, run_id=run_id)
    assert sum(slot["paid"] for slot in paid_before.values()) == 2
    assert paid_before["block_teach:b1"]["paid"] == 1
    assert paid_before["block_check:b2"]["paid"] == 1

    await t03_session.execute(
        sa.text("UPDATE runs SET status = 'running', stop_marker = NULL WHERE id = :run"),
        {"run": str(run_id)},
    )
    await t03_session.commit()
    resumed = await _drive(
        run_id, demo_t05.mock_demo_handler(), settings, demo_t05.generation_handlers()
    )
    assert resumed["status"] == "done"
    paid_after = await demo_t05.paid_by_step(t03_session, run_id=run_id)
    for step_key, slot in paid_before.items():
        assert paid_after[step_key] == slot, f"resume repaid done step {step_key}"

    await _verify_demo(t03_session, run_id=run_id, planted=planted, passages=passages)

    second_id = await executor.new_run(t03_session, course_id=course_id, kind="generation", dag=dag)
    await t03_session.commit()
    handler = demo_t05.mock_demo_handler()
    second = await _drive(second_id, handler, settings, demo_t05.generation_handlers())
    assert second["status"] == "done"
    assert handler.seen == []
    paid_second = await demo_t05.paid_by_step(t03_session, run_id=second_id)
    assert sum(slot["paid"] for slot in paid_second.values()) == 0
