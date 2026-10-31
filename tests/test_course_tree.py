"""Course tree contract: Pydantic model plus strict schemas and invariants.

The tree is the contract every generator reads: block skeletons carry
a role and a topic-without-answer brief plus at least one source ref,
module purposes and outcomes are filled, and the methodology
prescription per topic is honoured.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.course.tree import (
    ARCHITECT_SCHEMA,
    LIBRARIAN_SCHEMA,
    RESEARCHER_SCHEMA,
    CourseTree,
    validate_tree,
)
from app.llm.schema import validate_strict_subset


def _tree(**overrides: Any) -> CourseTree:
    blocks = overrides.get(
        "blocks",
        [
            {
                "plan_node_id": "b1",
                "role": "teach",
                "block_type": "reading",
                "brief": "The Article 4 duty to ensure AI literacy",
                "source_refs": [{"handle": "P1"}],
            },
            {
                "plan_node_id": "b2",
                "role": "example",
                "block_type": "reading",
                "brief": "How oversight looks in municipal services",
                "source_refs": [{"handle": "P2"}],
            },
            {
                "plan_node_id": "b3",
                "role": "check",
                "block_type": "choice",
                "brief": "Who must ensure AI literacy under Article 4?",
                "source_refs": [{"handle": "P1"}],
            },
        ],
    )
    return CourseTree.model_validate(
        {
            "modules": [
                {
                    "plan_node_id": "m1",
                    "title": "The literacy duty",
                    "purpose": "Ground the duty.",
                    "entry_knowledge": "None.",
                    "exit_knowledge": "Staff can name the duty.",
                    "topics": [
                        {
                            "plan_node_id": "t1",
                            "kind": "lesson",
                            "title": "Basics",
                            "objectives": ["Name the duty"],
                            "through_line_note": "Opens the case.",
                            "blocks": blocks,
                        }
                    ],
                }
            ]
        }
    )


PRESCRIPTION = {"lesson": ["teach", "example", "check"], "module_test": ["test"]}


def test_tree_schemas_are_strict_subset() -> None:
    for name, schema in (
        ("researcher", RESEARCHER_SCHEMA),
        ("architect", ARCHITECT_SCHEMA),
        ("librarian", LIBRARIAN_SCHEMA),
    ):
        validate_strict_subset(schema, name=name)


def test_valid_tree_has_no_errors() -> None:
    assert validate_tree(_tree(), PRESCRIPTION) == []


def test_block_brief_must_not_contain_the_answer() -> None:
    tree = _tree(
        blocks=[
            {
                "plan_node_id": "b9",
                "role": "check",
                "block_type": "choice",
                "brief": "Providers and deployers must ensure AI literacy.",
                "source_refs": [{"handle": "P1"}],
            }
        ]
    )
    errors = validate_tree(tree, {"lesson": ["check"]})
    assert any("never contain the answer" in error for error in errors)


def test_teach_brief_must_not_ask() -> None:
    tree = _tree(
        blocks=[
            {
                "plan_node_id": "b9",
                "role": "teach",
                "block_type": "reading",
                "brief": "What is the literacy duty?",
                "source_refs": [{"handle": "P1"}],
            }
        ]
    )
    errors = validate_tree(tree, {"lesson": ["teach"]})
    assert any("not a question" in error for error in errors)


def test_prescription_mismatch_is_blocking() -> None:
    errors = validate_tree(_tree(), {"lesson": ["teach", "check"], "module_test": ["test"]})
    assert any("prescription" in error for error in errors)


def test_duplicate_plan_node_ids_are_blocking() -> None:
    tree = _tree(
        blocks=[
            {
                "plan_node_id": "b1",
                "role": "teach",
                "block_type": "reading",
                "brief": "First topic",
                "source_refs": [{"handle": "P1"}],
            },
            {
                "plan_node_id": "b1",
                "role": "teach",
                "block_type": "reading",
                "brief": "Second topic",
                "source_refs": [{"handle": "P1"}],
            },
        ]
    )
    errors = validate_tree(tree, {"lesson": ["teach", "teach"]})
    assert any("duplicate" in error for error in errors)


def test_architect_output_checks_estimate_size() -> None:
    import json
    from pathlib import Path

    from app.agents.architect import check_output

    payload = json.loads(Path("tests/fixtures/architect_output.json").read_text())
    assert check_output(payload, PRESCRIPTION) == []
    payload["estimate_inputs"]["blocks"] = 99
    assert any("estimate blocks" in error for error in check_output(payload, PRESCRIPTION))


def test_source_ref_handles_are_validated() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _tree(
            blocks=[
                {
                    "plan_node_id": "b9",
                    "role": "teach",
                    "block_type": "reading",
                    "brief": "A topic",
                    "source_refs": [{"handle": "nope"}],
                }
            ]
        )
