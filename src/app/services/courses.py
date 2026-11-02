"""Courses: plan tree persistence, author editing, validation, versions.

The plan is an editable contract: the author renames, reorders, and
deletes modules and topics and edits typed constraints; validate_plan()
lists blocking questions; generation refuses to start while a required
question is open. Snapshots are immutable course_versions rows.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.course.tree import ConstraintRow, CourseTree
from app.errors import ConflictError, NotFoundError, UnprocessableError, ValidationError
from app.rbac.principal import Principal

PLAN_EDIT_OPS = (
    "rename_module",
    "rename_topic",
    "move_module",
    "move_topic",
    "delete_module",
    "delete_topic",
    "set_constraint",
    "add_module",
    "add_topic",
)


async def create_course(
    session: AsyncSession,
    *,
    owner_id: UUID,
    title: str,
    language: str = "en",
    scope_tags: list[str] | None = None,
) -> UUID:
    """Create a draft course owned by one user."""
    if not title.strip():
        raise ValidationError("course title must not be empty")
    if language not in ("en", "nb", "uk"):
        raise ValidationError(f"unsupported language: {language}")
    row = (
        await session.execute(
            sa.text(
                "INSERT INTO courses (owner_id, title, language, status, scope_tags)"
                " VALUES (:owner, :title, :lang, 'draft', :tags) RETURNING id"
            ),
            {
                "owner": str(owner_id),
                "title": title.strip(),
                "lang": language,
                "tags": scope_tags or [],
            },
        )
    ).one()
    return UUID(str(row[0]))


async def owns_any_course(session: AsyncSession, *, owner_id: UUID, course_ids: list[UUID]) -> bool:
    """True when the owner holds at least one of the courses (scope checks)."""
    if not course_ids:
        return False
    row = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM courses WHERE owner_id = :owner AND id::text = ANY(:ids)"
            ),
            {"owner": str(owner_id), "ids": [str(course) for course in course_ids]},
        )
    ).scalar_one()
    return int(row) > 0


async def course_facts(session: AsyncSession, course_ids: list[UUID]) -> dict[str, dict[str, int]]:
    """Counters every reader of a course shares (list rows and the course header).

    held_count: blocks held by a check; draft_count: blocks written but not yet
    read or accepted (status draft); pending_sources and source_count: course sources.
    """
    if not course_ids:
        return {}
    ids = [str(item) for item in course_ids]
    blocks = (
        await session.execute(
            sa.text(
                "SELECT m.course_id, count(*) FILTER (WHERE b.status = 'held'),"
                " count(*) FILTER (WHERE b.status = 'draft'), count(*) FROM blocks b"
                " JOIN topics tp ON tp.id = b.topic_id JOIN modules m ON m.id = tp.module_id"
                " WHERE m.course_id = ANY(CAST(:ids AS uuid[])) GROUP BY m.course_id"
            ),
            {"ids": ids},
        )
    ).all()
    sources = (
        await session.execute(
            sa.text(
                "SELECT cs.course_id, count(*) FILTER (WHERE cs.approval = 'pending'"
                " AND s.ingest_status IS DISTINCT FROM 'thin'), count(*)"
                " FROM course_sources cs JOIN sources s ON s.id = cs.source_id"
                " WHERE cs.course_id = ANY(CAST(:ids AS uuid[])) GROUP BY cs.course_id"
            ),
            {"ids": ids},
        )
    ).all()
    out = {
        item: {
            "held_count": 0,
            "draft_count": 0,
            "pending_sources": 0,
            "source_count": 0,
            "written_count": 0,
        }
        for item in ids
    }
    for course, held, draft, total in blocks:
        out[str(course)].update(
            held_count=int(held), draft_count=int(draft), written_count=int(total) - int(draft)
        )
    for course, pending, total in sources:
        out[str(course)].update(pending_sources=int(pending), source_count=int(total))
    return out


async def get_course(session: AsyncSession, *, course_id: UUID) -> dict[str, Any]:
    """One course row with its profile; 404 when missing."""
    row = (
        await session.execute(
            sa.text(
                "SELECT id, owner_id, title, language, status, scope_tags,"
                " row_version, published_version_id FROM courses WHERE id = :course"
            ),
            {"course": str(course_id)},
        )
    ).first()
    if row is None:
        raise NotFoundError(f"course {course_id} not found")
    profile = (
        await session.execute(
            sa.text(
                "SELECT methodology_codes, bloom_level, audience, dreyfus_start"
                " FROM course_profiles WHERE course_id = :course"
            ),
            {"course": str(course_id)},
        )
    ).first()
    constraints = (
        await session.execute(
            sa.text(
                "SELECT key, kind, value, required, question, answer"
                " FROM profile_constraints WHERE course_id = :course ORDER BY key"
            ),
            {"course": str(course_id)},
        )
    ).all()
    counts = (
        await session.execute(
            sa.text(
                "SELECT count(DISTINCT tp.id), count(b.id) FROM modules m"
                " LEFT JOIN topics tp ON tp.module_id = m.id"
                " LEFT JOIN blocks b ON b.topic_id = tp.id WHERE m.course_id = :course"
            ),
            {"course": str(course_id)},
        )
    ).one()
    current_version = row[7]
    if current_version is None:
        latest = (
            await session.execute(
                sa.text(
                    "SELECT id FROM course_versions WHERE course_id = :course"
                    " ORDER BY created_at DESC LIMIT 1"
                ),
                {"course": str(course_id)},
            )
        ).first()
        current_version = latest[0] if latest else None
    translations = (
        await session.execute(
            sa.text(
                "SELECT ct.language, ct.status,"
                " count(*) FILTER (WHERE tb.status = 'ok'),"
                " count(*) FILTER (WHERE tb.status = 'held'),"
                " count(*) FILTER (WHERE tb.status = 'failed')"
                " FROM (SELECT DISTINCT ON (t.language) t.id, t.language, t.status"
                " FROM course_translations t JOIN course_versions cv"
                " ON cv.id = t.course_version_id WHERE cv.course_id = :course"
                " ORDER BY t.language, t.created_at DESC) ct"
                " LEFT JOIN translation_blocks tb ON tb.translation_id = ct.id"
                " GROUP BY ct.language, ct.status ORDER BY ct.language"
            ),
            {"course": str(course_id)},
        )
    ).all()
    facts = (await course_facts(session, [course_id]))[str(course_id)]
    return {
        **facts,
        "id": str(row[0]),
        "owner_id": str(row[1]),
        "title": row[2],
        "language": row[3],
        "status": row[4],
        "scope_tags": list(row[5] or []),
        "row_version": row[6],
        "published_version_id": str(row[7]) if row[7] else None,
        "current_version_id": str(current_version) if current_version else None,
        "topic_count": int(counts[0]),
        "block_count": int(counts[1]),
        "translations": [
            {
                "language": str(item[0]),
                "status": str(item[1]),
                "ok": int(item[2]),
                "held": int(item[3]),
                "failed": int(item[4]),
            }
            for item in translations
        ],
        "profile": (
            {
                "methodology_codes": profile[0],
                "bloom_level": profile[1],
                "audience": profile[2],
                "dreyfus_start": profile[3],
            }
            if profile
            else None
        ),
        "constraints": [
            {
                "key": item[0],
                "kind": item[1],
                "value": item[2],
                "required": item[3],
                "question": item[4],
                "answer": item[5],
            }
            for item in constraints
        ],
    }


async def set_profile(
    session: AsyncSession,
    *,
    course_id: UUID,
    methodology_codes: list[str],
    bloom_level: str,
    audience: str,
    dreyfus_start: str = "novice",
    constraints: list[dict[str, Any]] | None = None,
) -> None:
    """Replace the course profile with typed constraint rows."""
    from app.course.methodologies import BLOOM_LEVELS, METHODOLOGIES

    if bloom_level not in BLOOM_LEVELS:
        raise ValidationError(f"unknown bloom level: {bloom_level!r}; use one of {BLOOM_LEVELS}")
    known = {entry["code"] for entry in METHODOLOGIES}
    unknown = [code for code in methodology_codes if code not in known]
    if unknown:
        raise ValidationError(f"unknown methodologies: {unknown}")
    await get_course(session, course_id=course_id)
    await session.execute(
        sa.text(
            "INSERT INTO course_profiles"
            " (course_id, methodology_codes, bloom_level, audience, dreyfus_start)"
            " VALUES (:course, CAST(:codes AS jsonb), :bloom, :audience, :dreyfus)"
            " ON CONFLICT (course_id) DO UPDATE SET"
            " methodology_codes = EXCLUDED.methodology_codes,"
            " bloom_level = EXCLUDED.bloom_level, audience = EXCLUDED.audience,"
            " dreyfus_start = EXCLUDED.dreyfus_start"
        ),
        {
            "course": str(course_id),
            "codes": json.dumps(methodology_codes),
            "bloom": bloom_level,
            "audience": audience,
            "dreyfus": dreyfus_start,
        },
    )
    for raw in constraints or []:
        row = ConstraintRow.model_validate(raw)
        await session.execute(
            sa.text(
                "INSERT INTO profile_constraints"
                " (course_id, kind, key, value, required, question, answer)"
                " VALUES (:course, :kind, :key, CAST(:value AS jsonb), :required,"
                " :question, CAST(:answer AS jsonb))"
                " ON CONFLICT (course_id, key) DO UPDATE SET kind = EXCLUDED.kind,"
                " value = EXCLUDED.value, required = EXCLUDED.required,"
                " question = EXCLUDED.question, answer = EXCLUDED.answer"
            ),
            {
                "course": str(course_id),
                "kind": row.kind,
                "key": row.key,
                "value": json.dumps(row.value),
                "required": row.required,
                "question": row.question,
                "answer": json.dumps(row.answer) if row.answer is not None else None,
            },
        )


async def write_tree(
    session: AsyncSession,
    *,
    course_id: UUID,
    tree: CourseTree,
    plan_digest: str,
    run_step_id: UUID | None = None,
    held: dict[str, str] | None = None,
    uncovered: list[dict[str, Any]] | None = None,
    scenario: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, str]]:
    """Replace the plan tree: modules, topics, block skeletons in order.

    `scenario` maps a topic plan_node_id to the outline's row for it
    (annotation, terms, prepares_for, requires, handles, passages);
    a topic without a row stores an empty object.

    Returns block plan_node_id to {"block_id", "version_id"} for the
    citation writer (same transaction). Plan-written versions carry
    the step id and become current; author-edited blocks (a current
    version with run_step_id NULL) are restored on top of the rewrite
    so re-planning never deletes author work. `held` maps block
    plan_node_id to a hold reason: those blocks are written held with
    the reason instead of draft (BRIEF §3: a hold returns the block).
    """
    await session.execute(
        sa.text("UPDATE courses SET status = 'draft' WHERE id = :course AND status = 'generated'"),
        {"course": str(course_id)},
    )
    kept = (
        await session.execute(
            sa.text(
                "SELECT b.plan_node_id, bv.content, bv.answer_key FROM blocks b"
                " JOIN block_versions bv ON bv.id = b.current_version_id"
                " JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :course AND bv.run_step_id IS NULL"
            ),
            {"course": str(course_id)},
        )
    ).all()
    author_versions = {
        str(node): {"content": content, "answer_key": answer} for node, content, answer in kept
    }
    await session.execute(
        sa.text("DELETE FROM modules WHERE course_id = :course"), {"course": str(course_id)}
    )
    if tree.through_line is not None:
        await session.execute(
            sa.text("UPDATE courses SET through_line = CAST(:tl AS jsonb) WHERE id = :course"),
            {"tl": tree.through_line.model_dump_json(), "course": str(course_id)},
        )
    if uncovered is not None:
        await session.execute(
            sa.text("UPDATE courses SET uncovered = CAST(:u AS jsonb) WHERE id = :course"),
            {"u": json.dumps(uncovered), "course": str(course_id)},
        )
    for module_index, module in enumerate(tree.modules):
        module_id = (
            await session.execute(
                sa.text(
                    "INSERT INTO modules"
                    " (course_id, ordinal, title, purpose, entry_knowledge,"
                    " exit_knowledge)"
                    " VALUES (:course, :ord, :title, :purpose, :entry, :exit)"
                    " RETURNING id"
                ),
                {
                    "course": str(course_id),
                    "ord": module_index,
                    "title": module.title,
                    "purpose": module.purpose,
                    "entry": module.entry_knowledge,
                    "exit": module.exit_knowledge,
                },
            )
        ).one()[0]
        for topic_index, topic in enumerate(module.topics):
            topic_id = (
                await session.execute(
                    sa.text(
                        "INSERT INTO topics (module_id, ordinal, kind, title, objectives,"
                        " through_line_note, plan_node_id, plan_digest, scenario)"
                        " VALUES (:module, :ord, :kind, :title,"
                        " CAST(:objectives AS jsonb), :note, :node, :digest,"
                        " CAST(:scenario AS jsonb))"
                        " RETURNING id"
                    ),
                    {
                        "module": str(module_id),
                        "ord": topic_index,
                        "kind": topic.kind,
                        "title": topic.title,
                        "objectives": json.dumps(topic.objectives),
                        "note": topic.through_line_note,
                        "node": topic.plan_node_id,
                        "digest": plan_digest,
                        "scenario": json.dumps(
                            {
                                **(scenario or {}).get(topic.plan_node_id, {}),
                                "misconception": topic.misconception,
                            }
                        ),
                    },
                )
            ).one()[0]
            for block_index, block in enumerate(topic.blocks):
                hold_reason = (held or {}).get(block.plan_node_id)
                block_id = (
                    await session.execute(
                        sa.text(
                            "INSERT INTO blocks (topic_id, plan_node_id, ordinal, role,"
                            " block_type, brief, status, hold_reason)"
                            " VALUES (:topic, :node, :ord, :role, :type, :brief,"
                            " :status, :reason) RETURNING id"
                        ),
                        {
                            "topic": str(topic_id),
                            "node": block.plan_node_id,
                            "ord": block_index,
                            "role": block.role,
                            "type": block.block_type,
                            "brief": block.brief,
                            "status": "held" if hold_reason else "draft",
                            "reason": hold_reason,
                        },
                    )
                ).one()[0]
                version_id = (
                    await session.execute(
                        sa.text(
                            "INSERT INTO block_versions (block_id, version_no, content,"
                            " answer_key, schema_version, run_step_id)"
                            " VALUES (:block, 1, CAST(:content AS jsonb), NULL,"
                            " '1', :step) RETURNING id"
                        ),
                        {
                            "block": str(block_id),
                            "content": json.dumps(
                                {
                                    "brief": block.brief,
                                    "source_handles": [ref.handle for ref in block.source_refs],
                                    "example_spec": block.example_spec.model_dump(),
                                }
                            ),
                            "step": str(run_step_id) if run_step_id is not None else None,
                        },
                    )
                ).one()[0]
                await session.execute(
                    sa.text("UPDATE blocks SET current_version_id = :version WHERE id = :block"),
                    {"version": str(version_id), "block": str(block_id)},
                )
                author = author_versions.get(block.plan_node_id)
                if author is not None:
                    restored_id = (
                        await session.execute(
                            sa.text(
                                "INSERT INTO block_versions (block_id, version_no, content,"
                                " answer_key, schema_version, run_step_id)"
                                " VALUES (:block, 2, CAST(:content AS jsonb),"
                                " CAST(:answer AS jsonb), '1', NULL) RETURNING id"
                            ),
                            {
                                "block": str(block_id),
                                "content": json.dumps(author["content"]),
                                "answer": json.dumps(author["answer_key"])
                                if author["answer_key"] is not None
                                else None,
                            },
                        )
                    ).one()[0]
                    await session.execute(
                        sa.text(
                            "UPDATE blocks SET current_version_id = :version WHERE id = :block"
                        ),
                        {"version": str(restored_id), "block": str(block_id)},
                    )
    by_node: dict[str, dict[str, str]] = {}
    rows = (
        await session.execute(
            sa.text(
                "SELECT plan_node_id, id, current_version_id FROM blocks"
                " WHERE topic_id IN (SELECT t.id FROM topics t"
                " JOIN modules m ON m.id = t.module_id WHERE m.course_id = :course)"
            ),
            {"course": str(course_id)},
        )
    ).all()
    for node, block_id, version_id in rows:
        by_node[str(node)] = {"block_id": str(block_id), "version_id": str(version_id)}
    return by_node


async def set_author_questions(
    session: AsyncSession, *, course_id: UUID, questions: list[dict[str, Any]]
) -> None:
    """Store the outline's questions as required constraints; an answered one keeps its answer.

    Each question blocks generation until the author answers it on the
    plan page (blocking_questions); a re-plan that drops a question
    removes its unanswered row.
    """
    keys = [str(q.get("key", "")) for q in questions if str(q.get("key", "")).strip()]
    await session.execute(
        sa.text(
            "DELETE FROM profile_constraints WHERE course_id = :course"
            " AND kind = 'author_question' AND answer IS NULL AND NOT (key = ANY(:keys))"
        ),
        {"course": str(course_id), "keys": keys},
    )
    for question in questions:
        key = str(question.get("key", "")).strip()
        if not key:
            continue
        await session.execute(
            sa.text(
                "INSERT INTO profile_constraints (course_id, kind, key, value, required, question)"
                " VALUES (:course, 'author_question', :key, '{}'::jsonb, true, :question)"
                " ON CONFLICT (course_id, key) DO UPDATE SET question = EXCLUDED.question,"
                " required = true"
            ),
            {"course": str(course_id), "key": key, "question": str(question.get("question", ""))},
        )


async def read_through_line(session: AsyncSession, *, course_id: UUID) -> dict[str, Any]:
    """The course-level through-line object (case + per-topic handoffs)."""
    row = (
        await session.execute(
            sa.text("SELECT through_line FROM courses WHERE id = :course"),
            {"course": str(course_id)},
        )
    ).first()
    if row is None:
        raise NotFoundError(f"course {course_id} not found")
    return dict(row[0] or {})


def stored_tree_contract(tree: dict[str, Any]) -> CourseTree:
    """Build the validatable contract from a stored tree dict.

    Modules do not persist plan_node_id (topics and blocks do), so
    positional ids fill the contract field read_tree cannot provide;
    every check that matters — prescriptions, refs, types, briefs —
    runs on the stored rows.
    """
    from copy import deepcopy

    filled = deepcopy(tree)
    for index, module in enumerate(filled.get("modules", [])):
        module.setdefault("plan_node_id", f"m{index}")
    return CourseTree.model_validate(filled)


async def validate_course_tree(session: AsyncSession, *, course_id: UUID) -> list[str]:
    """Re-run the tree contract in code: prescriptions, refs, types.

    The validate route calls this on the stored plan (not only the
    blocking questions), so an edited or drifted tree is rejected
    before generation.
    """
    from app.course.methodologies import prescription_for
    from app.course.tree import validate_tree

    course = await get_course(session, course_id=course_id)
    profile = course["profile"] or {}
    prescription = prescription_for(list(profile.get("methodology_codes", [])))
    tree = await read_tree(session, course_id=course_id)
    try:
        contract = stored_tree_contract(tree)
    except Exception as exc:
        return [f"tree contract: {exc}"]
    return validate_tree(contract, prescription)


def _source_refs(content: Any) -> list[dict[str, Any]]:
    """Handle plus the stored passage id; source_id and address_label fill in later."""
    body = content if isinstance(content, dict) else {}
    passages = body.get("source_passages")
    mapping = passages if isinstance(passages, dict) else {}
    return [
        {
            "handle": handle,
            "passage_id": mapping.get(handle),
            "source_id": None,
            "address_label": None,
        }
        for handle in body.get("source_handles", [])
    ]


async def _resolve_source_refs(session: AsyncSession, tree: dict[str, Any]) -> None:
    """Fill source_id and address_label of every ref whose passage id is known."""
    from app.i18n.citations import format_citation_address

    refs = [
        ref
        for module in tree["modules"]
        for topic in module["topics"]
        for block in topic["blocks"]
        for ref in block["source_refs"]
        if ref["passage_id"]
    ]
    if not refs:
        return
    rows = (
        await session.execute(
            sa.text(
                "SELECT id, source_id, address, address_key FROM passages"
                " WHERE id::text = ANY(:ids)"
            ),
            {"ids": sorted({str(ref["passage_id"]) for ref in refs})},
        )
    ).all()
    found = {
        str(r[0]): (
            str(r[1]),
            format_citation_address({**dict(r[2] or {}), "address_key": r[3]}, "en"),
        )
        for r in rows
    }
    for ref in refs:
        hit = found.get(str(ref["passage_id"]))
        if hit is None:
            ref["passage_id"] = None
        else:
            ref["source_id"], ref["address_label"] = hit


async def record_source_passages(
    session: AsyncSession,
    *,
    version_map: dict[str, dict[str, str]],
    handles: dict[str, dict[str, str]],
) -> None:
    """Store handle -> passage id beside each plan version so refs can link to rows."""
    for node_id, mapping in handles.items():
        target = version_map.get(node_id)
        if target is None or not mapping:
            continue
        await session.execute(
            sa.text(
                "UPDATE block_versions SET content = content ||"
                " jsonb_build_object('source_passages', CAST(:m AS jsonb)) WHERE id = :v"
            ),
            {"m": json.dumps(mapping), "v": target["version_id"]},
        )


async def read_tree(session: AsyncSession, *, course_id: UUID) -> dict[str, Any]:
    """Read the plan tree back in ordinal order."""
    modules = (
        await session.execute(
            sa.text(
                "SELECT id, title, purpose, entry_knowledge, exit_knowledge"
                " FROM modules WHERE course_id = :course ORDER BY ordinal"
            ),
            {"course": str(course_id)},
        )
    ).all()
    tree: dict[str, Any] = {"modules": []}
    for module in modules:
        topics = (
            await session.execute(
                sa.text(
                    "SELECT id, kind, title, objectives, through_line_note, plan_node_id,"
                    " scenario FROM topics WHERE module_id = :module ORDER BY ordinal"
                ),
                {"module": str(module[0])},
            )
        ).all()
        module_node: dict[str, Any] = {
            "id": str(module[0]),
            "title": module[1],
            "purpose": module[2],
            "entry_knowledge": module[3],
            "exit_knowledge": module[4],
            "topics": [],
        }
        for topic in topics:
            blocks = (
                await session.execute(
                    sa.text(
                        "SELECT b.id, b.plan_node_id, b.role, b.block_type, b.brief,"
                        " b.status, b.hold_reason, bv.content FROM blocks b"
                        " LEFT JOIN block_versions bv ON bv.id = ("
                        " SELECT id FROM block_versions WHERE block_id = b.id"
                        " AND content -> 'source_handles' IS NOT NULL"
                        " ORDER BY version_no DESC LIMIT 1)"
                        " WHERE b.topic_id = :topic ORDER BY b.ordinal"
                    ),
                    {"topic": str(topic[0])},
                )
            ).all()
            module_node["topics"].append(
                {
                    "id": str(topic[0]),
                    "kind": topic[1],
                    "title": topic[2],
                    "objectives": topic[3],
                    "through_line_note": topic[4],
                    "plan_node_id": topic[5],
                    "scenario": dict(topic[6] or {}),
                    "blocks": [
                        {
                            "id": str(block[0]),
                            "plan_node_id": block[1],
                            "role": block[2],
                            "block_type": block[3],
                            "brief": block[4],
                            "status": block[5],
                            "hold_reason": block[6],
                            "source_refs": _source_refs(block[7]),
                            "source_handles": list(
                                (block[7] or {}).get("source_handles", [])
                                if isinstance(block[7], dict)
                                else []
                            ),
                            "example_spec": dict(
                                (block[7] or {}).get("example_spec", {})
                                if isinstance(block[7], dict)
                                else {}
                            ),
                        }
                        for block in blocks
                    ],
                }
            )
        tree["modules"].append(module_node)
    await _resolve_source_refs(session, tree)
    try:
        tree["through_line"] = await read_through_line(session, course_id=course_id) or None
    except NotFoundError:
        tree["through_line"] = None
    uncovered_row = (
        await session.execute(
            sa.text("SELECT uncovered FROM courses WHERE id = :course"),
            {"course": str(course_id)},
        )
    ).first()
    tree["uncovered"] = list(uncovered_row[0] or []) if uncovered_row is not None else []
    return tree


async def blocking_questions(session: AsyncSession, *, course_id: UUID) -> list[dict[str, Any]]:
    """Required constraints with no answer: generation stays refused."""
    rows = (
        await session.execute(
            sa.text(
                "SELECT key, kind, question FROM profile_constraints"
                " WHERE course_id = :course AND required AND answer IS NULL ORDER BY key"
            ),
            {"course": str(course_id)},
        )
    ).all()
    return [{"key": row[0], "kind": row[1], "question": row[2]} for row in rows]


async def edit_plan(
    session: AsyncSession,
    *,
    course_id: UUID,
    operations: list[dict[str, Any]],
    base_version: int | None = None,
) -> dict[str, Any]:
    """Apply author edits: rename/move/delete modules and topics, constraints.

    With `base_version` the edit is refused with 409 when another writer
    moved the course first (lost-update protection for the plan editor).
    """
    if base_version is not None:
        current = (
            await session.execute(
                sa.text("SELECT row_version FROM courses WHERE id = :course"),
                {"course": str(course_id)},
            )
        ).first()
        if current is None:
            raise NotFoundError(f"course {course_id} not found")
        if int(current[0]) != base_version:
            raise ConflictError(
                f"plan changed underneath the editor: base {base_version},"
                f" current {int(current[0])}"
            )
    for operation in operations:
        op = operation.get("op")
        if op not in PLAN_EDIT_OPS:
            raise ValidationError(f"unknown plan edit: {op!r}")
        if op == "rename_module":
            await _rename(session, "modules", course_id, operation, "title")
        elif op == "rename_topic":
            await _rename(session, "topics", course_id, operation, "title")
        elif op == "move_module":
            await _move(session, "modules", course_id, operation)
        elif op == "move_topic":
            await _move(session, "topics", course_id, operation)
        elif op == "delete_module":
            await _delete(session, "modules", course_id, operation)
        elif op == "delete_topic":
            await _delete(session, "topics", course_id, operation)
        elif op == "set_constraint":
            await _set_constraint_answer(session, course_id, operation)
        elif op == "add_module":
            await _add_module(session, course_id, operation)
        elif op == "add_topic":
            await _add_topic(session, course_id, operation)
    await session.execute(
        sa.text("UPDATE courses SET row_version = row_version + 1 WHERE id = :course"),
        {"course": str(course_id)},
    )
    current = (
        await session.execute(
            sa.text("SELECT row_version FROM courses WHERE id = :course"),
            {"course": str(course_id)},
        )
    ).scalar_one()
    tree = await read_tree(session, course_id=course_id)
    return {**tree, "row_version": int(current)}


async def _add_module(session: AsyncSession, course_id: UUID, operation: dict[str, Any]) -> None:
    """Append an empty module; the backend picks the next ordinal."""
    title = str(operation.get("title", "")).strip()
    if not title:
        raise ValidationError("title must not be empty")
    await session.execute(
        sa.text(
            "INSERT INTO modules (course_id, ordinal, title) VALUES (:course,"
            " (SELECT COALESCE(max(ordinal), -1) + 1 FROM modules WHERE course_id = :course),"
            " :title)"
        ),
        {"course": str(course_id), "title": title},
    )


async def _add_topic(session: AsyncSession, course_id: UUID, operation: dict[str, Any]) -> None:
    """Append an empty topic to one module of this course (next ordinal)."""
    title = str(operation.get("title", "")).strip()
    if not title:
        raise ValidationError("title must not be empty")
    kind = str(operation.get("kind", "lesson"))
    if kind not in ("lesson", "module_test"):
        raise ValidationError("kind must be lesson or module_test")
    module = (
        await session.execute(
            sa.text("SELECT id FROM modules WHERE id::text = :m AND course_id = :course"),
            {"m": str(operation.get("module_id")), "course": str(course_id)},
        )
    ).first()
    if module is None:
        raise NotFoundError(f"plan node {operation.get('module_id')} not found")
    await session.execute(
        sa.text(
            "INSERT INTO topics (module_id, ordinal, kind, title, plan_node_id) VALUES (:module,"
            " (SELECT COALESCE(max(ordinal), -1) + 1 FROM topics WHERE module_id = :module),"
            " :kind, :title, 'author-' || gen_random_uuid()::text)"
        ),
        {"module": str(module[0]), "kind": kind, "title": title},
    )


async def _rename(
    session: AsyncSession, table: str, course_id: UUID, operation: dict[str, Any], column: str
) -> None:
    title = str(operation.get("title", "")).strip()
    if not title:
        raise ValidationError("title must not be empty")
    if table == "modules":
        row = (
            await session.execute(
                sa.text(
                    "UPDATE modules SET title = :title WHERE id = :node"
                    " AND course_id = :course RETURNING id"
                ),
                {"title": title, "node": str(operation.get("id")), "course": str(course_id)},
            )
        ).first()
    else:
        row = (
            await session.execute(
                sa.text(
                    "UPDATE topics SET title = :title WHERE id = :node AND module_id IN"
                    " (SELECT id FROM modules WHERE course_id = :course) RETURNING id"
                ),
                {"title": title, "node": str(operation.get("id")), "course": str(course_id)},
            )
        ).first()
    if row is None:
        raise NotFoundError(f"plan node {operation.get('id')} not found")


async def _move(
    session: AsyncSession, table: str, course_id: UUID, operation: dict[str, Any]
) -> None:
    """Reorder siblings around one move; a full ordinal list still applies.

    The client sends a single move ({id, ordinal}) and the backend
    shifts the siblings itself, so no two siblings ever share an
    ordinal. A client-sent full ordinal list ({id, ordinals: {...}})
    keeps working and is applied verbatim after validation.
    """
    bulk = operation.get("ordinals")
    if bulk is not None:
        await _move_bulk(session, table, course_id, operation, bulk)
        return
    try:
        ordinal = int(operation.get("ordinal", -1))
    except (TypeError, ValueError):
        raise ValidationError("ordinal must be an integer") from None
    if ordinal < 0:
        raise ValidationError("ordinal must be non-negative")
    node_id = str(operation.get("id"))
    siblings = await _siblings(session, table, course_id, node_id)
    current = next((index for index, sibling in enumerate(siblings) if sibling == node_id), None)
    if current is None:
        raise NotFoundError(f"plan node {operation.get('id')} not found")
    target = min(ordinal, len(siblings) - 1)
    ordered = [sibling for sibling in siblings if sibling != node_id]
    ordered.insert(target, node_id)
    await _write_ordinals(session, table, ordered)


async def _siblings(session: AsyncSession, table: str, course_id: UUID, node_id: str) -> list[str]:
    """Sibling ids in ordinal order within the node's own parent scope."""
    if table == "modules":
        rows = (
            await session.execute(
                sa.text("SELECT id FROM modules WHERE course_id = :course ORDER BY ordinal, id"),
                {"course": str(course_id)},
            )
        ).all()
        return [str(row[0]) for row in rows]
    parent = (
        await session.execute(
            sa.text(
                "SELECT t.module_id FROM topics t JOIN modules m ON m.id = t.module_id"
                " WHERE t.id = :node AND m.course_id = :course"
            ),
            {"node": node_id, "course": str(course_id)},
        )
    ).first()
    if parent is None:
        return []
    rows = (
        await session.execute(
            sa.text("SELECT id FROM topics WHERE module_id = :parent ORDER BY ordinal, id"),
            {"parent": str(parent[0])},
        )
    ).all()
    return [str(row[0]) for row in rows]


