"""T05c product path on the mock provider: plan, then generate, from the DB.

Every test drives the PRODUCT path: submit_plan -> run_plan_operation
(researcher + architect through the compiled plan DAG) -> submit_generate
-> run_generate_operation (the compiled generation DAG with the same
handlers the worker uses) on the real test database; only the HTTP
transport is a mock. Each mutant in build/tasks/T05c.md has the test
named after it below. Caller of every helper: these tests.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any
from uuid import UUID

import demo_t05
import httpx2
import pytest
import sqlalchemy as sa
from conftest import test_database_url
from sqlalchemy.ext.asyncio import AsyncSession
from transport_guard import guarded_client

from app.agents.reader import RUBRIC_ITEMS
from app.config import Settings
from app.db import create_session_factory
from app.providers.llm import MetaProvider
from app.services import planruns

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
NORWEGIAN = "Dette er en tekst som er skrevet på norsk og den skal ikke være med i kurset."


def _plan_reply(spot_node: str = "b13") -> dict[str, Any]:
    """Merrill plan covering all seven block types (13 blocks)."""

    def block(node: str, role: str, btype: str, brief: str) -> dict[str, Any]:
        return {
            "plan_node_id": node,
            "role": role,
            "block_type": btype,
            "brief": brief,
            "source_refs": [{"handle": "P1"}],
        }

    def topic(node: str, kind: str, title: str, blocks: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "plan_node_id": node,
            "kind": kind,
            "title": title,
            "objectives": ["name the duty"],
            "through_line_note": "The municipal case continues.",
            "blocks": blocks,
        }

    return {
        "modules": [
            {
                "plan_node_id": "m1",
                "title": "Duties",
                "purpose": "Teach the duties",
                "entry_knowledge": "none",
                "exit_knowledge": "duties",
                "topics": [
                    topic(
                        "t1",
                        "lesson",
                        "Who must act",
                        [
                            block("b1", "teach", "reading", "The literacy duty"),
                            block("b2", "example", "figure", "Fine ceilings"),
                            block("b3", "check", "choice", "Who must ensure literacy?"),
                            block("b4", "practice", "cloze", "Complete the duty?"),
                            block("b5", "practice", "matching", "Match roles to duties?"),
                        ],
                    ),
                    topic(
                        "t2",
                        "lesson",
                        "Tiers",
                        [
                            block("b6", "teach", "reading", "Risk tiers"),
                            block("b7", "example", "reading", "A tier example"),
                            block("b8", "check", "choice", "Which tier applies?"),
                            block("b9", "practice", "scenario_practice", "Sort the uses?"),
                            block("b10", "practice", "cloze", "Complete the rule?"),
                        ],
                    ),
                    topic(
                        "t3",
                        "module_test",
                        "Module test",
                        [
                            block("b11", "test", "choice", "Which duty applies?"),
                            block("b12", "test", "matching", "Match the duties?"),
                            block(spot_node, "test", "spot_the_error", "Find the error?"),
                        ],
                    ),
                ],
            }
        ],
        "estimate_inputs": {"blocks": 13, "calls": 30, "tokens": 40000},
        "through_line": {
            "case": "A municipal team adopts AI tools.",
            "handoffs": [
                {"topic_plan_node_id": "t1", "note": "The team meets the duty."},
                {"topic_plan_node_id": "t2", "note": "The team sorts its tools."},
                {"topic_plan_node_id": "t3", "note": "The team is tested."},
            ],
        },
    }


class Rig:
    """Mock transport with per-agent behaviour switches and a post log."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, str]] = []
        self.reader_holds: dict[str, int] = {}
        self.reader_malformed = False
        self.reader_skip: set[str] = set()
        self.spot_node = "b13"
        self.architect_reply: dict[str, Any] | None = None
        self.generator_hook: Callable[[str, str, dict[str, Any]], dict[str, Any]] | None = None

    def count(self, head: str) -> int:
        return sum(1 for h, _ in self.posts if h == head)

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content.decode("utf-8"))
        prompt = str(body.get("input", ""))
        head = prompt.split("\n", 1)[0].removeprefix("# ").split()[0].lower()
        self.posts.append((head, prompt))
        if head == "researcher":
            dynamic = json.loads(prompt.split("## Task input (typed, assembled by code)\n")[1])
            ranked = [
                {"handle": item["handle"], "rank": index, "score": 0.9 - index * 0.1}
                for index, item in enumerate(dynamic["passages"][:3], 1)
            ]
            reply: dict[str, Any] = {
                "query": dynamic["query"],
                "rephrased": dynamic["rephrased"],
                "ranked": ranked,
                "web_candidates": [],
            }
        elif head == "architect":
            reply = (
                self.architect_reply
                if self.architect_reply is not None
                else _plan_reply(self.spot_node)
            )
        elif head == "saboteur":
            task = json.loads(prompt.split("## Task input (typed, assembled by code)\n")[1])
            mutated = str(task["sentence"]).replace("shall", "shall not", 1)
            if mutated == task["sentence"]:
                mutated = "It is not the case that " + str(task["sentence"])
            reply = {"sentence": task["sentence"], "kind": task["kind"], "mutated": mutated}
        elif head == "reader":
            reply = self._reader(prompt)
        elif head == "continuity":
            reply = {"handoffs": [], "issues": []}
        else:
            reply = self._generator(prompt)
        return httpx2.Response(200, json=demo_t05._completed(json.dumps(reply)))  # noqa: SLF001

    def _reader(self, prompt: str) -> dict[str, Any]:
        if self.reader_malformed:
            return {"verdicts": [{"plan_node_id": "b1", "verdict": "pass", "items": {}}]}
        blocks = demo_t05._section(prompt, "Block skeleton")["blocks"]  # noqa: SLF001
        verdicts = []
        for block in blocks:
            node = str(block["plan_node_id"])
            if node in self.reader_skip:
                continue
            hold = self.reader_holds.get(node, 0) > 0
            if hold:
                self.reader_holds[node] -= 1
            verdicts.append(
                {
                    "plan_node_id": node,
                    "verdict": "hold" if hold else "pass",
                    "items": {
                        item: {
                            "verdict": "hold" if hold and item == "answer_key" else "pass",
                            "evidence": f"quoted {item} of {node}",
                        }
                        for item in RUBRIC_ITEMS
                    },
                }
            )
        return {"verdicts": verdicts}

    def _generator(self, prompt: str) -> dict[str, Any]:
        skeleton = demo_t05._section(prompt, "Block skeleton")  # noqa: SLF001
        node = str(skeleton["plan_node_id"])
        btype = str(skeleton.get("block_type", "reading"))
        first = demo_t05._section(prompt, "Approved sources")["sources"][0]  # noqa: SLF001
        reply: dict[str, Any] = {
            "block_type": btype,
            "text": f"Text of {node}: staff apply the cited rule at work.",
            "citations": [{"passage_id": "P1", "quote": str(first["text"])[:40]}],
            "answer": "key",
            "options": [
                {"text": f"Option A of {node}", "correct": True},
                {"text": f"Option B of {node}", "correct": False},
            ],
            "pairs": [
                {"left": f"Left one of {node}", "right": f"Right one of {node}"},
                {"left": f"Left two of {node}", "right": f"Right two of {node}"},
            ],
            "numbers": [15, 35],
            "blanks": ["apply"],
            "items": [
                {"text": f"Use one of {node}", "passage_id": "P1", "tier": "high"},
                {"text": f"Use two of {node}", "passage_id": "P1", "tier": "minimal"},
            ],
            "sentences": ["Alpha shall apply.", "Beta shall apply."],
        }
        if btype == "cloze":
            reply["text"] = f"Staff must ___ the rule of {node} at work."
        if self.generator_hook is not None:
            reply = self.generator_hook(node, prompt, reply)
        return reply


