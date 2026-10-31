"""T05 resume: a planted 500 stops the run; resume pays nothing twice.

A generation DAG (two block steps plus a reader) drives to a stop
marker on a planted 500; resume finishes it. Done steps record no new
paid call on resume, and a second identical run is served free from
the reply store.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from uuid import UUID

import httpx2
import sqlalchemy as sa
from conftest import test_database_url as _test_database_url
from sqlalchemy.ext.asyncio import AsyncSession
from transport_guard import guarded_client

from app.config import Settings
from app.providers.llm import MetaProvider
from app.services import courses as course_service

BASE = "https://api.meta.ai/v1"


def _settings(**overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        model_api_key="synthetic-test-key",
        model_api_base_url=BASE,
        llm_spend_ceiling_usd=50.0,
        source_store_path="/tmp/muse-t05-sources",
        **overrides,
    )


def _completed(text: str) -> dict[str, Any]:
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


def _block_reply() -> dict[str, Any]:
    return {
        "block_type": "reading",
        "text": "Deployers must train staff.",
        "citations": [{"passage_id": "P1", "quote": "train staff"}],
        "answer": "key",
    }


def _reader_reply() -> dict[str, Any]:
    from app.agents.reader import RUBRIC_ITEMS

    return {
        "verdicts": [
            {
                "plan_node_id": "b1",
                "verdict": "pass",
                "items": [
                    {"item": item, "status": "pass", "evidence": f"quote {item}"}
                    for item in RUBRIC_ITEMS
                ],
            }
        ]
    }


def _mock_handler(fail_reader: bool = False) -> Any:
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content.decode("utf-8"))
        prompt = str(body.get("input", ""))
        seen.append(prompt[:20])
        if fail_reader and prompt.startswith("# Reader"):
            return httpx2.Response(500, json={"error": {"message": "planted 500"}})
        if prompt.startswith("# Reader"):
            return httpx2.Response(200, json=_completed(json.dumps(_reader_reply())))
        return httpx2.Response(200, json=_completed(json.dumps(_block_reply())))

    handler.seen = seen  # type: ignore[attr-defined]
    return handler


def _provider_factory(http: httpx2.AsyncClient, ledger: Any, settings: Settings) -> Any:
    def make(session: AsyncSession) -> MetaProvider:
        return MetaProvider(session, settings, http, ledger)

    return make


def _dag(course_id: UUID) -> list[dict[str, Any]]:
    return [
        {
            "step_key": "block_teach:b1",
            "handler": "generate_block",
            "depends_on": [],
            "block_type": "reading",
            "course_id": str(course_id),
            "course_language": "en",
            "sources": [],
            "plan": {},
            "dossier": {},
            "skeleton": {"plan_node_id": "b1"},
        },
        {
            "step_key": "reader:t1",
            "handler": "read_topic",
            "depends_on": ["block_teach:b1"],
            "course_id": str(course_id),
            "course_language": "en",
            "sources": [],
            "plan": {},
            "dossier": {},
            "blocks": [{"plan_node_id": "b1"}],
        },
    ]


async def _drive(
    session: AsyncSession, run_id: UUID, handler: Any, settings: Settings
) -> dict[str, Any]:
    from app.db import create_session_factory
    from app.runs import executor

    url = _test_database_url()
    ledger = create_session_factory(url)
    http = guarded_client(settings, inner=httpx2.MockTransport(handler))
    return await executor.drive_run(
        create_session_factory(url),
        run_id=run_id,
        provider_factory=_provider_factory(http, ledger, settings),
        handlers={
            "generate_block": executor.generate_block_step,
            "read_topic": executor.read_topic_step,
        },
    )


async def _paid_calls(session: AsyncSession, run_id: UUID) -> int:
    return int(
        (
            await session.execute(
                sa.text("SELECT count(*) FROM llm_calls WHERE run_id = :run AND cost_usd > 0"),
                {"run": str(run_id)},
            )
        ).scalar_one()
    )


async def test_generation_resume_pays_nothing_twice(t03_session: AsyncSession) -> None:
    from app.runs import executor

    user_id = UUID(
        str(
            (
                await t03_session.execute(
                    sa.text(
                        "INSERT INTO users (email, display_name) VALUES (:e, 'N') RETURNING id"
                    ),
                    {"e": f"t05-{uuid.uuid4().hex[:12]}@example.com"},
                )
            ).one()[0]
        )
    )
    course_id = await course_service.create_course(
        t03_session, owner_id=user_id, title="T05 resume", language="en"
    )
    await t03_session.commit()
    settings = _settings()
    dag = _dag(course_id)
    run_id = await executor.new_run(t03_session, course_id=course_id, kind="generation", dag=dag)
    await t03_session.commit()

    stopped = await _drive(t03_session, run_id, _mock_handler(fail_reader=True), settings)
    assert stopped["status"] == "stopped"
    paid_before = await _paid_calls(t03_session, run_id)
    assert paid_before >= 1

    await t03_session.execute(
        sa.text("UPDATE runs SET status = 'running', stop_marker = NULL WHERE id = :run"),
        {"run": str(run_id)},
    )
    await t03_session.commit()
    resumed = await _drive(t03_session, run_id, _mock_handler(), settings)
    assert resumed["status"] == "done"
    paid_after = await _paid_calls(t03_session, run_id)
    assert paid_after == paid_before + 1, "only the failed step pays on resume"
    block_paid = int(
        (
            await t03_session.execute(
                sa.text(
                    "SELECT count(*) FROM llm_calls"
                    " WHERE run_id = :run AND agent = 'block_teach' AND cost_usd > 0"
                ),
                {"run": str(run_id)},
            )
        ).scalar_one()
    )
    assert block_paid == 1, "done block steps are never repaid"


async def test_second_identical_generation_run_pays_zero(t03_session: AsyncSession) -> None:
    from app.runs import executor

    user_id = UUID(
        str(
            (
                await t03_session.execute(
                    sa.text(
                        "INSERT INTO users (email, display_name) VALUES (:e, 'N') RETURNING id"
                    ),
                    {"e": f"t05-{uuid.uuid4().hex[:12]}@example.com"},
                )
            ).one()[0]
        )
    )
    course_id = await course_service.create_course(
        t03_session, owner_id=user_id, title="T05 identical", language="en"
    )
    await t03_session.commit()
    settings = _settings()
    run_ids = []
    for _ in range(2):
        dag = _dag(course_id)
        run_ids.append(
            await executor.new_run(t03_session, course_id=course_id, kind="generation", dag=dag)
        )
        await t03_session.commit()
    first_handler = _mock_handler()
    first = await _drive(t03_session, run_ids[0], first_handler, settings)
    assert first["status"] == "done"
    assert first_handler.seen != []
    second_handler = _mock_handler()
    second = await _drive(t03_session, run_ids[1], second_handler, settings)
    assert second["status"] == "done"
    assert second_handler.seen == []
    assert await _paid_calls(t03_session, run_ids[1]) == 0
