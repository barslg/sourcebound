"""Probe: the run's model-call log shows this run's calls only, to this run's owner only."""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from t06_helpers import World, http

from app.config import get_settings
from app.llm.calls import log_call
from app.runs.executor import new_run


async def _step(world: World, run: uuid.UUID, key: str) -> uuid.UUID:
    row = (
        await world.s.execute(
            sa.text(
                "INSERT INTO run_steps (run_id, name, status, step_key, depends_on, input_digest)"
                " VALUES (:r, :n, 'done', :k, '[]'::jsonb, 'd') RETURNING id"
            ),
            {"r": str(run), "n": key, "k": key},
        )
    ).one()
    return uuid.UUID(str(row[0]))


async def _call(world: World, run: uuid.UUID | None, step: uuid.UUID | None, **over: Any) -> Any:
    args: dict[str, Any] = {
        "agent": "architect",
        "model": "muse-spark-1.3",
        "prompt_version": "v1",
        "input_tokens": 1000,
        "cached_tokens": 0,
        "output_tokens": 2000,
        "duration_ms": 1500,
        "request": {
            "model": "muse-spark-1.3",
            "input": "plan the course",
            "instructions": "you are the architect",
            "safety_identifier": "hashed-learner",
            "prompt_cache_key": "k",
            "request_hash": "abc",
        },
        "response": {"modules": 3},
        "run_id": run,
        "step_id": step,
        "response_id": "resp_" + uuid.uuid4().hex[:8],
    }
    args.update(over)
    call_id = await log_call(world.s, **args)
    await world.s.commit()
    return call_id


async def _get(world: World, who: str, url: str) -> Any:
    auth = world.cookies[who]
    async with http(auth[0] if auth else None) as client:
        return await client.get(url)


async def _seed(world: World) -> dict[str, Any]:
    dag = [{"step_key": "architect", "handler": "none", "depends_on": []}]
    mine = await new_run(world.s, course_id=world.w["course_id"], kind="generate", dag=dag)
    theirs = await new_run(world.s, course_id=world.other["course_id"], kind="generate", dag=dag)
    await world.s.commit()
    mine_step = await _step(world, mine, "architect")
    theirs_step = await _step(world, theirs, "architect")
    real = await _call(world, mine, mine_step)
    replay = await _call(
        world,
        mine,
        mine_step,
        agent="block_teach",
        input_tokens=0,
        output_tokens=0,
        duration_ms=0,
    )
    await _call(
        world,
        None,
        None,
        agent="tutor",
        subject_user_id=world.student,
        request={"input": "LEARNER_QUESTION_TEXT"},
    )
    foreign = await _call(world, theirs, theirs_step)
    await world.s.commit()
    return {"mine": mine, "theirs": theirs, "real": real, "replay": replay, "foreign": foreign}


async def test_calls_list_is_this_runs_and_this_owners(world: World) -> None:
    ids = await _seed(world)
    base = f"/api/v1/runs/{ids['mine']}/calls"

    own = await _get(world, "owner", base)
    assert own.status_code == 200, own.text
    body = own.json()
    assert body["total"] == 2
    assert {c["agent"] for c in body["calls"]} == {"architect", "block_teach"}
    assert "LEARNER_QUESTION_TEXT" not in own.text
    by_agent = {c["agent"]: c for c in body["calls"]}
    assert by_agent["architect"]["cached_reply"] is False
    assert by_agent["block_teach"]["cached_reply"] is True
    assert by_agent["architect"]["step_key"] == "architect"
    assert by_agent["architect"]["display_name"] == "Muse Spark 1.3"
    assert by_agent["architect"]["endpoint"].endswith("/responses")
    summary = {row["agent"]: row for row in body["summary"]}
    assert summary["architect"]["calls"] == 1
    assert summary["architect"]["tokens_input"] == 1000
    assert summary["architect"]["tokens_output"] == 2000
    assert float(summary["architect"]["cost_usd"]) == 0.00975  # 1000*1.25/1e6 + 2000*4.25/1e6
    assert float(summary["block_teach"]["cost_usd"]) == 0

    assert (await _get(world, "other_owner", base)).status_code == 404
    assert (await _get(world, "student", base)).status_code == 403
    assert (await _get(world, "anon", base)).status_code == 401

    detail = f"{base}/{ids['real']}"
    shown = await _get(world, "owner", detail)
    assert shown.status_code == 200, shown.text
    assert shown.json()["request"]["input"] == "plan the course"
    assert shown.json()["response"] == {"modules": 3}
    for hidden in ("safety_identifier", "request_hash", "prompt_cache_key"):
        assert hidden not in shown.json()["request"]
    assert (await _get(world, "owner", f"{base}/{ids['foreign']}")).status_code == 404
    assert (await _get(world, "other_owner", detail)).status_code == 404


