"""Probe: a plan varies its exercise types and tests every lesson of a module."""

from __future__ import annotations

from app.course.tree import (
    BlockSkeleton,
    CourseTree,
    ModuleNode,
    SourceRef,
    TopicNode,
    validate_tree,
)

PRESCRIPTION = {"lesson": ["teach", "example", "check"], "module_test": ["test"]}


def _block(node: str, role: str, block_type: str) -> BlockSkeleton:
    brief = "Which step applies?" if role in ("check", "practice", "test") else "The step"
    return BlockSkeleton(
        plan_node_id=node,
        role=role,
        block_type=block_type,
        brief=brief,
        source_refs=[SourceRef(handle="P1")],
    )


def _lesson(node: str, check_type: str) -> TopicNode:
    return TopicNode(
        plan_node_id=node,
        kind="lesson",
        title=node,
        objectives=["Do it"],
        through_line_note="next",
        blocks=[
            _block(f"{node}-b1", "teach", "reading"),
            _block(f"{node}-b2", "example", "reading"),
            _block(f"{node}-b3", "check", check_type),
        ],
    )


def _module(check_types: list[str], test_types: list[str]) -> CourseTree:
    topics = [_lesson(f"t{i}", kind) for i, kind in enumerate(check_types, start=1)]
    topics.append(
        TopicNode(
            plan_node_id="m1-test",
            kind="module_test",
            title="Test",
            objectives=["Show it"],
            through_line_note="end",
            blocks=[_block(f"m1-test-b{i}", "test", t) for i, t in enumerate(test_types, start=1)],
        )
    )
    return CourseTree(
        modules=[
            ModuleNode(
                plan_node_id="m1",
                title="M",
                purpose="p",
                entry_knowledge="e",
                exit_knowledge="x",
                topics=topics,
            )
        ]
    )


def test_three_checks_of_one_type_are_rejected() -> None:
    tree = _module(["choice", "choice", "choice"], ["choice", "matching", "cloze"])
    errors = validate_tree(tree, PRESCRIPTION)
    assert any("all 3 exercises are 'choice'" in e for e in errors), errors


def test_mixed_checks_pass() -> None:
    tree = _module(["choice", "matching", "cloze"], ["choice", "matching", "cloze"])
    errors = validate_tree(tree, PRESCRIPTION)
    assert errors == []


def test_module_test_covers_every_lesson() -> None:
    errors = validate_tree(_module(["choice", "matching", "cloze"], ["choice"]), PRESCRIPTION)
    assert any("1 test block for 3 lessons" in e for e in errors), errors


def test_module_test_never_repeats_a_type_while_another_is_unused() -> None:
    tree = _module(["choice", "matching", "cloze"], ["choice", "choice", "matching"])
    errors = validate_tree(tree, PRESCRIPTION)
    assert any("repeats 'choice'" in e for e in errors), errors
