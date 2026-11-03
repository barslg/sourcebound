"""Probes: the sources page, the glossary and the numbered chips of a course."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import sqlalchemy as sa

from app.services import learner_reference as ref


def test_definition_is_the_first_sentence_naming_the_term() -> None:
    body = (
        "⟦P3⟧ The client sends a request. ⟦P4⟧ A **base_url** is the address the client points at; "
        "every call goes there. The base_url never changes mid-call."
    )
    sentence, handle = ref.definition_of("base_url", body)
    assert sentence == "A base_url is the address the client points at; every call goes there."
    assert handle == "P4"
    assert ref.definition_of("missing", body) == ("", "")


async def test_sources_page_numbers_sources_in_reading_order(world: Any) -> None:
    course = await ref.preview_sources(world.s, UUID(str(world.w["course_id"])))
    assert [s["number"] for s in course["sources"]] == list(range(1, len(course["sources"]) + 1))
    first = course["sources"][0]
    assert first["title"] and first["passages_cited"] > 0 and first["blocks_cited"] > 0


async def test_glossary_reads_the_scenario_terms_with_a_definition(world: Any) -> None:
    course_id = UUID(str(world.w["course_id"]))
    topic_id = (
        await world.s.execute(
            sa.text(
                "SELECT t.id FROM topics t JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :c AND t.kind = 'lesson' ORDER BY t.ordinal LIMIT 1"
            ),
            {"c": str(course_id)},
        )
    ).scalar_one()
    body = (
        await world.s.execute(
            sa.text(
                "SELECT bv.content->>'body' FROM blocks b JOIN block_versions bv"
                " ON bv.id = b.current_version_id WHERE b.topic_id = :t"
                " AND b.block_type = 'reading' ORDER BY b.ordinal LIMIT 1"
            ),
            {"t": str(topic_id)},
        )
    ).scalar_one()
    term = str(body).split()[1].strip(".,")
    await world.s.execute(
        sa.text("UPDATE topics SET scenario = CAST(:s AS jsonb) WHERE id = :t"),
        {"s": json.dumps({"introduces_terms": [term, "unseen term"]}), "t": str(topic_id)},
    )
    page = await ref.preview_glossary(world.s, course_id)
    by_term = {e["term"]: e for e in page["terms"]}
    assert set(by_term) == {term, "unseen term"}
    assert term.lower() in by_term[term]["definition"].lower()
    assert by_term[term]["topic_id"] == str(topic_id)
    assert by_term["unseen term"]["definition"] == ""


async def test_chips_carry_the_source_number(world: Any) -> None:
    from app.rbac.principal import load_principal
    from app.services.learner import preview_topic

    course_id = UUID(str(world.w["course_id"]))
    actor = await load_principal(world.s, UUID(str(world.w["user_id"])))
    topic_id = (
        await world.s.execute(
            sa.text(
                "SELECT t.id FROM topics t JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :c ORDER BY m.ordinal, t.ordinal LIMIT 1"
            ),
            {"c": str(course_id)},
        )
    ).scalar_one()
    view = await preview_topic(
        world.s, actor, course_id, UUID(str(topic_id)), language=None, threshold=0.8
    )
    chips = [c for b in view["blocks"] for c in b["citations"]]
    assert chips and all(c["source_number"] >= 1 for c in chips)
    assert "terms" in view