async def _write_ordinals(session: AsyncSession, table: str, ordered: list[str]) -> None:
    """Persist one dense ordinal run; the deferrable unique tolerates it."""
    column = "modules" if table == "modules" else "topics"
    for position, node_id in enumerate(ordered):
        await session.execute(
            sa.text(f"UPDATE {column} SET ordinal = :ord WHERE id = :node"),
            {"ord": position, "node": node_id},
        )


async def _move_bulk(
    session: AsyncSession,
    table: str,
    course_id: UUID,
    operation: dict[str, Any],
    bulk: Any,
) -> None:
    """Apply a client-sent full ordinal list after validating it."""
    if not isinstance(bulk, dict) or not bulk:
        raise ValidationError("ordinals must be a non-empty object of id to ordinal")
    try:
        wanted = {str(key): int(value) for key, value in bulk.items()}
    except (TypeError, ValueError):
        raise ValidationError("ordinals must map ids to integers") from None
    if any(value < 0 for value in wanted.values()):
        raise ValidationError("ordinal must be non-negative")
    if len(set(wanted.values())) != len(wanted):
        raise ValidationError("ordinals must be unique per sibling")
    node_id = str(operation.get("id") or next(iter(wanted)))
    siblings = set(await _siblings(session, table, course_id, node_id))
    if set(wanted) - siblings:
        raise NotFoundError(f"plan node {operation.get('id')} not found")
    for target_id, ordinal in wanted.items():
        column = "modules" if table == "modules" else "topics"
        await session.execute(
            sa.text(f"UPDATE {column} SET ordinal = :ord WHERE id = :node"),
            {"ord": ordinal, "node": target_id},
        )