class Env:
    """One test environment: settings, factory, rig, and the actor."""

    def __init__(self, rig: Rig) -> None:
        self.rig = rig
        self.settings = Settings(
            _env_file=None,
            model_api_key="synthetic-test-key",
            model_api_base_url="https://api.meta.ai/v1",
            llm_spend_ceiling_usd=50.0,
            source_store_path="/tmp/muse-t05c-product",
        )
        self.factory = create_session_factory(test_database_url())
        http = guarded_client(self.settings, inner=httpx2.MockTransport(rig.handler))

        def make(session: AsyncSession) -> MetaProvider:
            return MetaProvider(session, self.settings, http, self.factory)

        self.provider_factory = make

    def actor(self, user_id: UUID) -> Any:
        from app.rbac.principal import Principal

        return Principal(
            user_id=user_id,
            email="product@example.com",
            roles=("author",),
            permissions=frozenset({"course.plan", "course.generate", "run.retry"}),
            scope_tags=(),
            is_superadmin=False,
        )

    async def plan(self, session: AsyncSession, demo: dict[str, Any]) -> UUID:
        op = await planruns.submit_plan(
            session,
            actor=self.actor(demo["user_id"]),
            course_id=demo["course_id"],
            queries=["Who must ensure AI literacy?", "Which AI practices are prohibited?"],
        )
        out = await planruns.run_plan_operation(
            session, op, provider_factory=self.provider_factory, session_factory=self.factory
        )
        return UUID(out["run_id"])

    async def generate(self, session: AsyncSession, demo: dict[str, Any]) -> UUID:
        op = await planruns.submit_generate(
            session, actor=self.actor(demo["user_id"]), course_id=demo["course_id"]
        )
        out = await planruns.run_generate_operation(
            session, op, provider_factory=self.provider_factory, session_factory=self.factory
        )
        return UUID(out["run_id"])

    async def rewrite(self, session: AsyncSession, demo: dict[str, Any], block_id: str) -> UUID:
        op = (
            await session.execute(
                sa.text(
                    "INSERT INTO operations (kind, status, params, progress, actor_id)"
                    " VALUES ('rewrite_block', 'queued', CAST(:p AS jsonb), 0, :a) RETURNING id"
                ),
                {
                    "p": json.dumps({"course_id": str(demo["course_id"]), "block_id": block_id}),
                    "a": str(demo["user_id"]),
                },
            )
        ).one()[0]
        await session.commit()
        out = await planruns.run_generate_operation(
            session,
            UUID(str(op)),
            provider_factory=self.provider_factory,
            session_factory=self.factory,
        )
        return UUID(out["run_id"])


