"""Two-level planning facts: sections, their assignment, the scoped handoff rule (permanent)."""

from __future__ import annotations

from app.agents.architect import _handoff_errors
from app.agents.outline import check_output
from app.course.sections import module_handles, sections_of
from app.course.tree import ArchitectOutput


def _passage(handle: str, address: str, text: str = "Some text here.") -> dict[str, object]:
    return {
        "handle": handle,
        "address": address,
        "text": text,
        "source_id": "s",
        "is_content": True,
    }


def test_sections_follow_headings_and_pages_in_document_order() -> None:
    passages = [
        _passage("P1", "h:Muse Code>Install/para1"),
        _passage("P2", "h:Muse Code>Install/para2"),
        _passage("P3", "h:Muse Code>Install>Mac/para1"),
        _passage("P4", "h:Muse Code>First run/para1"),
        _passage("P5", "p3/para1"),
        _passage("P6", "p3/para2"),
        _passage("P7", "p4/para1"),
    ]
    sections = sections_of(passages)
    assert [(s["id"], s["title"], s["handles"]) for s in sections] == [
        ("S1", "Muse Code>Install", ["P1", "P2", "P3"]),
        ("S2", "Muse Code>First run", ["P4"]),
        ("S3", "page 3", ["P5", "P6"]),
        ("S4", "page 4", ["P7"]),
    ]
    assert module_handles(sections, ["S3", "S1"]) == ["P1", "P2", "P3", "P5", "P6"]


def test_outline_check_wants_every_section_in_one_topic_or_declared() -> None:
    def outline(
        sections: list[str],
        uncovered: list[dict[str, str]],
        topic_sections: list[list[str]] | None = None,
        requires: list[str] | None = None,
    ) -> dict[str, object]:
        per_topic = topic_sections if topic_sections is not None else [sections]
        return {
            "learner": "a case worker",
            "case": "a triage tool with its owner named",
            "modules": [
                {
                    "plan_node_id": "m1",
                    "title": "t",
                    "purpose": "p",
                    "entry_knowledge": "e",
                    "exit_knowledge": "x",
                    "sections": sections,
                    "topics": [
                        {
                            "plan_node_id": f"m1-t{i + 1}",
                            "title": f"topic {i + 1}",
                            "annotation": "What it teaches and how the case advances.",
                            "introduces_terms": [],
                            "prepares_for": "",
                            "requires": requires or [],
                            "sections": own,
                        }
                        for i, own in enumerate(per_topic)
                    ],
                }
            ],
            "uncovered": uncovered,
            "questions": [],
        }

    assert check_output(outline(["S1", "S2"], []), ["S1", "S2"]) == []
    assert (
        check_output(outline(["S1"], [{"section": "S2", "reason": "changelog"}]), ["S1", "S2"])
        == []
    )
    assert check_output(outline(["S1"], []), ["S1", "S2"]) == [
        "every section is placed in a module or listed in uncovered with a reason; missing: ['S2']"
    ]
    assert "unknown section 'S9'" in check_output(outline(["S1", "S9"], []), ["S1"])[0]
    assert "already placed" in check_output(outline(["S1", "S1"], []), ["S1"])[0]
    # inside a module every section belongs to exactly one topic
    assert check_output(outline(["S1", "S2"], [], [["S1"], ["S2"]]), ["S1", "S2"]) == []
    assert check_output(outline(["S1", "S2"], [], [["S1"]]), ["S1", "S2"]) == [
        "module m1: sections ['S2'] belong to no topic"
    ]
    assert (
        "already covered by topic m1-t1"
        in check_output(outline(["S1", "S2"], [], [["S1"], ["S1", "S2"]]), ["S1", "S2"])[0]
    )
    # requires names only an earlier topic
    assert (
        "requires 'm1-t9', which is not an earlier topic"
        in check_output(outline(["S1"], [], requires=["m1-t9"]), ["S1"])[0]
    )


def test_a_scoped_module_hands_its_last_topic_over_too() -> None:
    def topic(node: str) -> dict[str, object]:
        return {
            "plan_node_id": node,
            "kind": "lesson",
            "title": node,
            "objectives": ["o"],
            "through_line_note": "n",
            "blocks": [
                {
                    "plan_node_id": f"{node}-b1",
                    "role": "teach",
                    "block_type": "reading",
                    "brief": "b",
                    "source_refs": [{"handle": "P1"}],
                }
            ],
        }

    output = ArchitectOutput.model_validate(
        {
            "modules": [
                {
                    "plan_node_id": "m1",
                    "title": "m",
                    "purpose": "p",
                    "entry_knowledge": "e",
                    "exit_knowledge": "x",
                    "topics": [topic("m1-t1"), topic("m1-t2")],
                }
            ],
            "estimate_inputs": {"blocks": 1, "calls": 1, "tokens": 1},
            "through_line": {
                "case": "c",
                "handoffs": [{"topic_plan_node_id": "m1-t1", "note": "on"}],
            },
        }
    )
    # every topic carries a through_line_note, and that note is its handoff
    assert _handoff_errors(output) == []
    assert _handoff_errors(output, require_last=True) == []
    output.modules[0].topics[1].through_line_note = " "
    assert _handoff_errors(output, require_last=True) == [
        "through-line: no handoff for topic boundary after 'm1-t2'"
    ]


def test_block_ids_are_written_by_code_in_topic_order() -> None:
    """A module reply that repeats a block id is repaired, never rejected (m3-t3-b1 twice)."""
    from app.runs.executor import _renumber_blocks

    reply = {
        "modules": [
            {
                "plan_node_id": "m3",
                "topics": [
                    {
                        "plan_node_id": "m3-t3",
                        "blocks": [{"plan_node_id": "m3-t3-b1"}, {"plan_node_id": "m3-t3-b1"}],
                    }
                ],
            }
        ]
    }
    _renumber_blocks(reply)
    assert [b["plan_node_id"] for b in reply["modules"][0]["topics"][0]["blocks"]] == [
        "m3-t3-b1",
        "m3-t3-b2",
    ]