async def _delete(
    session: AsyncSession, table: str, course_id: UUID, operation: dict[str, Any]
) -> None:
    if table == "modules":
        row = (
            await session.execute(
                sa.text(
                    "DELETE FROM modules WHERE id = :node AND course_id = :course RETURNING id"
                ),
                {"node": str(operation.get("id")), "course": str(course_id)},
            )
        ).first()
    else:
        row = (
            await session.execute(
                sa.text(
                    "DELETE FROM topics WHERE id = :node AND module_id IN"
                    " (SELECT id FROM modules WHERE course_id = :course) RETURNING id"
                ),
                {"node": str(operation.get("id")), "course": str(course_id)},
            )
        ).first()
    if row is None:
        raise NotFoundError(f"plan node {operation.get('id')} not found")


async def _set_constraint_answer(
    session: AsyncSession, course_id: UUID, operation: dict[str, Any]
) -> None:
    key = str(operation.get("key", ""))
    if not key:
        raise ValidationError("constraint key must not be empty")
    row = (
        await session.execute(
            sa.text(
                "UPDATE profile_constraints SET answer = CAST(:answer AS jsonb)"
                " WHERE course_id = :course AND key = :key RETURNING id"
            ),
            {
                "answer": json.dumps(operation.get("answer")),
                "course": str(course_id),
                "key": key,
            },
        )
    ).first()
    if row is None:
        raise NotFoundError(f"constraint {key} not found")


