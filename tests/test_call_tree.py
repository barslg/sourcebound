"""Probe: the course call tree over runs, a held block and a cached call."""

from __future__ import annotations

import json
import uuid
from typing import Any

import sqlalchemy as sa
from t06_helpers import World, http

from app.llm.calls import log_call
from app.runs.executor import new_run


def _walk(node: dict[str, Any]) -> list[dict[str, Any]]:
    return [node, *[leaf for child in node["children"] for leaf in _walk(child)]]


def _find(root: dict[str, Any], **want: Any) -> list[dict[str, Any]]:
    return [
        n
        for n in _walk(root)
        if all(n.get(k) == v or n["facts"].get(k) == v for k, v in want.items())
    ]


async def _step(world: World, run: uuid.UUID, key: str, deps: list[str]) -> uuid.UUID:
    row = (
        await world.s.execute(
            sa.text(
                "INSERT INTO run_steps (run_id, name, status, step_key, depends_on, input_digest)"
                " VALUES (:r, :n, 'done', :k, CAST(:d AS jsonb), 'd') RETURNING id"
            ),
            {"r": str(run), "n": key, "k": key, "d": json.dumps(deps)},
        )
    ).one()
    return uuid.UUID(str(row[0]))


async def _call(world: World, run: uuid.UUID | None, step: uuid.UUID | None, **over: Any) -> str:
    args: dict[str, Any] = {
        "agent": "block_teach",
        "model": "muse-spark-1.3",
        "prompt_version": "v1",
        "input_tokens": 1000,
        "cached_tokens": 0,
        "output_tokens": 2000,
        "duration_ms": 1500,
        "request": {"model": "muse-spark-1.3", "input": "REQUEST_BODY_TEXT"},
        "response": {"text": "REPLY_BODY_TEXT"},
        "run_id": run,
        "step_id": step,
        "response_id": "resp_" + uuid.uuid4().hex[:8],
    }
    args.update(over)
    call_id = await log_call(world.s, **args)
    await world.s.commit()
    return str(call_id)


async def _blocks_of_lesson(world: World) -> list[tuple[str, str]]:
    rows = (
        await world.s.execute(
            sa.text(
                "SELECT b.id, b.plan_node_id FROM blocks b WHERE b.topic_id = :t ORDER BY b.ordinal"
            ),
            {"t": str(world.w["lesson_topic"])},
        )
    ).all()
    return [(str(r[0]), str(r[1])) for r in rows]


async def _seed(world: World) -> dict[str, Any]:
    (b1_id, b1), (b2_id, b2), (b3_id, b3) = (await _blocks_of_lesson(world))[:3]
    topic_node = str(
        (
            await world.s.execute(
                sa.text("SELECT t.plan_node_id FROM topics t WHERE t.id = :t"),
                {"t": str(world.w["lesson_topic"])},
            )
        ).scalar_one()
    )
    module_node = "m-" + topic_node.split("-", 1)[1]
    plan = await new_run(world.s, course_id=world.w["course_id"], kind="plan", dag=[])
    await world.s.commit()
    dag = [
        {"step_key": f"block_teach:{b1}", "depends_on": []},
        {"step_key": f"block_example:{b2}", "depends_on": [f"block_teach:{b1}"]},
        {
            "step_key": f"block_exercise:{b3}",
            "depends_on": [f"block_teach:{b1}", f"block_example:{b2}"],
        },
    ]
    gen = await new_run(world.s, course_id=world.w["course_id"], kind="generation", dag=dag)
    await world.s.commit()
    rew = await new_run(world.s, course_id=world.w["course_id"], kind="rewrite", dag=[])
    await world.s.commit()
    foreign = await new_run(world.s, course_id=world.other["course_id"], kind="generation", dag=[])
    await world.s.commit()

    q = await _step(world, plan, "researcher:qaaa", [])
    res = await _call(world, plan, q, agent="researcher", response={"query": "duty of care"})
    await _call(world, plan, q, agent="web_search")
    arch_step = await _step(world, plan, "architect:plan", ["researcher:qaaa"])
    await _call(world, plan, arch_step, agent="outline")
    architect = await _call(
        world,
        plan,
        arch_step,
        agent="architect",
        response={
            "modules": [
                {
                    "plan_node_id": module_node,
                    "topics": [{"plan_node_id": topic_node, "blocks": []}],
                }
            ]
        },
    )

    s1 = await _step(world, gen, f"block_teach:{b1}", [])
    s2 = await _step(world, gen, f"block_example:{b2}", [f"block_teach:{b1}"])
    s3 = await _step(
        world, gen, f"block_exercise:{b3}", [f"block_teach:{b1}", f"block_example:{b2}"]
    )
    cached = await _call(world, gen, s1, input_tokens=0, output_tokens=0, duration_ms=0)
    await _call(world, gen, s2, agent="block_example")
    rejected = await _call(world, gen, s3, agent="block_exercise", status="rejected")
    await _call(world, gen, s3, agent="block_exercise")
    rd = await _step(world, gen, f"reader:{topic_node}", [])
    await _call(world, gen, rd, agent="reader")

    rs = await _step(world, rew, f"block_teach:{b1}", [])
    await _call(world, rew, rs)

    await _call(world, None, None, agent="tutor", subject_user_id=world.student)
    await _call(world, foreign, None, agent="architect")

    await world.s.execute(
        sa.text("UPDATE blocks SET status = 'held', hold_reason = :h WHERE id = :b"),
        {"h": "number: '85' appears in no passage; giveaway: option shape", "b": b3_id},
    )
    await world.s.execute(
        sa.text("UPDATE blocks SET status = 'accepted' WHERE id IN (:a, :b)"),
        {"a": b1_id, "b": b2_id},
    )
    await world.s.commit()
    return {
        "plan": str(plan),
        "gen": str(gen),
        "architect": architect,
        "cached": cached,
        "rejected": rejected,
        "research": res,
        "b1": b1,
        "b2": b2,
        "b3": b3,
        "b3_id": b3_id,
        "foreign": str(foreign),
    }


