"""Shared builder for the T05 EU AI Act demo course.

Both the live test (tests/test_t05_live.py, real Meta models) and the
mock twin (tests/test_t05_demo.py, guarded mock transport) build the
same course from tests/fixtures/eurlex_excerpt.md: 1 module x 2 topics,
blocks on Spark, the reader on Wonder, code-planted spot-the-error,
and resume after an injected fault with zero new paid calls.
"""

from __future__ import annotations

import json
import pathlib
import uuid
from typing import Any
from uuid import UUID

import httpx2
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

EXCERPT_PATH = "tests/fixtures/eurlex_excerpt.md"
EXCERPT = pathlib.Path(EXCERPT_PATH).read_text(encoding="utf-8")

COURSE_TITLE = "AI literacy at work: the EU AI Act"
LICENCE_NOTE = "Commission Decision 2011/833/EU"


def demo_tree() -> dict[str, Any]:
    """One module with two lesson topics: duties, then prohibitions."""
    return {
        "modules": [
            {
                "plan_node_id": "m1",
                "title": "Duties and risk tiers",
                "topics": [
                    {
                        "plan_node_id": "t1",
                        "kind": "lesson",
                        "title": "Who must ensure AI literacy",
                        "blocks": [
                            {"plan_node_id": "b1", "role": "teach", "block_type": "reading"},
                            {"plan_node_id": "b2", "role": "check", "block_type": "choice"},
                        ],
                    },
                    {
                        "plan_node_id": "t2",
                        "kind": "lesson",
                        "title": "Prohibitions and fines",
                        "blocks": [
                            {"plan_node_id": "b3", "role": "teach", "block_type": "reading"},
                            {
                                "plan_node_id": "b4",
                                "role": "practice",
                                "block_type": "spot_the_error",
                            },
                        ],
                    },
                ],
            }
        ]
    }


async def setup_demo(session: AsyncSession, *, settings: Any, email: str) -> dict[str, Any]:
    """Create the demo course, source, and approved addressed passages."""
    from app.services import courses as course_service
    from app.services import library

    user_id = UUID(
        str(
            (
                await session.execute(
                    sa.text(
                        "INSERT INTO users (email, display_name) VALUES (:e, 'N') RETURNING id"
                    ),
                    {"e": email},
                )
            ).one()[0]
        )
    )
    course_id = await course_service.create_course(
        session, owner_id=user_id, title=COURSE_TITLE, language="en"
    )
    await course_service.set_profile(
        session,
        course_id=course_id,
        methodology_codes=["merrill_first_principles"],
        bloom_level="apply",
        audience="non-lawyer municipal staff",
    )
    source_id = await library.store_upload(
        session,
        settings=settings,
        created_by=user_id,
        filename="eurlex_excerpt.md",
        media="text",
        content=EXCERPT.encode("utf-8"),
        licence="EUR-Lex reuse with acknowledgement (Decision 2011/833/EU)",
        course_id=course_id,
    )
    await library.ingest_passages(
        session,
        source_id=source_id,
        passages=library.chunk_markdown(EXCERPT),
        ts_config="english",
    )
    await library.approve_source(session, source_id=source_id, approved_by=user_id)
    await session.commit()
    rows = (
        await session.execute(
            sa.text(
                "SELECT id, source_id, address_key, text FROM passages"
                " WHERE source_id = :source ORDER BY address_key"
            ),
            {"source": str(source_id)},
        )
    ).all()
    passages = [
        {"id": str(row[0]), "source_id": str(row[1]), "address": str(row[2]), "text": str(row[3])}
        for row in rows
    ]
    return {"user_id": user_id, "course_id": course_id, "passages": passages}