async def _pin_block_versions(session: AsyncSession, tree: dict[str, Any]) -> None:
    """Record each block's current version id in the snapshot (learners read it)."""
    nodes = [
        block
        for module in tree["modules"]
        for topic in module["topics"]
        for block in topic["blocks"]
    ]
    if not nodes:
        return
    rows = (
        await session.execute(
            sa.text("SELECT id, current_version_id FROM blocks WHERE id::text = ANY(:ids)"),
            {"ids": [block["id"] for block in nodes]},
        )
    ).all()
    pinned = {str(row[0]): (str(row[1]) if row[1] else None) for row in rows}
    for block in nodes:
        block["version_id"] = pinned.get(block["id"])


async def snapshot_version(
    session: AsyncSession, *, course_id: UUID, publish: bool = False
) -> UUID:
    """Freeze the current plan tree as an immutable course version."""
    tree = await read_tree(session, course_id=course_id)
    await _pin_block_versions(session, tree)
    profile = await get_course(session, course_id=course_id)
    row = (
        await session.execute(
            sa.text(
                "INSERT INTO course_versions (course_id, snapshot, published_at)"
                " VALUES (:course, CAST(:snapshot AS jsonb),"
                " CASE WHEN :publish THEN now() ELSE NULL END) RETURNING id"
            ),
            {
                "course": str(course_id),
                "snapshot": json.dumps({"tree": tree, "profile": profile.get("profile")}),
                "publish": publish,
            },
        )
    ).one()
    version_id = UUID(str(row[0]))
    if publish:
        await session.execute(
            sa.text(
                "UPDATE courses SET status = 'published', published_version_id = :version"
                " WHERE id = :course"
            ),
            {"version": str(version_id), "course": str(course_id)},
        )
    return version_id