async def _new_course(session: AsyncSession, env: Env, prefix: str) -> dict[str, Any]:
    return await demo_t05.setup_demo(
        session, settings=env.settings, email=demo_t05.fresh_email(prefix)
    )


async def _status(session: AsyncSession, run_id: UUID) -> str:
    return str(
        (
            await session.execute(
                sa.text("SELECT status FROM runs WHERE id = :r"), {"r": str(run_id)}
            )
        ).scalar_one()
    )


async def _blocks(session: AsyncSession, course_id: UUID) -> dict[str, dict[str, Any]]:
    rows = (
        await session.execute(
            sa.text(
                "SELECT b.id, b.plan_node_id, b.block_type, b.status, b.hold_reason,"
                " b.current_version_id, bv.content, bv.answer_key,"
                " (SELECT count(*) FROM block_versions x WHERE x.block_id = b.id)"
                " FROM blocks b JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id"
                " LEFT JOIN block_versions bv ON bv.id = b.current_version_id"
                " WHERE m.course_id = :c"
            ),
            {"c": str(course_id)},
        )
    ).all()
    return {
        str(r[1]): {
            "id": str(r[0]),
            "type": str(r[2]),
            "status": str(r[3]),
            "hold": r[4],
            "version": str(r[5]),
            "content": dict(r[6] or {}),
            "key": dict(r[7] or {}),
            "versions": int(r[8]),
        }
        for r in rows
    }


async def _snapshot(session: AsyncSession, course_id: UUID) -> dict[str, Any]:
    blocks = await _blocks(session, course_id)
    counts = {}
    for name, join in (
        ("citations", "citations x JOIN block_versions v ON v.id = x.block_version_id"),
        ("versions", "block_versions v"),
    ):
        counts[name] = (
            await session.execute(
                sa.text(
                    f"SELECT count(*) FROM {join} JOIN blocks b ON b.id = v.block_id"
                    " JOIN topics t ON t.id = b.topic_id JOIN modules m ON m.id = t.module_id"
                    " WHERE m.course_id = :c"
                ),
                {"c": str(course_id)},
            )
        ).scalar_one()
    course = (
        await session.execute(
            sa.text("SELECT status, row_version, updated_at FROM courses WHERE id = :c"),
            {"c": str(course_id)},
        )
    ).one()
    return {
        "blocks": {
            k: (v["status"], v["hold"], v["version"], v["versions"]) for k, v in blocks.items()
        },
        "course": tuple(str(x) for x in course),
        **{k: int(v) for k, v in counts.items()},
    }


async def _built(session: AsyncSession, env: Env, prefix: str = "prod") -> dict[str, Any]:
    """A course planned and generated through the product path."""
    demo = await _new_course(session, env, prefix)
    assert await _status(session, await env.plan(session, demo)) == "done"
    demo["gen_run"] = await env.generate(session, demo)
    return demo


async def _request_inputs(
    session: AsyncSession, run_id: UUID, agent: str | None = None
) -> list[str]:
    sql = "SELECT request FROM llm_calls WHERE run_id = :r"
    if agent:
        sql += " AND agent = :a"
    rows = (await session.execute(sa.text(sql), {"r": str(run_id), "a": agent})).all()
    return [str((r[0] or {}).get("input", "")) for r in rows]


