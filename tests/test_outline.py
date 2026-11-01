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


def test_outline_check_wants_every_section_placed_once_or_declared() -> None:
    def outline(sections: list[str], uncovered: list[dict[str, str]]) -> dict[str, object]:
        return {
            "case": "c",
            "modules": [
                {
                    "plan_node_id": "m1",
                    "title": "t",
                    "purpose": "p",
                    "entry_knowledge": "e",
                    "exit_knowledge": "x",
                    "sections": sections,
                }
            ],
            "uncovered": uncovered,
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
    assert _handoff_errors(output) == []
    assert _handoff_errors(output, require_last=True) == [
        "through-line: no handoff for topic boundary after 'm1-t2'"
    ]