async def test_run_numbers_follow_blocks_and_citations(world: World) -> None:
    ids = await _seed(world)
    url = f"/api/v1/runs/{ids['mine']}/calls"
    await world.s.execute(
        sa.text(
            "UPDATE blocks SET status = 'accepted' WHERE topic_id IN (SELECT t.id FROM topics t"
            " JOIN modules m ON m.id = t.module_id WHERE m.course_id = :c)"
        ),
        {"c": str(world.w["course_id"])},
    )
    await world.s.commit()
    before = (await _get(world, "owner", url)).json()["numbers"]
    assert before["blocks_held"] == 0
    assert before["blocks_accepted"] > 0

    current = (
        await world.s.execute(
            sa.text(
                "SELECT b.id, b.current_version_id FROM blocks b JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id WHERE m.course_id = :c"
                " AND b.current_version_id IS NOT NULL ORDER BY b.plan_node_id"
            ),
            {"c": str(world.w["course_id"])},
        )
    ).all()
    await world.s.execute(
        sa.text("UPDATE blocks SET status = 'held', hold_reason = 'probe' WHERE id = :b"),
        {"b": str(current[0][0])},
    )
    passage = (
        await world.s.execute(sa.text("SELECT passage_id FROM citations LIMIT 1"))
    ).scalar_one()
    for quote, verified in (("a verbatim quote", "code"), ("", "plan")):
        await world.s.execute(
            sa.text(
                "INSERT INTO citations (block_version_id, passage_id, claim_path, quote,"
                " verified_by) VALUES (:v, :p, 'x', :q, :by)"
            ),
            {"v": str(current[1][1]), "p": str(passage), "q": quote, "by": verified},
        )
    await world.s.commit()

    after = (await _get(world, "owner", url)).json()["numbers"]
    assert after["blocks_held"] == 1
    assert after["blocks_accepted"] == before["blocks_accepted"] - 1
    assert after["quotes_verbatim"] == before["quotes_verbatim"] + 1
    assert after["quotes_total"] == before["quotes_total"] + 1


async def test_every_recorded_call_prints_one_metadata_line(
    world: World, capsys: Any, monkeypatch: Any
) -> None:
    import json

    from app.logging import configure_logging

    monkeypatch.setattr("structlog._config._CONFIG.is_configured", False, raising=False)
    configure_logging("INFO")
    ids = await _seed(world)
    lines = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("{") and '"model_call"' in line
    ]
    mine = [line for line in lines if line["run_id"] == str(ids["mine"])]
    assert len(mine) == 2
    real = next(line for line in mine if line["agent"] == "architect")
    assert real["model"] == "muse-spark-1.3"
    assert real["step_key"] == "architect"
    assert real["endpoint"] == f"POST {get_settings().model_api_base_url}/responses"
    assert (real["tokens_input"], real["tokens_output"], real["cost_usd"]) == (
        1000,
        2000,
        "0.009750",
    )
    out = json.dumps(lines)
    for private in ("plan the course", "you are the architect", "LEARNER_QUESTION_TEXT"):
        assert private not in out