async def test_acceptance_on_the_product_path_mock(t03_session: AsyncSession) -> None:
    """Items 1-4, 7-8 and the mutants that live in this run's evidence."""
    rig = Rig()
    rig.reader_holds = {"b3": 1}
    env = Env(rig)
    session = t03_session
    demo_c = await _new_course(session, env, "c")
    await demo_t05.write_demo_tree(session, course_id=demo_c["course_id"])
    demo_a = await _new_course(session, env, "a")
    await demo_t05.write_demo_tree(session, course_id=demo_a["course_id"])
    before_a = await _snapshot(session, demo_a["course_id"])
    before_c = await _snapshot(session, demo_c["course_id"])

    demo = await _new_course(session, env, "b")
    plan_run = await env.plan(session, demo)
    assert await _status(session, plan_run) == "done"
    gen_run = await env.generate(session, demo)
    assert await _status(session, gen_run) == "done"

    assert await _snapshot(session, demo_a["course_id"]) == before_a, "A untouched"
    assert await _snapshot(session, demo_c["course_id"]) == before_c, "C untouched"

    blocks = await _blocks(session, demo["course_id"])
    assert sorted(blocks) == sorted(f"b{i}" for i in range(1, 14))
    assert {b["type"] for b in blocks.values()} == {
        "reading", "figure", "choice", "matching", "cloze", "scenario_practice", "spot_the_error",
    }  # fmt: skip
    from importlib import import_module

    from app.blocks.registry import learner_serializers

    render = learner_serializers()["card"]
    for node, block in blocks.items():
        assert block["status"] == "accepted", (node, block["status"], block["hold"])
        validate = import_module(f"app.blocks.{block['type']}.schema").validate
        assert validate(block["content"]) == [], (node, block["content"])
        rendered = render(block["type"], block["content"], block["key"])
        assert any(rendered.values()), (node, "renders empty")
        assert not UUID_RE.search(json.dumps(rendered)), (node, rendered)
        assert not UUID_RE.search(json.dumps(block["content"])), (node, "UUID in content")
    # per-type persistence: content from the type, key in the key column only
    assert blocks["b3"]["content"]["options"] == ["Option A of b3", "Option B of b3"]
    assert blocks["b3"]["key"]["correct"] == [0]
    assert "correct" not in json.dumps(blocks["b3"]["content"])  # mutant: key merged into content
    assert blocks["b5"]["content"]["pairs"][0]["left"] == "Left one of b5"
    assert blocks["b5"]["key"]["mapping_by_index"] == {"0": 0, "1": 1}
    assert blocks["b4"]["content"]["blanks"] == ["___"] and blocks["b4"]["key"]["blanks"] == [
        "apply"
    ]
    assert blocks["b2"]["content"]["numbers"] == [15, 35]
    assert blocks["b9"]["content"]["items"][0]["text"] == "Use one of b9"
    assert blocks["b9"]["key"]["labels_by_index"] == {"0": "high", "1": "minimal"}
    assert blocks["b1"]["content"]["body"].startswith("Text of b1")

    # rework ran: b3 was held by the reader, rewritten, read again, accepted (mutant: rework no-op)
    assert blocks["b3"]["versions"] >= 2, "rework must re-version the held block"
    t1_readers = [
        p
        for h, p in rig.posts
        if h == "reader" and demo_t05._section(p, "Block skeleton")["topic_plan_node_id"] == "t1"  # noqa: SLF001
    ]
    assert len(t1_readers) == 2, "reworked block is read again"
    assert "reread_after_rework" in t1_readers[1]

    # reader requests carry the FULL learner content + key (mutants: no text, no sources)
    reader_inputs = await _request_inputs(session, gen_run, "reader")
    joined = "\n".join(reader_inputs)
    for node, block in blocks.items():
        probe = {
            "reading": f"Text of {node}",
            "figure": '"numbers"',
            "choice": f"Option B of {node}",
            "matching": f"Left two of {node}",
            "cloze": f"Staff must ___ the rule of {node}",
            "scenario_practice": f"Use two of {node}",
            "spot_the_error": str(block["content"].get("sentences", ["?"])[0]),
        }[block["type"]]
        assert probe in joined, (node, block["type"], probe)
    assert '"correct": [' in joined and '"planted_index"' in joined, "keys reach the reader"
    source_text = str(demo["passages"][0]["text"])[:40]
    assert all(source_text in text for text in reader_inputs), "reader prompt carries the sources"

    # wave B generators read wave A text (mutant: wave B gets no wave A text)
    for agent, wave_a in (("block_check", "Text of b1"), ("block_practice", "Text of b2")):
        inputs = await _request_inputs(session, gen_run, agent)
        assert inputs and any(wave_a in text for text in inputs), agent

    # b13 has seed % 4 == 3: the production picker seeds it fully correct
    key, content = blocks["b13"]["key"], blocks["b13"]["content"]
    assert key["fully_correct"] is True, "fully_correct is seeded in production"
    assert key["planted_index"] == -1 and key["planted_sentence"] == ""
    assert rig.count("saboteur") == 0, "a fully-correct exercise pays no saboteur call"
    assert len(content["sentences"]) >= 2
    assert "planted_index" not in json.dumps(content)

    # no UUID in any prompt of either run (item 7)
    for run in (plan_run, gen_run):
        for text in await _request_inputs(session, run):
            assert not UUID_RE.search(text), text[:200]

    # SECOND identical submit_generate: 0 HTTP posts, 0 paid rows (item 2)
    posts_before = len(rig.posts)
    run2 = await env.generate(session, demo)
    assert await _status(session, run2) == "done"
    assert len(rig.posts) == posts_before, [
        (h, re.findall(r'"plan_node_id": "(b\d+)"', p)[-1:]) for h, p in rig.posts[posts_before:]
    ]
    paid2 = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM llm_calls WHERE run_id = :r"
                " AND (cost_usd > 0 OR tokens_input > 0)"
            ),
            {"r": str(run2)},
        )
    ).scalar_one()
    assert paid2 == 0


