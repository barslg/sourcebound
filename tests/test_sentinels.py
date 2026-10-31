"""Sentinel guard (permanent): no answer key or planted position leaves a learner route.

The seeded course carries a SENTINEL string in every block type's answer key.
A student reads every topic, submits a wrong answer to every block and the
joined responses must contain neither a sentinel nor a key field name.
"""

from __future__ import annotations

import json
from typing import Any

import demo_t05
import httpx2
import sqlalchemy as sa
from conftest import test_database_url
from sqlalchemy.ext.asyncio import AsyncSession
from t06_helpers import SENTINELS, World, http
from transport_guard import guarded_client

LEAKS = (
    "answer_key",
    "planted_",
    "mapping_by_index",
    "labels_by_index",
    "passages_by_index",
    "true_sentence",
    "fully_correct",
    "translated_answer_key",
)


async def test_no_learner_response_carries_a_key_or_a_planted_position(world: World) -> None:
    auth = world.cookies["student"]
    assert auth is not None
    texts: list[str] = []
    async with http(auth[0]) as client:
        topics = []
        for topic in (world.w["lesson_topic"], world.w["test_topic"]):
            response = await client.get(f"/api/v1/learn/topics/{topic}")
            texts.append(response.text)
            topics += response.json()["blocks"]
        texts.append((await client.get(f"/api/v1/learn/courses/{world.course}")).text)
        wrong: dict[str, dict[str, Any]] = {
            "choice": {"picked": 1},
            "matching": {"mapping": {"Provider": "oversee use"}},
            "cloze": {"blanks": ["x", "y"]},
            "scenario_practice": {"labels": {}, "passages": {}},
            "spot_the_error": {"sentence_id": 0, "kind": "scope"},
            "reading": {"acknowledged": False},
            "figure": {"acknowledged": False},
        }
        for block in topics:
            reply = await client.post(
                f"/api/v1/learn/blocks/{block['id']}/answer",
                json=wrong[block["block_type"]],
                headers={"X-CSRF-Token": auth[1]},
            )
            assert reply.status_code == 200, reply.text
            texts.append(reply.text)
    joined = "\n".join(texts)
    assert {b["block_type"] for b in topics} == set(wrong), "all seven types were served"
    for sentinel in SENTINELS:
        assert sentinel not in joined, sentinel
    for leak in LEAKS:
        assert leak not in joined, leak


async def test_reader_request_of_one_course_carries_none_of_another_courses_text(
    t03_session: AsyncSession,
) -> None:
    """Data isolation: two courses share topic and block ids, B's reader sees only B."""
    from app.config import Settings
    from app.db import create_session_factory
    from app.providers.llm import MetaProvider
    from app.runs import executor

    settings = Settings(
        _env_file=None,
        model_api_key="synthetic-test-key",
        model_api_base_url="https://api.meta.ai/v1",
        llm_spend_ceiling_usd=50.0,
        source_store_path="/tmp/muse-sentinel-sources",
    )
    session = t03_session
    courses: dict[str, Any] = {}
    for tag in ("a", "b"):
        courses[tag] = await demo_t05.setup_demo(
            session, settings=settings, email=demo_t05.fresh_email(f"scope-{tag}")
        )
        await demo_t05.write_demo_tree(session, course_id=courses[tag]["course_id"])
        await session.execute(
            sa.text(
                "UPDATE block_versions SET content = CAST(:c AS jsonb) WHERE id IN ("
                " SELECT b.current_version_id FROM blocks b JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :course AND b.plan_node_id IN ('b1', 'b2'))"
            ),
            {
                "c": json.dumps({"body": f"ONLY-COURSE-{tag.upper()}"}),
                "course": str(courses[tag]["course_id"]),
            },
        )
    await session.commit()
    inner = demo_t05.mock_demo_handler()
    prompts: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        prompts.append(str(json.loads(request.content.decode("utf-8")).get("input", "")))
        return inner(request)

    ledger = create_session_factory(test_database_url())
    http_client = guarded_client(settings, inner=httpx2.MockTransport(handler))

    def make(db: AsyncSession) -> MetaProvider:
        return MetaProvider(db, settings, http_client, ledger)

    run = await executor.new_run(
        session, course_id=courses["b"]["course_id"], kind="generation", dag=[]
    )
    await session.commit()
    await executor.read_topic_step(
        session,
        run_id=run,
        entry={
            "step_key": "reader:t1",
            "course_id": str(courses["b"]["course_id"]),
            "course_language": "en",
            "depends_on": [],
            "sources": [],
            "plan": {},
            "dossier": {},
            "blocks": [],
        },
        provider_factory=make,
    )
    reader = [p for p in prompts if p.startswith("# Reader")]
    assert len(reader) == 1
    assert "ONLY-COURSE-B" in reader[0], "the reader saw its own course's blocks"
    assert "ONLY-COURSE-A" not in reader[0], "the reader saw another course's block text"
