"""Sentinel guard (permanent): no answer key value or planted position leaves a learner route.

The seeded course carries sentinel VALUES in every key field of every block
type (correct option text, matching rights, blanks, tiers, true sentences).
A student reads every topic and answers every block wrong, partly and right;
no GET and no POST may carry a key value (the find-the-error true statement
of the answered exercise is the one reveal the brief allows).
"""

from __future__ import annotations

import json
from typing import Any

import demo_t05
import httpx2
import sqlalchemy as sa
from conftest import test_database_url as _db_url
from sqlalchemy.ext.asyncio import AsyncSession
from t06_helpers import (
    CLOZE_BLANKS,
    KEY_ONLY_VALUES,
    MATCH_RIGHTS,
    SENTINELS,
    SERVED_VALUES,
    TIERS,
    World,
    cookie_for,
    http,
    sign_in_new_user,
)
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


def _payloads(block: dict[str, Any], by_text: dict[str, str], chips: dict[str, str]) -> Any:
    """(label, answer) per outcome for one served block; values come from the seeded key."""
    kind = block["block_type"]
    left = [item["id"] for item in block["content"].get("left", [])]
    if kind == "choice":
        return [("wrong", {"picked": 1}), ("right", {"picked": 0})]
    if kind == "cloze":
        return [
            ("wrong", {"blanks": ["x", "y"]}),
            ("partial", {"blanks": [CLOZE_BLANKS[0], "y"]}),
            ("right", {"blanks": list(CLOZE_BLANKS)}),
        ]
    if kind == "matching":
        good = {left[0]: by_text[MATCH_RIGHTS[0]], left[1]: by_text[MATCH_RIGHTS[1]]}
        return [
            (
                "wrong",
                {"mapping": {left[0]: by_text[MATCH_RIGHTS[1]], left[1]: by_text[MATCH_RIGHTS[0]]}},
            ),
            ("partial", {"mapping": {left[0]: good[left[0]], left[1]: good[left[0]]}}),
            ("right", {"mapping": good}),
        ]
    if kind == "ordering":
        ids = [item["id"] for item in block["content"].get("items", [])]
        texts = {item["id"]: item["text"] for item in block["content"].get("items", [])}
        authored = [
            "Name the AI system in use",
            "Train the staff who use it",
            "Record the training",
        ]
        right = [next(i for i in ids if texts[i] == step) for step in authored]
        return [
            ("wrong", {"order": [right[2], right[0], right[1]]}),
            ("partial", {"order": [right[0], right[2], right[1]]}),
            ("right", {"order": right}),
        ]
    if kind == "scenario_practice":
        cites = {"0": chips["P1"], "1": chips["P2"]}
        return [
            ("wrong", {"labels": {}, "passages": {}}),
            ("partial", {"labels": {"0": TIERS[0], "1": "x"}, "passages": cites}),
            ("right", {"labels": {"0": TIERS[0], "1": TIERS[1]}, "passages": cites}),
        ]
    if kind == "spot_the_error":
        return [
            ("wrong", {"sentence_id": 0, "kind": "scope"}),
            ("partial", {"sentence_id": 1, "kind": "negation"}),
            ("right", {"sentence_id": 1, "kind": "scope", "corrected_value": "x"}),
            ("none", {"sentence_id": "none"}),
        ]
    return [("right", {"acknowledged": True})]


async def test_no_key_value_leaves_any_learner_route_on_a_wrong_partial_or_right_answer(
    world: World,
) -> None:
    """Sentinel VALUES sit in every key field of every type; none may come back.

    Key-only values (blanks, tiers, true sentences) appear in no GET and no
    POST; served values (the correct option text, the matching rights) appear
    in GET only. The one reveal is the find-the-error true statement of the
    exercise that was just answered. A correct answer by displayed ids scores 1.0.
    """
    auth = world.cookies["student"]
    assert auth is not None
    await _walk_without_leaks(world, auth)