async def test_mutant_reader_reply_unchecked(t03_session: AsyncSession) -> None:
    """A reader reply missing rubric items is rejected, never stored, nothing accepted."""
    rig = Rig()
    rig.reader_malformed = True
    env = Env(rig)
    demo = await _new_course(t03_session, env, "m-reader")
    await env.plan(t03_session, demo)
    run = await env.generate(t03_session, demo)
    assert await _status(t03_session, run) == "stopped"
    blocks = await _blocks(t03_session, demo["course_id"])
    assert all(b["status"] != "accepted" for b in blocks.values())
    stored = (
        await t03_session.execute(
            sa.text("SELECT count(*) FROM llm_replies WHERE response ? 'verdicts'")
        )
    ).scalar_one()
    assert stored == 0, "a rejected reader reply is never stored"


async def test_mutant_reader_hold_changes_nothing(t03_session: AsyncSession) -> None:
    """A reader that holds b3 on every pass leaves b3 held with the reason."""
    rig = Rig()
    rig.reader_holds = {"b3": 99}
    env = Env(rig)
    demo = await _built(t03_session, env, "m-hold")
    assert await _status(t03_session, demo["gen_run"]) == "done"
    blocks = await _blocks(t03_session, demo["course_id"])
    assert blocks["b3"]["status"] == "held"
    assert "answer_key" in str(blocks["b3"]["hold"])
    assert blocks["b1"]["status"] == "accepted"
    assert all(b["status"] != "draft" for b in blocks.values())


async def test_mutant_language_check_removed(t03_session: AsyncSession) -> None:
    """A generator reply in another language holds the block with the reason."""
    rig = Rig()

    def hook(node: str, prompt: str, reply: dict[str, Any]) -> dict[str, Any]:
        if node == "b1":
            reply["text"] = NORWEGIAN
        return reply

    rig.generator_hook = hook
    env = Env(rig)
    demo = await _built(t03_session, env, "m-lang")
    blocks = await _blocks(t03_session, demo["course_id"])
    assert blocks["b1"]["status"] == "held"
    assert "course language is en" in str(blocks["b1"]["hold"])


async def test_malformed_typed_replies_are_held_not_stored(t03_session: AsyncSession) -> None:
    """Item 8: a reply that does not validate its type schema holds the block."""
    rig = Rig()

    def hook(node: str, prompt: str, reply: dict[str, Any]) -> dict[str, Any]:
        if node == "b3":
            reply["options"] = [{"text": "only one", "correct": True}]
        if node == "b4":
            reply["text"] = "No gap in this cloze text."
        if node == "b2":
            reply["numbers"] = []
        return reply

    rig.generator_hook = hook
    env = Env(rig)
    demo = await _built(t03_session, env, "m-bad")
    blocks = await _blocks(t03_session, demo["course_id"])
    for node in ("b2", "b3", "b4"):
        assert blocks[node]["status"] == "held", (node, blocks[node]["status"])
        assert blocks[node]["hold"]
    assert blocks["b1"]["status"] == "accepted"
    held_keys = (
        await t03_session.execute(
            sa.text(
                "SELECT count(*) FROM block_versions bv JOIN blocks b ON b.id = bv.block_id"
                " WHERE b.id = ANY(:ids) AND bv.id = b.current_version_id"
                " AND bv.answer_key IS NOT NULL"
            ),
            {"ids": [blocks[n]["id"] for n in ("b2", "b3", "b4")]},
        )
    ).scalar_one()
    assert held_keys == 0, "a held reply stores no answer key"