async def list_courses(
    session: AsyncSession, *, owner_id: UUID, is_superadmin: bool
) -> list[dict[str, Any]]:
    """Course rows for the library view, scoped to the caller.

    Superadmins see every course; anyone else sees only their own.
    Flags avoid extra round trips: profile presence, plan presence,
    and the count of unanswered required questions.
    """
    rows = (
        await session.execute(
            sa.text(
                "SELECT c.id, c.title, c.language, c.status,"
                " EXISTS(SELECT 1 FROM course_profiles p WHERE p.course_id = c.id),"
                " EXISTS(SELECT 1 FROM modules m WHERE m.course_id = c.id),"
                " (SELECT count(*) FROM profile_constraints pc WHERE pc.course_id = c.id"
                " AND pc.required AND pc.answer IS NULL),"
                " c.scope_tags,"
                " (SELECT p.audience FROM course_profiles p WHERE p.course_id = c.id),"
                " (SELECT count(*) FROM topics tp JOIN modules m ON m.id = tp.module_id"
                " WHERE m.course_id = c.id),"
                " (SELECT count(*) FROM blocks b JOIN topics tp ON tp.id = b.topic_id"
                " JOIN modules m ON m.id = tp.module_id WHERE m.course_id = c.id)"
                " FROM courses c"
                + ("" if is_superadmin else " WHERE c.owner_id = :owner")
                + " ORDER BY c.created_at, c.id"
            ),
            {} if is_superadmin else {"owner": str(owner_id)},
        )
    ).all()
    facts = await course_facts(session, [UUID(str(row[0])) for row in rows])
    return [
        {
            **facts[str(row[0])],
            "id": str(row[0]),
            "title": row[1],
            "language": row[2],
            "status": row[3],
            "has_profile": bool(row[4]),
            "has_plan": bool(row[5]),
            "open_questions": int(row[6]),
            "scope_tags": list(row[7] or []),
            "audience": row[8] or "",
            "topic_count": int(row[9]),
            "block_count": int(row[10]),
        }
        for row in rows
    ]


