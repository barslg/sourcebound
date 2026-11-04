"""Prepare the isolated e2e database: create, migrate, reset, seed users and sessions.

Writes the session tokens to tmp/e2e-sessions.json at the repo root (git-ignored).
Never touches the dev database: the target is always the muse_e2e database.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import psycopg
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

ROOT = Path(__file__).resolve().parents[2]
BASE = os.environ.get("E2E_BASE_URL", "postgresql+asyncpg://muse:muse@localhost:5434/postgres")
DB_NAME = os.environ.get("E2E_DB_NAME", "muse_e2e")
URL = re.sub(r"/[^/?]*(\?.*)?$", f"/{DB_NAME}", BASE)

USERS = {
    "admin": ("admin@example.com", "Ada Admin", True),
    "waiting": ("waiting@example.com", "Walt Waiting", False),
    "student": ("student@example.com", "Sam Student", False),
    "other": ("other@example.com", "Olga Other", False),
    "assignee": ("assignee@example.com", "Anna Assignee", False),
    "banme": ("banme@example.com", "Bo Banme", False),
    "inviteme": ("inviteme@example.com", "Ines Inviteme", False),
    "deleteme": ("deleteme@example.com", "Dag Deleteme", False),
    "author": ("author@example.com", "Aud Author", False),
    "editonly": ("editonly@example.com", "Edd Editonly", False),
    "visitor": ("visitor@example.com", "Vera Visitor", False),
}


def ensure_database() -> None:
    admin_dsn = re.sub(r"/[^/?]*(\?.*)?$", "/postgres", BASE).replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,)).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{DB_NAME}"')


def migrate() -> None:
    env = {**os.environ, "DATABASE_URL": URL}
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True)
    subprocess.run(["uv", "run", "python", "-m", "app.tasks.schema"], cwd=ROOT, env=env, check=True)


def seed_tree(prefix: str) -> Any:
    sys.path.insert(0, str(ROOT / "src"))
    from app.course.tree import CourseTree

    def block(node: str, role: str, brief: str) -> dict[str, object]:
        return {
            "plan_node_id": f"{prefix}-{node}",
            "role": role,
            "block_type": "reading",
            "brief": brief,
            "source_refs": [{"handle": "P1"}],
        }

    def topic(node: str, title: str) -> dict[str, object]:
        return {
            "plan_node_id": f"{prefix}-{node}",
            "kind": "lesson",
            "title": title,
            "objectives": [f"Explain {title.lower()}"],
            "through_line_note": "Builds on the previous topic.",
            "blocks": [
                block(f"{prefix}-{node}-a", "teach", f"{title}: what it is and when it applies"),
                block(f"{prefix}-{node}-c", "example", f"{title} on a ward, one worked case"),
                block(f"{prefix}-{node}-b", "check", f"Why does {title.lower()} matter?"),
            ],
        }

    def module(node: str, title: str, topics: list[dict[str, object]]) -> dict[str, object]:
        return {
            "plan_node_id": f"{prefix}-{node}",
            "title": title,
            "purpose": f"Teach {title}",
            "entry_knowledge": "none",
            "exit_knowledge": title,
            "topics": topics,
        }

    return CourseTree.model_validate(
        {
            "modules": [
                module("m1", "Foundations", [topic("t1", "Basics"), topic("t2", "Terms")]),
                module(
                    "m2",
                    "Practice",
                    [
                        topic(
                            "t3",
                            "Drills for ward staff who move between patients and their surroundings all day",
                        )
                    ],
                ),
            ]
        }
    )


async def seed_planned_course(session: AsyncSession, owner: str, title: str) -> Any:
    from app.services.courses import create_course, set_profile, write_tree

    cid = await create_course(
        session, owner_id=owner, title=title, language="en", scope_tags=["e2e-course"]
    )
    await set_profile(
        session,
        course_id=cid,
        methodology_codes=["direct_instruction"],
        bloom_level="understand",
        audience="new nurses",
        constraints=[
            {
                "kind": "audience",
                "key": "audience_level",
                "required": True,
                "question": "Who is the learner at the start?",
            }
        ],
    )
    await write_tree(session, course_id=cid, tree=seed_tree(str(cid)[:8]), plan_digest="e2e")
    return cid


async def add_source(
    session: AsyncSession, course: Any, title: str, text: str, approval: str, licence: str
) -> str:
    import hashlib

    digest = hashlib.sha256(f"{course}:{title}".encode()).hexdigest()
    sid = (
        await session.execute(
            sa.text(
                "INSERT INTO sources (content_sha256, kind, media, title, licence, approval, as_of)"
                " VALUES (:d, 'upload', 'text', :t, :l, :a, DATE '2026-10-01') RETURNING id"
            ),
            {"d": digest, "t": title, "l": licence, "a": approval},
        )
    ).scalar_one()
    for n, para in enumerate(text.split("\n\n"), start=1):
        await session.execute(
            sa.text(
                "INSERT INTO passages (source_id, text, address, address_key)"
                " VALUES (:s, :t, CAST(:a AS jsonb), :k)"
            ),
            {
                "s": str(sid),
                "t": para,
                "a": json.dumps({"kind": "md", "heading_path": [], "para": n}),
                "k": f"para{n}",
            },
        )
    await session.execute(
        sa.text("INSERT INTO course_sources (course_id, source_id, approval) VALUES (:c, :s, :a)"),
        {"c": str(course), "s": str(sid), "a": approval},
    )
    return str(sid)


async def hold_blocks(
    session: AsyncSession, course: Any, source_id: str, reasons: list[str]
) -> None:
    """Mark the first check block of each topic held, with a real version and a citation to a passage."""
    passage = (
        await session.execute(
            sa.text("SELECT id FROM passages WHERE source_id = :s ORDER BY address_key LIMIT 1"),
            {"s": source_id},
        )
    ).scalar_one()
    rows = (
        await session.execute(
            sa.text(
                "SELECT b.id FROM blocks b JOIN topics t ON t.id = b.topic_id JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :c AND b.role = 'check' ORDER BY m.ordinal, t.ordinal"
            ),
            {"c": str(course)},
        )
    ).all()
    for (block_id,), reason in zip(rows, reasons, strict=False):
        version = (
            await session.execute(
                sa.text(
                    "INSERT INTO block_versions (block_id, version_no, content)"
                    " SELECT :b, COALESCE(MAX(version_no), 0) + 1, CAST(:c AS jsonb) FROM block_versions WHERE block_id = :b RETURNING id"
                ),
                {
                    "b": str(block_id),
                    "c": json.dumps(
                        {
                            "text": "Draft that the checker held.",
                            "body": "Draft that the checker held.",
                        }
                    ),
                },
            )
        ).scalar_one()
        await session.execute(
            sa.text(
                "INSERT INTO citations (block_version_id, passage_id, claim_path, quote) VALUES (:v, :p, 'text', 'quote')"
            ),
            {"v": str(version), "p": str(passage)},
        )
        await session.execute(
            sa.text(
                "UPDATE blocks SET status = 'held', hold_reason = :r, current_version_id = :v WHERE id = :b"
            ),
            {"r": reason, "v": str(version), "b": str(block_id)},
        )


async def give_content(session: AsyncSession, course: Any) -> list[str]:
    """Write one source-language version into every block so the learner view has text."""
    rows = (
        await session.execute(
            sa.text(
                "SELECT b.id, b.plan_node_id FROM blocks b JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id WHERE m.course_id = :c"
                " ORDER BY m.ordinal, t.ordinal, b.ordinal"
            ),
            {"c": str(course)},
        )
    ).all()
    for block_id, node in rows:
        version = (
            await session.execute(
                sa.text(
                    "INSERT INTO block_versions (block_id, version_no, content)"
                    " SELECT :b, COALESCE(MAX(version_no), 0) + 1, CAST(:c AS jsonb)"
                    " FROM block_versions WHERE block_id = :b RETURNING id"
                ),
                {
                    "b": str(block_id),
                    # a learner-valid reading: a body and one cited passage (the learner
                    # services serve only versions that pass the type's schema)
                    "c": json.dumps(
                        {
                            "text": f"Source text of {node}",
                            "body": f"Source text of {node}",
                            "citations": [{"handle": "P1", "quote": f"Source text of {node}"}],
                        }
                    ),
                },
            )
        ).scalar_one()
        await session.execute(
            sa.text("UPDATE blocks SET current_version_id = :v WHERE id = :b"),
            {"v": str(version), "b": str(block_id)},
        )
    return [str(node) for _, node in rows]


async def source_digest(session: AsyncSession, version: Any, node: str) -> str:
    """The digest the learner view compares a translation row against (content and key of the block's current version)."""
    from app.translation.variants import block_digest

    row = (
        await session.execute(
            sa.text(
                "SELECT bv.content, bv.answer_key FROM blocks b JOIN block_versions bv ON bv.id = b.current_version_id"
                " JOIN topics t ON t.id = b.topic_id JOIN modules m ON m.id = t.module_id"
                " JOIN course_versions cv ON cv.course_id = m.course_id"
                " WHERE cv.id = :v AND b.plan_node_id = :n"
            ),
            {"v": str(version), "n": node},
        )
    ).one()
    return block_digest(dict(row[0] or {}), dict(row[1] or {}))


async def add_translation(
    session: AsyncSession,
    version: Any,
    language: str,
    status: str,
    states: list[str],
    nodes: list[str],
) -> None:
    """One course_translations row with a translation_blocks row per given state."""
    translation = (
        await session.execute(
            sa.text(
                "INSERT INTO course_translations (id, course_version_id, language, status)"
                " VALUES (gen_random_uuid(), :v, :l, :s) RETURNING id"
            ),
            {"v": str(version), "l": language, "s": status},
        )
    ).scalar_one()
    for node, state in zip(nodes, states, strict=False):
        await session.execute(
            sa.text(
                "INSERT INTO translation_blocks"
                " (id, translation_id, plan_node_id, block_type, status, content, source_digest)"
                " VALUES (gen_random_uuid(), :t, :n, 'reading', :s, CAST(:c AS jsonb), :d)"
            ),
            {
                "d": await source_digest(session, version, node),
                "t": str(translation),
                "n": node,
                "s": state,
                "c": json.dumps(
                    {"text": f"{language} text of {node}", "body": f"{language} text of {node}"}
                )
                if state == "ok"
                else "{}",
            },
        )


async def seed_translations(session: AsyncSession, owner: str) -> dict[str, str]:
    """Two published courses: nb partial with held blocks plus uk ready; uk failed."""
    from app.services.courses import snapshot_version

    labs: dict[str, str] = {}
    translated = await seed_planned_course(session, owner, "Translated lab")
    nodes = await give_content(session, translated)
    version = await snapshot_version(session, course_id=translated, publish=True)
    await add_translation(
        session, version, "nb", "partial", ["ok", "held", "held", "ok", "ok", "ok"], nodes
    )
    await add_translation(session, version, "uk", "ready", ["ok"] * len(nodes), nodes)
    labs["translated"] = str(translated)
    labs["translated_topic"] = str(
        (
            await session.execute(
                sa.text(
                    "SELECT t.id FROM topics t JOIN modules m ON m.id = t.module_id"
                    " WHERE m.course_id = :c ORDER BY m.ordinal, t.ordinal LIMIT 1"
                ),
                {"c": str(translated)},
            )
        ).scalar_one()
    )
    failed = await seed_planned_course(session, owner, "Translation failed lab")
    nodes = await give_content(session, failed)
    version = await snapshot_version(session, course_id=failed, publish=True)
    await add_translation(session, version, "uk", "failed", ["failed"], nodes)
    labs["translate_failed"] = str(failed)
    labs["no_version"] = str(await seed_planned_course(session, owner, "No version lab"))
    return labs


LEARN_PASSAGES = (
    "Alcohol rub takes twenty seconds when hands are not visibly soiled.",
    "Soap and water takes forty seconds and is used when hands are soiled.",
    "Hands are cleaned before touching a patient and after touching surroundings.",
    "Gloves do not replace hand hygiene.",
    "Alcohol rub does not remove the spores of Clostridioides difficile.",
)


async def seed_learn(session: AsyncSession, owner: str) -> dict[str, str]:
    """A published course with all seven block types, a module test, and an nb translation of two blocks."""
    import uuid

    from app.course.tree import BlockSkeleton, CourseTree, ModuleNode, SourceRef, TopicNode
    from app.services import courses
    from app.translation.variants import block_digest

    cid = await courses.create_course(
        session, owner_id=owner, title="Learner lab", language="en", scope_tags=["learn-lab"]
    )
    await add_source(
        session, cid, "Hygiene protocol", "\n\n".join(LEARN_PASSAGES), "approved", "Internal"
    )
    await session.execute(
        sa.text(
            "UPDATE passages SET address = jsonb_set(address, '{heading_path}', '\"Hand hygiene>Rub or wash\"')"
            " WHERE source_id IN (SELECT source_id FROM course_sources WHERE course_id = :c)"
        ),
        {"c": str(cid)},
    )
    passages = (
        await session.execute(
            sa.text(
                "SELECT p.id, p.text FROM passages p JOIN course_sources cs ON cs.source_id = p.source_id"
                " WHERE cs.course_id = :c ORDER BY p.address_key"
            ),
            {"c": str(cid)},
        )
    ).all()
    chips = [
        {"handle": f"P{n}", "quote": str(text)[:40], "_pid": str(pid)}
        for n, (pid, text) in enumerate(passages[:2], start=1)
    ]
    public = lambda items: [{k: v for k, v in c.items() if not k.startswith("_")} for c in items]  # noqa: E731

    def fx(name: str) -> tuple[dict[str, object], dict[str, object]]:
        # Block fixtures live under tests/block_fixtures (moved out of src by the callers gate).
        from importlib.util import module_from_spec, spec_from_file_location

        path = ROOT / "tests" / "block_fixtures" / f"{name}.py"
        spec = spec_from_file_location(f"block_fixture_{name}", path)
        assert spec is not None and spec.loader is not None, path
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.fixture()  # type: ignore[no-any-return]

    specs: list[dict[str, object]] = []

    def add(
        name: str,
        role: str,
        topic: str,
        content: dict[str, object],
        key: dict[str, object],
        cites: list[dict[str, str]],
        node: str | None = None,
    ) -> None:
        specs.append(
            {
                "node": node or f"learn-{name}",
                "type": name,
                "role": role,
                "topic": topic,
                "content": content,
                "key": key,
                "cites": cites,
            }
        )

    content, key = fx("reading")
    # markdown as the writers produce it: a bold term, a passage marker, a table passage
    content["body"] = (
        "⟦P1⟧ An **alcohol rub** takes twenty seconds when hands are not visibly soiled."
        "\n\nSoap and water takes forty seconds and is the method when they are."
        "\n\n| Method | Seconds |\n|---|---|\n| Alcohol rub | 20 |\n| Soap and water | 40 |"
    )
    content["citations"] = public(chips[:1])
    add("reading", "teach", "t1", content, key, chips[:1])
    content, key = fx("figure")
    content["caption"] = "Seconds of hand hygiene by method."
    content["numbers"] = [20, 40]
    content["citation"] = public(chips[1:2])[0]
    key["source_numbers"] = [20, 40]
    add("figure", "example", "t1", content, key, chips[1:2])
    content, key = fx("diagram")
    content["caption"] = "**Hand hygiene** as the passage orders it: `alcohol rub` first. ⟦P1⟧"
    content["nodes"] = [
        {"id": "n0", "label": "alcohol rub"},
        {"id": "n1", "label": "soap and water"},
    ]
    content["edges"] = [{"from": "n0", "to": "n1", "label": "when hands are soiled"}]
    content["citations"] = public(chips[:1])
    add("diagram", "example", "t1", content, key, chips[:1])
    content, key = fx("choice")
    content["stem"] = "How long does an alcohol rub take?"
    content["options"] = ["Twenty seconds", "Forty seconds", "One minute"]
    content["citations"] = public(chips[:1])
    key["correct"] = [0]
    add("choice", "check", "t1", content, key, chips[:1])
    content, key = fx("matching")
    content["pairs"] = [
        {"left": "Alcohol rub", "right": "twenty seconds"},
        {"left": "Soap and water", "right": "forty seconds"},
    ]
    content["citations"] = public(chips)
    key["mapping"] = {"Alcohol rub": "twenty seconds", "Soap and water": "forty seconds"}
    key["mapping_by_index"] = {"0": 0, "1": 1}
    add("matching", "practice", "t1", content, key, chips)
    content, key = fx("cloze")
    content["text"] = "Gloves do not replace ___ ___."
    content["blanks"] = ["___", "___"]
    key["blanks"] = ["hand", "hygiene"]
    add("cloze", "practice", "t1", content, key, chips[:1])
    content, key = fx("ordering")
    content["prompt"] = "Put the steps of hand hygiene in order."
    content["items"] = ["Wet the hands", "Rub for twenty seconds", "Dry the hands"]
    content["citations"] = public(chips[:1])
    key["order_by_index"] = [0, 1, 2]
    add("ordering", "practice", "t1", content, key, chips[:1])
    content, key = fx("scenario_practice")
    content["scenario"] = "Sort two ward situations into risk tiers."
    content["items"] = [
        {"text": "Skipping the rub before a patient", "handle": "P1"},
        {"text": "Wearing gloves after the rub", "handle": "P2"},
    ]
    content["citations"] = public(chips)
    key["labels"] = {
        "Skipping the rub before a patient": "high-risk",
        "Wearing gloves after the rub": "minimal",
    }
    key["labels_by_index"] = {"0": "high-risk", "1": "minimal"}
    key["passages_by_index"] = {"0": "P1", "1": "P2"}
    add("scenario_practice", "practice", "t1", content, key, chips)
    content, key = fx("spot_the_error")
    content["sentences"] = [
        "Alcohol rub takes forty seconds when hands are not visibly soiled.",
        "print(rub, seconds)\n...\nalcohol rub 20\nsoap and water 40",
    ]
    key.update(
        {
            "planted_sentence": content["sentences"][0],
            "planted_index": 0,
            "kind": "number_date",
            "passage": "P1",
            "true_sentence": LEARN_PASSAGES[0],
        }
    )
    add("spot_the_error", "practice", "t1", content, key, chips[:1])
    content, key = fx("choice")
    content["stem"] = "What do gloves replace?"
    content["options"] = ["Nothing, hand hygiene stays", "Hand hygiene", "Soap"]
    content["citations"] = public(chips[:1])
    key["correct"] = [0]
    add("choice", "test", "t2", content, key, chips[:1], node="learn-mt-choice")
    content, key = fx("matching")
    content["pairs"] = [
        {"left": "Alcohol rub", "right": "twenty seconds"},
        {"left": "Soap and water", "right": "forty seconds"},
    ]
    content["citations"] = public(chips)
    key["mapping"] = {"Alcohol rub": "twenty seconds", "Soap and water": "forty seconds"}
    key["mapping_by_index"] = {"0": 0, "1": 1}
    add("matching", "test", "t2", content, key, chips, node="learn-mt-matching")
    content, key = fx("spot_the_error")
    content["sentences"] = [
        "Soap and water takes forty seconds and is used when hands are soiled.",
        "Gloves replace hand hygiene.",
    ]
    key.update(
        {
            "planted_sentence": content["sentences"][1],
            "planted_index": 1,
            "kind": "negation",
            "passage": "P1",
            "true_sentence": LEARN_PASSAGES[3],
        }
    )
    add("spot_the_error", "test", "t2", content, key, chips[:1], node="learn-mt-spot")

    suffix = uuid.uuid4().hex[:8]
    topics: dict[str, list[dict[str, object]]] = {"t1": [], "t2": []}
    for spec in specs:
        topics[str(spec["topic"])].append(spec)

    def skeleton(spec: dict[str, object]) -> BlockSkeleton:
        return BlockSkeleton(
            plan_node_id=f"{spec['node']}-{suffix}",
            role=str(spec["role"]),
            block_type=str(spec["type"]),
            brief="brief",
            source_refs=[SourceRef(handle="P1")],
        )

    tree = CourseTree(
        modules=[
            ModuleNode(
                plan_node_id=f"m-{suffix}",
                title="Hand hygiene",
                purpose="Clean hands at the right moments.",
                entry_knowledge="none",
                exit_knowledge="clean hands",
                topics=[
                    TopicNode(
                        plan_node_id=f"t1-{suffix}",
                        kind="lesson",
                        title="Rub or wash: choosing the method for hands that are not visibly soiled",
                        objectives=["Choose the method"],
                        through_line_note="c",
                        blocks=[skeleton(x) for x in topics["t1"]],
                    ),
                    TopicNode(
                        plan_node_id=f"t2-{suffix}",
                        kind="module_test",
                        title="Hygiene test",
                        objectives=["Show it"],
                        through_line_note="c",
                        blocks=[skeleton(x) for x in topics["t2"]],
                    ),
                ],
            )
        ]
    )
    version_map = await courses.write_tree(session, course_id=cid, tree=tree, plan_digest="learn")
    for spec in specs:
        ids = version_map[f"{spec['node']}-{suffix}"]
        spec["ids"] = ids
        await session.execute(
            sa.text(
                "UPDATE block_versions SET content = CAST(:c AS jsonb), answer_key = CAST(:k AS jsonb) WHERE id = :v"
            ),
            {
                "v": ids["version_id"],
                "c": json.dumps(spec["content"]),
                "k": json.dumps(spec["key"]),
            },
        )
        for cite in spec["cites"]:  # type: ignore[attr-defined]
            await session.execute(
                sa.text(
                    "INSERT INTO citations (block_version_id, passage_id, claim_path, quote, quote_start, quote_end, verified_by) VALUES (:v, :p, 'c', :q, 0, :e, 'code')"
                ),
                {
                    "v": ids["version_id"],
                    "p": cite["_pid"],
                    "q": cite["quote"],
                    "e": len(cite["quote"]),
                },
            )
    topic_ids = {
        str(r[0]): str(r[1])
        for r in (
            await session.execute(
                sa.text(
                    "SELECT t.plan_node_id, t.id FROM topics t JOIN modules m ON m.id = t.module_id WHERE m.course_id = :c"
                ),
                {"c": str(cid)},
            )
        ).all()
    }
    await session.execute(
        sa.text("UPDATE topics SET scenario = CAST(:s AS jsonb) WHERE id = :t"),
        {"s": json.dumps({"introduces_terms": ["alcohol rub"]}), "t": topic_ids[f"t1-{suffix}"]},
    )
    await session.commit()
    version = await courses.publish_course(session, course_id=cid)
    translation = (
        await session.execute(
            sa.text(
                "INSERT INTO course_translations (id, course_version_id, language, status) VALUES (gen_random_uuid(), :v, 'nb', 'partial') RETURNING id"
            ),
            {"v": str(version)},
        )
    ).scalar_one()
    norsk = {
        "reading": {
            "body": "Hendene gnis med alkohol i tjue sekunder når de ikke er synlig skitne.",
            "citations": public(chips[:1]),
        },
        "choice": {
            "stem": "Hvor lenge tar alkoholgnidning?",
            "options": ["Tjue sekunder", "Førti sekunder", "Ett minutt"],
            "citations": public(chips[:1]),
        },
    }
    for spec in specs:
        if spec["node"] not in ("learn-reading", "learn-choice"):
            continue
        translated = norsk[str(spec["type"])]
        await session.execute(
            sa.text(
                "INSERT INTO translation_blocks (id, translation_id, plan_node_id, block_type, status, content, translated_answer_key, source_digest) VALUES (gen_random_uuid(), :t, :n, :bt, 'ok', CAST(:c AS jsonb), CAST(:k AS jsonb), :d)"
            ),
            {
                "t": str(translation),
                "n": spec["ids"]["plan_node_id"]
                if "plan_node_id" in spec["ids"]
                else f"{spec['node']}-{suffix}",
                "bt": spec["type"],
                "c": json.dumps(translated),
                "k": json.dumps(spec["key"]),
                "d": block_digest(dict(spec["content"]), dict(spec["key"])),
            },  # type: ignore[arg-type]
        )
    return {
        "learn": str(cid),
        "learn_topic": topic_ids[f"t1-{suffix}"],
        "learn_test": topic_ids[f"t2-{suffix}"],
    }


async def seed_labs(session: AsyncSession, owner: str) -> dict[str, str]:
    from app.runs.executor import new_run

    labs: dict[str, str] = {}
    sources = await seed_planned_course(session, owner, "Sources lab")
    await add_source(
        session,
        sources,
        "WHO hand hygiene guide",
        "Clean hands before touching a patient.\n\nClean hands after touching a patient.",
        "pending",
        "CC BY 4.0",
    )
    await add_source(
        session, sources, "Nursing handbook", "Gloves do not replace hand hygiene.", "pending", ""
    )
    await add_source(
        session,
        sources,
        "Clinic protocol",
        "Alcohol rub takes twenty seconds.\n\nSoap and water takes forty seconds.",
        "approved",
        "Internal",
    )
    labs["sources"] = str(sources)
    fix = await seed_planned_course(session, owner, "Fix lab")
    source = await add_source(
        session,
        fix,
        "Fix lab protocol",
        "Alcohol rub takes twenty seconds.\n\nSoap and water takes forty seconds.",
        "approved",
        "Internal",
    )
    await hold_blocks(
        session,
        fix,
        source,
        [
            "The claim about twenty seconds is not in the cited passage.",
            "grounding: Drop the claim or cite P1 for it. (evidence: Alcohol rub takes forty seconds.)",
            "The answer is visible in the question.",
        ],
    )
    labs["fix"] = str(fix)
    spent = await new_run(
        session,
        course_id=fix,
        kind="generate",
        dag=[{"step_key": "block:fix-a", "handler": "none", "depends_on": []}],
        estimate={
            "blocks": 6,
            "calls": 12,
            "tokens": 1000,
            "cost_usd": "1.00",
            "seconds": 30.0,
            "note": "Forecast, not a limit.",
        },
    )
    await session.execute(
        sa.text(
            "INSERT INTO llm_calls (agent, model, prompt_version, request, run_id, cost_usd) VALUES ('author', 'muse-spark-1.3', 'v1', '{}'::jsonb, :r, 0.1234)"
        ),
        {"r": str(spent)},
    )
    labs["live_run"] = str(spent)
    labs["conflict"] = str(await seed_planned_course(session, owner, "Conflict lab"))
    labs["dnd"] = str(await seed_planned_course(session, owner, "Drag lab"))
    publish = await seed_planned_course(session, owner, "Publish lab")
    await give_content(session, publish)
    await session.execute(
        sa.text(
            "UPDATE blocks SET status = 'accepted', accepted_version_id = current_version_id WHERE topic_id IN (SELECT t.id FROM topics t"
            " JOIN modules m ON m.id = t.module_id WHERE m.course_id = :c)"
        ),
        {"c": str(publish)},
    )
    labs["publish"] = str(publish)
    labs.update(await seed_translations(session, owner))
    labs.update(await seed_learn(session, owner))
    return labs


async def seed_notification(session: AsyncSession, user: str) -> None:
    await session.execute(
        sa.text(
            "INSERT INTO notifications (user_id, kind, payload) VALUES (:u, 'operation.done', CAST(:p AS jsonb))"
        ),
        {
            "u": user,
            "p": json.dumps(
                {"operation_id": "00000000-0000-0000-0000-00000000e2e1", "status": "done"}
            ),
        },
    )


async def seed_course(session: AsyncSession, owner: str) -> dict[str, str]:
    from app.runs.executor import new_run, stop_run
    from app.services.courses import create_course

    cid = await seed_planned_course(session, owner, "Hand hygiene")
    await add_source(
        session, cid, "Hygiene guide", "Wash hands.\n\nUse alcohol rub.", "approved", "CC BY 4.0"
    )
    editing = await seed_planned_course(session, owner, "Plan editing")
    empty = await create_course(
        session, owner_id=owner, title="Empty course", language="en", scope_tags=[]
    )
    run = await new_run(
        session,
        course_id=cid,
        kind="generate",
        dag=[{"step_key": "block:t1-a", "handler": "none", "depends_on": []}],
        estimate={
            "blocks": 6,
            "calls": 12,
            "tokens": 1000,
            "cost_usd": "0.42",
            "seconds": 30.0,
            "note": "Forecast, not a limit.",
        },
    )
    await session.execute(
        sa.text(
            "INSERT INTO run_steps (run_id, name, status, step_key, depends_on, input_digest, attempt, error)"
            " VALUES (:r, 'block:t1-a', 'failed', 'block:t1-a', '[]'::jsonb, 'x', 2, 'model quota exhausted')"
        ),
        {"r": str(run)},
    )
    await stop_run(session, run_id=run, marker="retry policy exhausted at block:t1-a")
    from app.llm.calls import log_call

    step = (
        await session.execute(
            sa.text("SELECT id FROM run_steps WHERE run_id = :r AND step_key = 'block:t1-a'"),
            {"r": str(run)},
        )
    ).scalar_one()
    for agent, tokens_in, tokens_out, ms in (
        ("architect", 100, 50, 4100),
        ("block_teach", 80, 40, 2300),
    ):
        await log_call(
            session,
            agent=agent,
            model="muse-spark-1.3",
            prompt_version="v1",
            input_tokens=tokens_in,
            cached_tokens=0,
            output_tokens=tokens_out,
            duration_ms=ms,
            request={
                "model": "muse-spark-1.3",
                "input": f"{agent} task",
                "instructions": f"{agent} rules",
                "prompt_cache_key": f"{agent}-cache",
                "safety_identifier": "seeded-hash",
            },
            response={"ok": True},
            run_id=run,
            step_id=step,
            response_id="resp_0a1b2c3d4e5f6071" if agent == "architect" else "resp_8293a4b5c6d7e8f9",
        )
    extra = await seed_labs(session, owner)
    return {
        "course": str(cid),
        "editing": str(editing),
        "empty": str(empty),
        "run": str(run),
        **extra,
    }


async def seed() -> dict[str, dict[str, str]]:
    sys.path.insert(0, str(ROOT / "src"))
    from app.auth.session import create_session

    engine = create_async_engine(URL)
    out: dict[str, dict[str, str]] = {}
    async with engine.begin() as conn:
        await conn.execute(sa.text("UPDATE blocks SET current_version_id = NULL"))
        for table in (
            "citations",
            "llm_calls",
            "run_steps",
            "runs",
            "course_versions",
            "block_versions",
            "blocks",
            "topics",
            "modules",
            "course_sources",
            "passages",
            "sources",
            "profile_constraints",
            "course_profiles",
            "courses",
            "invite_uses",
            "notifications",
            "operations",
            "audit_log",
            "auth_states",
            "sessions",
            "identities",
            "invites",
            "user_roles",
            "role_permissions",
            "users",
            "roles",
        ):
            try:
                async with conn.begin_nested():
                    await conn.execute(sa.text(f'DELETE FROM "{table}"'))
            except Exception:
                pass
    async with AsyncSession(engine, expire_on_commit=False) as session:
        for key, (email, name, admin) in USERS.items():
            row = (
                await session.execute(
                    sa.text(
                        "INSERT INTO users (email, display_name, avatar_url, is_superadmin)"
                        " VALUES (:e, :n, '', :a) RETURNING id"
                    ),
                    {"e": email, "n": name, "a": admin},
                )
            ).one()
            token, csrf = await create_session(session, row[0])
            out[key] = {
                "id": str(row[0]),
                "email": email,
                "name": name,
                "token": token,
                "csrf": csrf,
            }
        await session.commit()
        out["ids"] = await seed_course(session, out["admin"]["id"])
        await seed_notification(session, out["student"]["id"])
        await session.commit()
    await engine.dispose()
    return out


def main() -> None:
    ensure_database()
    migrate()
    sessions = asyncio.run(seed())
    target = ROOT / "tmp"
    target.mkdir(exist_ok=True)
    (target / "e2e-sessions.json").write_text(json.dumps(sessions, indent=2))
    print(f"seeded {len(sessions)} users into {DB_NAME}")


if __name__ == "__main__":
    main()