async def test_mutant_generator_digest_ignores_prompt(t03_session: AsyncSession) -> None:
    """Resume with a changed prompt re-asks that step; unchanged steps stay free."""
    from app.runs import executor

    rig = Rig()
    env = Env(rig)
    demo = await _built(t03_session, env, "m-digest")
    run = demo["gen_run"]
    dag = (
        await t03_session.execute(
            sa.text("SELECT compiled_dag FROM runs WHERE id = :r"), {"r": str(run)}
        )
    ).scalar_one()
    for entry in dag:
        if entry["step_key"] == "block_teach:b1":
            entry["skeleton"]["brief"] = "A different brief for the first reading"
    await t03_session.execute(
        sa.text(
            "UPDATE runs SET compiled_dag = CAST(:d AS jsonb), status = 'running' WHERE id = :r"
        ),
        {"d": json.dumps(dag), "r": str(run)},
    )
    await t03_session.commit()
    before = len(rig.posts)
    final = await executor.drive_run(
        env.factory,
        run_id=run,
        provider_factory=env.provider_factory,
        handlers=planruns.plan_handlers(),
    )
    assert final["status"] == "done"
    new = [p for h, p in rig.posts[before:] if h.startswith("block")]
    assert any("A different brief for the first reading" in p for p in new), (
        "a changed prompt must reach a paid generator call on resume"
    )
    assert len(new) <= 2, "unchanged steps stay free"


async def test_mutant_generator_variant_zero(t03_session: AsyncSession) -> None:
    """Rewriting an unchanged block pays for a fresh draft (variant > 0), others keep versions."""
    rig = Rig()
    env = Env(rig)
    demo = await _built(t03_session, env, "m-variant")
    blocks = await _blocks(t03_session, demo["course_id"])
    posts_before = len(rig.posts)
    run = await env.rewrite(t03_session, demo, blocks["b1"]["id"])
    assert await _status(t03_session, run) == "done"
    new = rig.posts[posts_before:]
    assert [h for h, _ in new].count("block") + sum(1 for h, _ in new if h.startswith("block")) >= 1
    after = await _blocks(t03_session, demo["course_id"])
    assert after["b1"]["version"] != blocks["b1"]["version"], "named block re-versioned"
    for node, block in blocks.items():
        if node not in ("b1",):
            assert after[node]["version"] == block["version"], f"{node} must keep its version"
    variants = (
        await t03_session.execute(
            sa.text("SELECT variant FROM llm_calls WHERE run_id = :r AND agent LIKE 'block_%'"),
            {"r": str(run)},
        )
    ).all()
    assert variants and all(int(v[0]) > 0 for v in variants), variants


async def test_mutant_generation_handlers_missing_from_plan_handlers(
    t03_session: AsyncSession,
) -> None:
    """The handler map the worker and resume drive contains every generation handler."""
    handlers = planruns.plan_handlers()
    for name in ("generate_block", "read_topic", "read_continuity", "rework_blocks", "saboteur"):
        assert name in handlers, name
    rig = Rig()
    env = Env(rig)
    demo = await _built(t03_session, env, "m-handlers")
    assert await _status(t03_session, demo["gen_run"]) == "done"


async def test_rejected_architect_reply_is_paid_again_not_replayed(
    t03_session: AsyncSession,
) -> None:
    """Item 12b on the product path: reject once, run again, exactly one new paid call."""
    rig = Rig()
    rig.architect_reply = {"modules": []}
    env = Env(rig)
    demo = await _new_course(t03_session, env, "rej")
    run1 = await env.plan(t03_session, demo)
    assert await _status(t03_session, run1) == "stopped"
    architect_1 = rig.count("architect")
    assert architect_1 == 1
    run2 = await env.plan(t03_session, demo)
    assert await _status(t03_session, run2) == "stopped"
    assert rig.count("architect") == architect_1 + 1, (
        "the rejected reply was replayed, not re-asked"
    )
    marker = (
        await t03_session.execute(
            sa.text("SELECT stop_marker FROM runs WHERE id = :r"), {"r": str(run2)}
        )
    ).scalar_one()
    assert "ValidationError" not in marker and "modules" not in marker
    assert str(marker).endswith(".")
    stored = (
        await t03_session.execute(
            sa.text("SELECT count(*) FROM llm_replies WHERE model LIKE '%wonder%'")
        )
    ).scalar_one()
    assert stored == 0
    _ = pytest