async def list_tags(session: AsyncSession, *, actor: Principal) -> list[dict[str, Any]]:
    """Existing scope tags with the visible courses carrying each, for the pickers.

    Visibility follows the course library (superadmin: all, others: own);
    a scoped caller sees only the tags inside its scope.
    """
    rows = await list_courses(session, owner_id=actor.user_id, is_superadmin=actor.is_superadmin)
    scope = actor.scope_for("course.edit")
    by_tag: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        for tag in row["scope_tags"]:
            if scope is not None and tag not in scope:
                continue
            by_tag.setdefault(tag, []).append({"id": row["id"], "title": row["title"]})
    return [{"tag": tag, "courses": by_tag[tag]} for tag in sorted(by_tag)]


async def course_estimate(session: AsyncSession, *, course_id: UUID) -> dict[str, Any]:
    """Forecast for one course from its current block count (never a limit)."""
    from app.agents.estimator import forecast

    await get_course(session, course_id=course_id)
    count = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM blocks b JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id WHERE m.course_id = :course"
            ),
            {"course": str(course_id)},
        )
    ).scalar_one()
    return forecast({"blocks": int(count)})


async def mark_generated_if_complete(session: AsyncSession, *, course_id: UUID) -> bool:
    """draft -> generated when every block has a current version that passes its schema."""
    from importlib import import_module

    rows = (
        await session.execute(
            sa.text(
                "SELECT b.block_type, bv.content FROM blocks b JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id"
                " LEFT JOIN block_versions bv ON bv.id = b.current_version_id"
                " WHERE m.course_id = :course"
            ),
            {"course": str(course_id)},
        )
    ).all()
    if not rows:
        return False
    for block_type, content in rows:
        if content is None:
            return False
        try:
            errors = import_module(f"app.blocks.{block_type}.schema").validate(dict(content))
        except ModuleNotFoundError:
            return False
        if errors:
            return False
    result = await session.execute(
        sa.text(
            "UPDATE courses SET status = 'generated' WHERE id = :course AND status = 'draft'"
            " RETURNING id"
        ),
        {"course": str(course_id)},
    )
    return result.first() is not None


