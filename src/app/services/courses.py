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
from app.errors import NotFoundError, ValidationError

PLAN_EDIT_OPS = (
    "rename_module",
    "rename_topic",
    "move_module",
    "move_topic",
    "delete_module",
    "delete_topic",
    "set_constraint",
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
    return {
        "id": str(row[0]),
        "owner_id": str(row[1]),
        "title": row[2],
        "language": row[3],
        "status": row[4],
        "scope_tags": list(row[5] or []),
        "row_version": row[6],
        "published_version_id": str(row[7]) if row[7] else None,
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
    from app.course.methodologies import METHODOLOGIES

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
) -> dict[str, dict[str, str]]:
    """Replace the plan tree: modules, topics, block skeletons in order.

    Returns block plan_node_id to {"block_id", "version_id"} for the
    citation writer (same transaction). Plan-written versions carry
    the step id and become current; author-edited blocks (a current
    version with run_step_id NULL) are restored on top of the rewrite
    so re-planning never deletes author work.
    """
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
                        " through_line_note, plan_node_id, plan_digest)"
                        " VALUES (:module, :ord, :kind, :title,"
                        " CAST(:objectives AS jsonb), :note, :node, :digest)"
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
                    },
                )
            ).one()[0]
            for block_index, block in enumerate(topic.blocks):
                block_id = (
                    await session.execute(
                        sa.text(
                            "INSERT INTO blocks (topic_id, plan_node_id, ordinal, role,"
                            " block_type, brief, status)"
                            " VALUES (:topic, :node, :ord, :role, :type, :brief,"
                            " 'draft') RETURNING id"
                        ),
                        {
                            "topic": str(topic_id),
                            "node": block.plan_node_id,
                            "ord": block_index,
                            "role": block.role,
                            "type": block.block_type,
                            "brief": block.brief,
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
                    "SELECT id, kind, title, objectives, through_line_note, plan_node_id"
                    " FROM topics WHERE module_id = :module ORDER BY ordinal"
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
                        " LEFT JOIN block_versions bv ON bv.id = b.current_version_id"
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
                    "blocks": [
                        {
                            "id": str(block[0]),
                            "plan_node_id": block[1],
                            "role": block[2],
                            "block_type": block[3],
                            "brief": block[4],
                            "status": block[5],
                            "hold_reason": block[6],
                            "source_refs": [
                                {"handle": handle}
                                for handle in (
                                    (block[7] or {}).get("source_handles", [])
                                    if isinstance(block[7], dict)
                                    else []
                                )
                            ],
                        }
                        for block in blocks
                    ],
                }
            )
        tree["modules"].append(module_node)
    try:
        tree["through_line"] = await read_through_line(session, course_id=course_id) or None
    except NotFoundError:
        tree["through_line"] = None
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
    session: AsyncSession, *, course_id: UUID, operations: list[dict[str, Any]]
) -> dict[str, Any]:
    """Apply author edits: rename/move/delete modules and topics, constraints."""
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
    await session.execute(
        sa.text("UPDATE courses SET row_version = row_version + 1 WHERE id = :course"),
        {"course": str(course_id)},
    )
    return await read_tree(session, course_id=course_id)


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
    """Reorder by ordinal; the deferrable unique tolerates the swap."""
    try:
        ordinal = int(operation.get("ordinal", -1))
    except (TypeError, ValueError):
        raise ValidationError("ordinal must be an integer") from None
    if ordinal < 0:
        raise ValidationError("ordinal must be non-negative")
    if table == "modules":
        row = (
            await session.execute(
                sa.text(
                    "UPDATE modules SET ordinal = :ord WHERE id = :node"
                    " AND course_id = :course RETURNING id"
                ),
                {"ord": ordinal, "node": str(operation.get("id")), "course": str(course_id)},
            )
        ).first()
    else:
        row = (
            await session.execute(
                sa.text(
                    "UPDATE topics SET ordinal = :ord WHERE id = :node AND module_id IN"
                    " (SELECT id FROM modules WHERE course_id = :course) RETURNING id"
                ),
                {"ord": ordinal, "node": str(operation.get("id")), "course": str(course_id)},
            )
        ).first()
    if row is None:
        raise NotFoundError(f"plan node {operation.get('id')} not found")


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


async def snapshot_version(
    session: AsyncSession, *, course_id: UUID, publish: bool = False
) -> UUID:
    """Freeze the current plan tree as an immutable course version."""
    tree = await read_tree(session, course_id=course_id)
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