async def test_planted_spot_key_is_the_saboteurs_pick(t03_session: AsyncSession) -> None:
    """Item 3: the planted sentence, the key and the passage come from ONE pick."""
    rig = Rig()
    rig.spot_node = "q13"  # seed % 4 == 1: planted, kind negation (model rewrite)
    env = Env(rig)
    demo = await _built(t03_session, env, "planted")
    assert await _status(t03_session, demo["gen_run"]) == "done"
    block = (await _blocks(t03_session, demo["course_id"]))["q13"]
    key, content = block["key"], block["content"]
    assert key["fully_correct"] is False
    assert content["sentences"][key["planted_index"]] == key["planted_sentence"]
    assert key["planted_sentence"] != key["true_sentence"]
    passage = {p["address"]: p["text"] for p in demo["passages"]}
    assert any(key["true_sentence"].rstrip(".") in text for text in passage.values()), key
    saboteur = [p for h, p in rig.posts if h == "saboteur"]
    assert len(saboteur) == 1 and key["true_sentence"] in saboteur[0]
    assert key["true_sentence"] not in content["sentences"], "the true sentence is never shown"
    cited = (
        await t03_session.execute(
            sa.text("SELECT c.quote FROM citations c WHERE c.block_version_id = :v"),
            {"v": block["version"]},
        )
    ).all()
    assert cited and all(str(r[0]) in key["true_sentence"] for r in cited)
    assert block["status"] == "accepted"


async def test_mutant_reader_skips_a_block(t03_session: AsyncSession) -> None:
    """A reader reply with no verdict for a block is rejected: no block ends unread."""
    rig = Rig()
    rig.reader_skip = {"b2"}
    env = Env(rig)
    demo = await _new_course(t03_session, env, "skip")
    await env.plan(t03_session, demo)
    run = await env.generate(t03_session, demo)
    assert await _status(t03_session, run) == "stopped"
    blocks = await _blocks(t03_session, demo["course_id"])
    assert blocks["b2"]["status"] == "draft" and blocks["b1"]["status"] == "draft"


async def test_upstream_texts_follow_dag_order_not_row_order(t03_session: AsyncSession) -> None:
    """Prompt digests must not depend on the order Postgres returns rows in."""
    from app.runs import executor

    rig = Rig()
    env = Env(rig)
    demo = await _new_course(t03_session, env, "order")
    run = await executor.new_run(
        t03_session, course_id=demo["course_id"], kind="generation", dag=[]
    )
    for key in ("block_example:b2", "block_teach:b1"):  # inserted against DAG order
        await t03_session.execute(
            sa.text(
                "INSERT INTO run_steps (run_id, name, status, step_key, depends_on, output)"
                " VALUES (:r, :k, 'done', :k2, '[]', CAST(:o AS jsonb))"
            ),
            {"r": str(run), "k": key, "k2": key, "o": json.dumps({"text": key})},
        )
    await t03_session.commit()
    got = await executor._upstream_outputs(  # noqa: SLF001
        t03_session,
        run_id=run,
        entry={"depends_on": ["block_teach:b1", "block_example:b2"]},
    )
    assert [u["step_key"] for u in got] == ["block_teach:b1", "block_example:b2"]


async def _built_with_hold(
    session: AsyncSession, prefix: str
) -> tuple[Rig, dict[str, Any], dict[str, dict[str, Any]]]:
    """Course built through the product path; the reader holds b3 once."""
    rig = Rig()
    rig.reader_holds = {"b3": 1}
    demo = await _built(session, Env(rig), prefix)
    return rig, demo, await _blocks(session, demo["course_id"])


async def test_mutant_reader_gets_no_block_text(t03_session: AsyncSession) -> None:
    """Every block's learner content and key reach the reader request."""
    rig, demo, blocks = await _built_with_hold(t03_session, "m-text")
    joined = "\n".join(await _request_inputs(t03_session, demo["gen_run"], "reader"))
    assert "Option B of b3" in joined and "Left two of b5" in joined and "Use two of b9" in joined
    assert "Staff must ___ the rule of b4" in joined and "Text of b1" in joined
    assert '"correct": [' in joined and '"mapping_by_index"' in joined
    assert str(blocks["b13"]["content"]["sentences"][0]) in joined
    _ = rig


async def test_mutant_reader_prompt_without_sources(t03_session: AsyncSession) -> None:
    """The approved source text rides in every reader request, without UUIDs."""
    _rig, demo, _blocks_ = await _built_with_hold(t03_session, "m-src")
    inputs = await _request_inputs(t03_session, demo["gen_run"], "reader")
    first = str(demo["passages"][0]["text"])[:40]
    assert inputs and all(first in text and not UUID_RE.search(text) for text in inputs)


async def test_mutant_wave_b_gets_no_wave_a_text(t03_session: AsyncSession) -> None:
    """Wave B generators receive the wave A text of their own topic."""
    _rig, demo, _blocks_ = await _built_with_hold(t03_session, "m-wave")
    for agent in ("block_check", "block_practice"):
        inputs = await _request_inputs(t03_session, demo["gen_run"], agent)
        assert inputs and any("Text of b1" in t and "Text of b2" in t for t in inputs), agent