async def test_a_signed_in_user_without_an_invite_walks_a_public_course_without_a_leak(
    world: World,
) -> None:
    """Public course: a first-time sign-in with no invite reads and answers every block.

    The owner flips the course public through the route; the visitor holds only
    the empty student grant sign-in gives. The same walk as the invited student
    runs, and no key value leaves any route.
    """
    owner = world.cookies["owner"]
    assert owner is not None
    async with http(owner[0]) as client:
        flipped = await client.patch(
            f"/api/v1/courses/{world.course}/visibility",
            json={"public": True},
            headers={"X-CSRF-Token": owner[1]},
        )
    assert flipped.status_code == 200, flipped.text
    visitor = await sign_in_new_user(world.s, "visitor")
    auth = await cookie_for(world.s, visitor)
    await _walk_without_leaks(world, auth)


async def _walk_without_leaks(world: World, auth: tuple[dict[str, str], str]) -> None:
    spot_keys = {
        b["block_id"]: b["key"] for b in world.w["blocks"].values() if b["type"] == "spot_the_error"
    }
    own_true = {
        block_id: key["true_sentence"]
        for block_id, key in spot_keys.items()
        if not key.get("fully_correct")
    }
    all_true = [key["true_sentence"] for key in spot_keys.values()]
    gets: list[str] = []
    posts: list[tuple[str, str, str]] = []
    async with http(auth[0]) as client:
        topics = []
        for topic in (world.w["lesson_topic"], world.w["test_topic"]):
            response = await client.get(f"/api/v1/learn/topics/{topic}")
            gets.append(response.text)
            topics += response.json()["blocks"]
        gets.append((await client.get(f"/api/v1/learn/courses/{world.course}")).text)
        assert {b["block_type"] for b in topics} == {
            "reading",
            "figure",
            "diagram",
            "choice",
            "matching",
            "cloze",
            "ordering",
            "scenario_practice",
            "spot_the_error",
        }, "all nine types were served"
        for block in topics:
            by_text = {r["text"]: r["id"] for r in block["content"].get("right", [])}
            chips = {c["handle"]: c["passage_id"] for c in block["citations"]}
            for label, body in _payloads(block, by_text, chips):
                reply = await client.post(
                    f"/api/v1/learn/blocks/{block['id']}/answer",
                    json=body,
                    headers={"X-CSRF-Token": auth[1]},
                )
                assert reply.status_code == 200, (block["block_type"], label, reply.text)
                posts.append((block["id"], label, reply.text))
                if block["block_type"] in ("matching", "ordering") and label == "right":
                    assert reply.json()["score"] == 1.0, "displayed ids grade by displayed ids"
                if block["block_type"] == "ordering" and label == "partial":
                    assert 0.0 < reply.json()["score"] < 1.0, "one misplaced step is partial"
    joined_gets = "\n".join(gets)
    for value in KEY_ONLY_VALUES:
        assert value not in joined_gets, value
    for sentinel in SENTINELS:
        assert sentinel not in joined_gets and all(sentinel not in t for _, _, t in posts), sentinel
    for value in (*SERVED_VALUES, *KEY_ONLY_VALUES, *all_true):
        for block_id, label, text in posts:
            reveal = own_true.get(block_id)
            if value == reveal:
                continue
            assert value not in text, (value, block_id, label)
    for value in all_true:
        assert value not in joined_gets, value
    for leak in LEAKS:
        assert leak not in joined_gets, leak


async def test_matching_answered_by_authored_index_is_refused(world: World) -> None:
    """A learner never sees authored order: mapping_by_index answers 422."""
    auth = world.cookies["student"]
    assert auth is not None
    async with http(auth[0]) as client:
        reply = await client.post(
            f"/api/v1/learn/blocks/{world.w['by_type']['matching']['block_id']}/answer",
            json={"mapping_by_index": {"0": 0, "1": 1}},
            headers={"X-CSRF-Token": auth[1]},
        )
    assert reply.status_code == 422, reply.text


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
        body = json.loads(request.content.decode("utf-8"))
        if str(body.get("instructions", "")).startswith("# Reader"):
            prompts.append(str(body.get("input", "")))
        return inner(request)

    ledger = create_session_factory(_db_url())
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
    reader = list(prompts)
    assert len(reader) == 1
    assert "ONLY-COURSE-B" in reader[0], "the reader saw its own course's blocks"
    assert "ONLY-COURSE-A" not in reader[0], "the reader saw another course's block text"