async def block_course_id(session: AsyncSession, *, block_id: UUID) -> UUID:
    """The course one block belongs to (scope checks for block writes)."""
    row = (
        await session.execute(
            sa.text(
                "SELECT m.course_id FROM blocks b JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id WHERE b.id = :block"
            ),
            {"block": str(block_id)},
        )
    ).first()
    if row is None:
        raise NotFoundError(f"block {block_id} not found")
    return UUID(str(row[0]))


async def held_blocks(session: AsyncSession, *, course_id: UUID) -> dict[str, Any]:
    """Held blocks with their first citing passage, plus a batch rewrite quote."""
    from app.agents.estimator import forecast

    await get_course(session, course_id=course_id)
    rows = (
        await session.execute(
            sa.text(
                "SELECT b.id, t.title, b.brief, b.hold_reason, b.current_version_id"
                " FROM blocks b JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :course AND b.status = 'held'"
                " ORDER BY m.ordinal, t.ordinal, b.ordinal"
            ),
            {"course": str(course_id)},
        )
    ).all()
    blocks: list[dict[str, Any]] = []
    for block_id, topic_title, brief, hold_reason, version_id in rows:
        cited = (
            await session.execute(
                sa.text(
                    "SELECT p.text, p.address_key FROM citations c"
                    " JOIN passages p ON p.id = c.passage_id"
                    " WHERE c.block_version_id = :version"
                    " ORDER BY c.created_at LIMIT 1"
                ),
                {"version": str(version_id) if version_id else None},
            )
        ).first()
        blocks.append(
            {
                "id": str(block_id),
                "topic_title": topic_title,
                "brief": brief,
                "hold_reason": hold_reason,
                "passage": cited[0] if cited else "",
                "address": cited[1] if cited else "",
            }
        )
    quote = forecast({"blocks": len(blocks)})
    return {"blocks": blocks, "batch_quote_usd": quote["cost_usd"]}


