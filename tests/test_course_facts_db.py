"""GET /courses/{id} carries the facts the course header and the translation panel show."""

from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.course.tree import CourseTree
from app.services import courses as course_service


def _tree() -> CourseTree:
    def block(node: str) -> dict[str, Any]:
        return {
            "plan_node_id": node,
            "role": "teach",
            "block_type": "reading",
            "brief": f"Brief {node}",
            "source_refs": [{"handle": "P1"}],
        }

    return CourseTree.model_validate(
        {
            "modules": [
                {
                    "plan_node_id": "m1",
                    "title": "Module",
                    "purpose": "Purpose.",
                    "entry_knowledge": "None.",
                    "exit_knowledge": "Something.",
                    "topics": [
                        {
                            "plan_node_id": "t1",
                            "kind": "lesson",
                            "title": "Topic",
                            "objectives": ["Name it"],
                            "through_line_note": "Opens.",
                            "blocks": [block("b1"), block("b2"), block("b3")],
                        }
                    ],
                }
            ]
        }
    )


async def _course(session: AsyncSession) -> UUID:
    user = (
        await session.execute(
            sa.text("INSERT INTO users (email, display_name) VALUES (:e, 'F') RETURNING id"),
            {"e": f"facts-{uuid.uuid4().hex[:10]}@example.com"},
        )
    ).scalar_one()
    course = await course_service.create_course(
        session, owner_id=user, title="Facts", language="en"
    )
    await course_service.set_profile(
        session,
        course_id=course,
        methodology_codes=["direct_instruction"],
        bloom_level="understand",
        audience="new nurses",
    )
    await course_service.write_tree(session, course_id=course, tree=_tree(), plan_digest="facts")
    await session.commit()
    return UUID(str(course))


async def test_course_row_has_counts_and_no_version_before_a_snapshot(
    t03_session: AsyncSession,
) -> None:
    course = await course_service.get_course(t03_session, course_id=await _course(t03_session))
    assert (course["topic_count"], course["block_count"]) == (1, 3)
    assert course["current_version_id"] is None
    assert course["translations"] == []


async def test_course_row_lists_translations_with_ok_held_failed_counts(
    t03_session: AsyncSession,
) -> None:
    course_id = await _course(t03_session)
    version = await course_service.snapshot_version(t03_session, course_id=course_id)
    for language, status, states in (
        ("nb", "partial", ["ok", "held", "held"]),
        ("uk", "failed", ["failed"]),
    ):
        translation = (
            await t03_session.execute(
                sa.text(
                    "INSERT INTO course_translations (id, course_version_id, language, status)"
                    " VALUES (gen_random_uuid(), :v, :l, :s) RETURNING id"
                ),
                {"v": str(version), "l": language, "s": status},
            )
        ).scalar_one()
        for index, state in enumerate(states):
            await t03_session.execute(
                sa.text(
                    "INSERT INTO translation_blocks"
                    " (id, translation_id, plan_node_id, block_type, status)"
                    " VALUES (gen_random_uuid(), :t, :n, 'reading', :s)"
                ),
                {"t": str(translation), "n": f"b{index}", "s": state},
            )
    await t03_session.commit()
    course = await course_service.get_course(t03_session, course_id=course_id)
    assert course["current_version_id"] == str(version)
    assert course["translations"] == [
        {"language": "nb", "status": "partial", "ok": 1, "held": 2, "failed": 0},
        {"language": "uk", "status": "failed", "ok": 0, "held": 0, "failed": 1},
    ]