async def _get(world: World, who: str, url: str) -> Any:
    auth = world.cookies[who]
    async with http(auth[0] if auth else None) as client:
        return await client.get(url)


async def test_tree_shows_runs_chains_deps_holds_and_no_bodies(world: World) -> None:
    ids = await _seed(world)
    url = f"/api/v1/courses/{world.course}/calls/tree"

    shown = await _get(world, "owner", url)
    assert shown.status_code == 200, shown.text
    root = shown.json()
    assert root["kind"] == "course"
    assert "REQUEST_BODY_TEXT" not in shown.text and "REPLY_BODY_TEXT" not in shown.text

    runs = [c for c in root["children"] if c["kind"] == "run"]
    assert [r["facts"]["run_id"] for r in runs][:2] == [ids["plan"], ids["gen"]]
    assert ids["foreign"] not in shown.text
    assert root["facts"]["calls"] == 10
    assert len([n for n in _walk(root) if n["kind"] == "call"]) == 10
    assert all(n["facts"]["agent"] != "tutor" for n in _walk(root) if n["kind"] == "call")

    arch = _find(root, call_id=ids["architect"])[0]
    block3 = _find(root, kind="block", plan_node_id=ids["b3"])[0]
    block1 = _find(root, kind="block", plan_node_id=ids["b1"])
    gen_block1 = next(b for b in block1 if b["id"].startswith(f"run:{ids['gen']}"))
    gen_block2 = _find(root, kind="block", plan_node_id=ids["b2"])[0]
    assert block3["deps"] == [arch["id"], gen_block1["id"], gen_block2["id"]]
    assert gen_block1["deps"] == [arch["id"]]

    assert block3["status"] == "held"
    assert block3["facts"]["hold_lines"] == [
        "number: '85' appears in no passage",
        "giveaway: option shape",
    ]
    assert block3["facts"]["block_id"] == ids["b3_id"]
    assert gen_block1["status"] == "accepted"
    assert [c["status"] for c in block3["children"] if c["kind"] == "call"] == [
        "rejected",
        "completed",
    ]

    cached = _find(root, call_id=ids["cached"])[0]
    assert cached["facts"]["cached_reply"] is True
    assert _find(root, call_id=ids["rejected"])[0]["facts"]["cached_reply"] is False
    assert _find(root, call_id=ids["research"])[0]["facts"]["run_id"] == ids["plan"]

    reader = _find(root, kind="group", step_key=f"reader:{_topic_node_of(root, gen_block1)}")
    assert reader and reader[0]["deps"] == [gen_block1["id"], gen_block2["id"], block3["id"]]

    assert (await _get(world, "other_owner", url)).status_code == 404
    assert (await _get(world, "student", url)).status_code == 403
    assert (await _get(world, "anon", url)).status_code == 401


def _topic_node_of(root: dict[str, Any], block: dict[str, Any]) -> str:
    for n in _walk(root):
        if n["kind"] == "topic" and any(c["id"] == block["id"] for c in n["children"]):
            return str(n["facts"]["plan_node_id"])
    raise AssertionError("block has no topic")


async def test_tree_of_a_course_without_runs_is_an_empty_course_node(world: World) -> None:
    shown = await _get(world, "owner", f"/api/v1/courses/{world.course}/calls/tree")
    assert shown.status_code == 200, shown.text
    assert shown.json()["children"] == []
    assert shown.json()["facts"]["calls"] == 0