async def accept_block(session: AsyncSession, *, block_id: UUID) -> None:
    """Accept a held block: only held blocks can be accepted."""
    row = (
        await session.execute(
            sa.text("SELECT status FROM blocks WHERE id = :block"),
            {"block": str(block_id)},
        )
    ).first()
    if row is None:
        raise NotFoundError(f"block {block_id} not found")
    if row[0] != "held":
        raise ValidationError(f"block {block_id} is {row[0]}; only held blocks are accepted")
    await session.execute(
        sa.text(
            "UPDATE blocks SET status = 'accepted', hold_reason = NULL,"
            " accepted_version_id = current_version_id WHERE id = :block"
        ),
        {"block": str(block_id)},
    )


async def set_block_brief(session: AsyncSession, *, block_id: UUID, brief: str) -> None:
    """Edit a block brief in place; the current version follows, keys never leak."""
    cleaned = brief.strip()
    if not cleaned:
        raise ValidationError("brief must not be empty")
    row = (
        await session.execute(
            sa.text("SELECT current_version_id FROM blocks WHERE id = :block"),
            {"block": str(block_id)},
        )
    ).first()
    if row is None:
        raise NotFoundError(f"block {block_id} not found")
    await session.execute(
        sa.text("UPDATE blocks SET brief = :brief WHERE id = :block"),
        {"brief": cleaned, "block": str(block_id)},
    )
    if row[0] is not None:
        await session.execute(
            sa.text(
                "UPDATE block_versions SET content = content ||"
                " jsonb_build_object('brief', CAST(:brief AS text)) WHERE id = :version"
            ),
            {"brief": cleaned, "version": str(row[0])},
        )


async def submit_rewrite_operation(
    session: AsyncSession,
    *,
    actor: Any,
    kind: str,
    params: dict[str, Any],
    defer: Any = None,
) -> UUID:
    """Queue a block-rewrite operation (202); the worker drives it to done.

    The operation is committed and handed to the queue on the same
    submit path as plan/generate, so it never stands orphaned: until
    T05 the worker answers 409 "not available until the generation
    pipeline lands" and the operation fails loudly instead of idling
    in queued.
    """
    from app.rbac.require import require
    from app.services.audit import write_audit

    require(actor, "course.generate")
    row = (
        await session.execute(
            sa.text(
                "INSERT INTO operations (kind, status, params, progress, actor_id)"
                " VALUES (:kind, 'queued', CAST(:params AS jsonb), 0, :actor)"
                " RETURNING id"
            ),
            {
                "kind": kind,
                "params": json.dumps({**params}),
                "actor": str(actor.user_id),
            },
        )
    ).one()
    operation_id = UUID(str(row[0]))
    await write_audit(
        session,
        actor_id=actor.user_id,
        action=f"{kind}.submit",
        entity="operation",
        entity_id=str(operation_id),
        meta=params,
    )
    await session.commit()
    if defer is not None:
        await defer(operation_id)
    return operation_id


async def publish_course(session: AsyncSession, *, course_id: UUID) -> UUID:
    """Publish a course: snapshot immutably, 422 while a held block stands."""
    await get_course(session, course_id=course_id)
    held = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM blocks b JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :course AND b.status = 'held'"
            ),
            {"course": str(course_id)},
        )
    ).scalar_one()
    if int(held):
        raise UnprocessableError(
            f"course has {int(held)} held block(s); accept or rewrite each first"
        )
    return await snapshot_version(session, course_id=course_id, publish=True)


def list_methodologies() -> list[dict[str, Any]]:
    """The registered methodologies for the profile picker (code, name, one line)."""
    from app.course.methodologies import METHODOLOGIES

    return [
        {
            "code": entry["code"],
            "name": entry["name"],
            "description": entry["description"],
            "compatible_with": list(entry["compatible_with"]),
        }
        for entry in METHODOLOGIES
    ]
