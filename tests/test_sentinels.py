"""Sentinel guard (permanent): no answer key or planted position leaves a learner route.

The seeded course carries a SENTINEL string in every block type's answer key.
A student reads every topic, submits a wrong answer to every block and the
joined responses must contain neither a sentinel nor a key field name.
"""

from __future__ import annotations

from typing import Any

from t06_helpers import SENTINELS, World, http

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