async def test_mutant_answer_key_merged_into_choice_content(t03_session: AsyncSession) -> None:
    """The choice key lives in answer_key only; content holds stem and options."""
    _rig, _demo, blocks = await _built_with_hold(t03_session, "m-key")
    for node in ("b3", "b8", "b11"):
        assert set(blocks[node]["content"]) == {"stem", "options", "citations"}, node
        assert blocks[node]["key"]["correct"] == [0]


async def test_mutant_rework_does_nothing(t03_session: AsyncSession) -> None:
    """A reader-held block is regenerated, read again and accepted."""
    rig, _demo, blocks = await _built_with_hold(t03_session, "m-rework")
    assert blocks["b3"]["versions"] >= 2 and blocks["b3"]["status"] == "accepted"
    assert rig.count("reader") == 4  # t1, t2, t3, then t1 again
    assert all(b["status"] != "draft" for b in blocks.values())


async def test_mutant_fully_correct_never_seeded(t03_session: AsyncSession) -> None:
    """The production picker seeds a fully-correct exercise (b13: seed % 4 == 3)."""
    rig, _demo, blocks = await _built_with_hold(t03_session, "m-fc")
    key = blocks["b13"]["key"]
    assert key["fully_correct"] is True and key["planted_index"] == -1
    assert rig.count("saboteur") == 0


async def test_second_run_after_rework_pays_nothing(t03_session: AsyncSession) -> None:
    """Item 2: even after a hold+rework, an identical submit_generate makes 0 HTTP posts."""
    rig = Rig()
    rig.reader_holds = {"b3": 1, "b8": 1}
    env = Env(rig)
    demo = await _built(t03_session, env, "second")
    posts = len(rig.posts)
    run2 = await env.generate(t03_session, demo)
    assert await _status(t03_session, run2) == "done"
    assert len(rig.posts) == posts, [h for h, _ in rig.posts[posts:]]


async def test_stored_reply_string_decodes_like_the_live_path(t03_session: AsyncSession) -> None:
    """Item 12c: a reply the store hands back as JSON text parses with the live decoder."""
    from app.llm.schema import validate_strict_subset

    rig = Rig()
    env = Env(rig)
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }
    validate_strict_subset(schema, name="probe")
    async with env.factory() as session:
        provider = env.provider_factory(session)
        rig.architect_reply = {"ok": True}
        first = await provider.generate_json(
            agent="architect", prompt="# Architect probe", schema=schema
        )
        assert first == {"ok": True} and rig.count("architect") == 1
    async with env.factory() as session:
        await session.execute(sa.text("UPDATE llm_replies SET response = to_jsonb(response::text)"))
        await session.commit()
    async with env.factory() as session:
        provider = env.provider_factory(session)
        again = await provider.generate_json(
            agent="architect", prompt="# Architect probe", schema=schema
        )
    assert again == {"ok": True}
    assert rig.count("architect") == 1, "the stored reply was served, not re-paid"


def _api_routes(app: Any) -> list[Any]:
    """Every APIRoute of the app, unwrapping included routers."""
    from fastapi.routing import APIRoute

    found: list[Any] = []

    def walk(routes: list[Any]) -> None:
        for route in routes:
            inner = getattr(route, "original_router", None)
            if inner is not None:
                walk(list(inner.routes))
            elif isinstance(route, APIRoute):
                found.append(route)

    walk(list(app.routes))
    return found


def test_every_id_route_in_the_app_parses_ids_with_the_uuid_dependency() -> None:
    """Item 13: EVERY route with an *_id path parameter parses it with app.routes.ids."""
    from app.main import create_app
    from app.routes import ids

    checked: list[str] = []
    for route in _api_routes(create_app(sync_on_startup=False)):
        for param in re.findall(r"\{(\w+_id)\}", route.path):
            modules = {dep.call.__module__ for dep in route.dependant.dependencies if dep.call}
            assert ids.__name__ in modules, f"{sorted(route.methods)} {route.path} ({param})"
            checked.append(route.path)
    assert len(checked) >= 20, checked


def test_malformed_ids_answer_404_on_every_id_route() -> None:
    """Item 13: every id route in the OpenAPI spec answers 404, never 500, for a bad id."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(sync_on_startup=False)
    bad: list[tuple[str, str, int]] = []
    seen = 0
    with TestClient(app, raise_server_exceptions=False) as client:
        for path, item in app.openapi()["paths"].items():
            if not re.search(r"\{\w+_id\}", path):
                continue
            url = re.sub(r"\{\w+\}", "not-a-uuid", path)
            for method in item:
                response = client.request(method.upper(), url, json={})
                seen += 1
                if response.status_code != 404:
                    bad.append((method, path, response.status_code))
    assert seen >= 20 and not bad, bad