def demo_sources(passages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Whole approved source for prompts: passage ids included for citations."""
    return [
        {
            "source_id": item["source_id"],
            "address": item["address"],
            "text": item["text"],
            "passage_id": item["id"],
        }
        for item in passages
    ]


def demo_dossier() -> dict[str, Any]:
    """Frozen dossier: thesis, per-language glossary, sibling handoffs."""
    from app.helpers.language import course_glossary

    return {
        "thesis": "Every claim about the AI Act at work traces to its article.",
        "glossary": course_glossary("en", {"deployer": "deployer", "literacy": "literacy"}),
        "handoffs": [
            {"topic_plan_node_id": "t1", "note": "Case: a municipal team adopts AI tools."},
            {"topic_plan_node_id": "t2", "note": "Same team sorts its tools into risk tiers."},
        ],
    }


def plant_spot_error(passages: list[dict[str, Any]]) -> dict[str, Any]:
    """Code-planted exercise: exactly one mutated sentence from a real passage."""
    from app.course.errorspot import plant_error

    prohibition = next(item for item in passages if "prohibited" in item["text"])
    return plant_error(
        verified=[{"sentence": prohibition["text"], "passage": prohibition["id"]}],
        reader_holds=[],
        seed=3,
        kind="negation",
    )


def demo_dag(
    *,
    course_id: UUID,
    tree: dict[str, Any],
    sources: list[dict[str, Any]],
    dossier: dict[str, Any],
    planted: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compiler DAG minus research/plan: sources are already approved."""
    from app.pipelines.merrill import compile_generation_dag

    blocks_by_id = {
        block["plan_node_id"]: block
        for module in tree["modules"]
        for topic in module["topics"]
        for block in topic["blocks"]
    }
    blocks_by_topic = {
        topic["plan_node_id"]: topic["blocks"]
        for module in tree["modules"]
        for topic in module["topics"]
    }
    raw = compile_generation_dag(
        profile={"methodologies": ["merrill_first_principles"], "bloom": "apply"},
        tree=tree,
        course_id=str(course_id),
    )
    dag: list[dict[str, Any]] = []
    for entry in raw:
        handler = str(entry["handler"])
        if handler in ("generation_research", "generation_plan"):
            continue
        enriched = dict(entry)
        enriched["depends_on"] = [
            dep for dep in entry.get("depends_on", []) if dep not in ("plan:plan", "research:plan")
        ]
        enriched["course_id"] = str(course_id)
        enriched["course_language"] = "en"
        enriched["plan"] = tree
        enriched["dossier"] = dossier
        if handler == "generate_block":
            node_id = str(entry["step_key"]).partition(":")[2]
            skeleton = dict(blocks_by_id[node_id])
            skeleton["brief"] = f"Teach {node_id} from the cited articles"
            if skeleton["block_type"] == "spot_the_error":
                skeleton["candidates"] = planted["sentences"]
                skeleton["kind"] = planted["kind"]
            enriched["block_type"] = skeleton["block_type"]
            enriched["sources"] = sources
            enriched["skeleton"] = skeleton
        elif handler == "read_topic":
            topic_id = str(entry["step_key"]).partition(":")[2]
            enriched["sources"] = sources
            enriched["blocks"] = [
                {"plan_node_id": block["plan_node_id"]} for block in blocks_by_topic[topic_id]
            ]
        elif handler == "saboteur":
            enriched["sources"] = sources
        elif handler == "read_continuity":
            enriched["handoffs"] = dossier["handoffs"]
        elif handler == "rework_blocks":
            enriched["named"] = []
        dag.append(enriched)
    return dag


def generation_handlers() -> dict[str, Any]:
    """Executor handlers for the demo DAG (generate_json stays in the executor)."""
    from app.runs import executor

    return {
        "generate_block": executor.generate_block_step,
        "read_topic": executor.read_topic_step,
        "read_continuity": executor.read_continuity_step,
        "rework_blocks": executor.rework_blocks_step,
        "saboteur": executor.saboteur_step,
    }


async def paid_by_step(session: AsyncSession, *, run_id: UUID) -> dict[str, dict[str, Any]]:
    """Paid call counts and spend per step key for one run."""
    rows = (
        await session.execute(
            sa.text(
                "SELECT s.step_key, count(c.id), coalesce(sum(c.cost_usd), 0)"
                " FROM run_steps s LEFT JOIN llm_calls c ON c.step_id = s.id"
                " AND c.cost_usd > 0 WHERE s.run_id = :run GROUP BY s.step_key"
            ),
            {"run": str(run_id)},
        )
    ).all()
    return {str(key): {"paid": int(count), "cost": total} for key, count, total in rows}


async def block_outputs(session: AsyncSession, *, run_id: UUID) -> dict[str, dict[str, Any]]:
    """Stored generator outputs keyed by block plan node id."""
    rows = (
        await session.execute(
            sa.text("SELECT step_key, output FROM run_steps WHERE run_id = :run"),
            {"run": str(run_id)},
        )
    ).all()
    outputs: dict[str, dict[str, Any]] = {}
    for step_key, output in rows:
        agent, _, node_id = str(step_key).partition(":")
        if agent.startswith("block_") and isinstance(output, dict):
            outputs[node_id] = output
    return outputs


async def model_by_agent(session: AsyncSession, *, run_id: UUID) -> dict[str, str]:
    """Model id used per agent in one run."""
    rows = (
        await session.execute(
            sa.text("SELECT DISTINCT agent, model FROM llm_calls WHERE run_id = :run"),
            {"run": str(run_id)},
        )
    ).all()
    return {str(agent): str(model) for agent, model in rows}


def _section(prompt: str, name: str) -> dict[str, Any]:
    """Parse one code-assembled prompt section back into JSON (mock only)."""
    marker = f"## {name}\n"
    start = prompt.index(marker) + len(marker)
    rest = prompt[start:]
    end = rest.find("\n\n## ")
    return json.loads(rest[:end] if end != -1 else rest)


def mock_demo_handler() -> Any:
    """Guarded-mock transport: verbatim quotes from the prompt's own sources."""
    from app.agents.reader import RUBRIC_ITEMS

    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content.decode("utf-8"))
        prompt = str(body.get("input", ""))
        seen.append(prompt[:20])
        if prompt.startswith("# Saboteur"):
            task = json.loads(prompt.split("## Task input (typed, assembled by code)\n", 1)[1])
            reply = {
                "sentence": task["sentence"],
                "kind": task["kind"],
                "mutated": str(task["sentence"]).replace("shall", "shall not", 1),
            }
            if reply["mutated"] == reply["sentence"]:
                reply["mutated"] = "It is not the case that " + reply["sentence"]
        elif prompt.startswith("# Reader"):
            blocks = _section(prompt, "Block skeleton")["blocks"]
            reply = {
                "verdicts": [
                    {
                        "plan_node_id": block["plan_node_id"],
                        "verdict": "pass",
                        "items": {
                            item: {"verdict": "pass", "evidence": f"quoted {item}"}
                            for item in RUBRIC_ITEMS
                        },
                    }
                    for block in blocks
                ]
            }
        elif prompt.startswith("# Continuity"):
            reply = {"handoffs": [], "issues": []}
        else:
            first = _section(prompt, "Approved sources")["sources"][0]
            quote = str(first["text"])[:40]
            reply = {
                "block_type": "reading",
                "text": "Staff apply the cited passage at work.",
                "citations": [{"passage_id": str(first.get("handle", "P1")), "quote": quote}],
                "answer": "key",
                "options": [{"text": "Yes", "correct": True}, {"text": "No", "correct": False}],
                "pairs": [{"left": "a", "right": "b"}],
                "blanks": ["x"],
                "items": [{"text": "i", "passage_id": "P1", "tier": "high"}],
                "sentences": ["Alpha shall apply.", "Beta shall apply."],
            }
        return httpx2.Response(200, json=_completed(json.dumps(reply)))

    handler.seen = seen  # type: ignore[attr-defined]
    return handler


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


def fresh_email(prefix: str) -> str:
    """Unique synthetic address for demo users (never a real mailbox)."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}@example.com"
