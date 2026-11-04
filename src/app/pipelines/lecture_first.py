"""Lecture-first pipeline: the brief alone in, one lecture per topic, exercises cut from it.

The plan run is three steps with dependencies only downward: the blueprint
(topics, the through-line, annotations), the lectures (every topic at once,
each from the blueprint alone) and the block plan, which code derives from
the lectures: every paragraph becomes a passage of the course's own
"Lectures" source, the topic's teach block is the lecture itself, and the
exercises are typed by what the paragraphs hold. The generation run then
writes only the exercises, each from its lecture, with one corrector pass.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from app.course.tree import (
    BLOCK_TYPES,
    BlockSkeleton,
    CourseTree,
    ModuleNode,
    SourceRef,
    ThroughLine,
    TopicNode,
)
from app.runs.executor import input_digest_of

METHODOLOGY = "lecture_first"
BLUEPRINT_STEP = "blueprint:course"
LECTURES_STEP = "lectures:course"
BLOCKPLAN_STEP = "blockplan:course"
LECTURE_SOURCE_TITLE = "Lectures"
LESSON_ROLES = ("teach", "check", "practice")

_FENCE_RE = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.DOTALL)
_STEP_LINE_RE = re.compile(r"(?m)^\s*(?:\d+[.)]|[-*])\s+\S")
_PAIR_RE = re.compile(r"(?m)^\s*(?:[-*]\s+)?(?:\*\*[^*]{2,40}\*\*|`[^`]{1,40}`)\s*[:—-]\s+\S")


def is_lecture_first(codes: list[str]) -> bool:
    """True when the course profile chose this pipeline."""
    return METHODOLOGY in [str(c) for c in codes]


def lecture_source_sha(course_id: str) -> str:
    """The content digest that names a course's one generated Lectures source."""
    return hashlib.sha256(f"lectures:{course_id}".encode()).hexdigest()


def compile_plan_dag(
    *, course_id: str, language: str, profile: dict[str, Any], constraints: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Blueprint, then every lecture, then the code-made block plan."""
    return [
        {
            "step_key": BLUEPRINT_STEP,
            "handler": "blueprint",
            "depends_on": [],
            "course_id": course_id,
            "language": language,
            "profile": profile,
            "constraints": constraints,
        },
        {
            "step_key": LECTURES_STEP,
            "handler": "lectures",
            "depends_on": [BLUEPRINT_STEP],
            "course_id": course_id,
            "language": language,
        },
        {
            "step_key": BLOCKPLAN_STEP,
            "handler": "blockplan",
            "depends_on": [BLUEPRINT_STEP, LECTURES_STEP],
            "course_id": course_id,
            "language": language,
        },
    ]


def compile_generation_dag(*, tree: dict[str, Any], course_id: str) -> list[dict[str, Any]]:
    """One exercise writer step per question block; teach blocks were written by code."""
    dag: list[dict[str, Any]] = []
    for module in tree.get("modules", []):
        for topic in module.get("topics", []):
            for block in topic.get("blocks", []):
                if str(block.get("role", "")) == "teach":
                    continue
                node_id = str(block.get("plan_node_id", "block"))
                dag.append(
                    {
                        "step_key": f"block_exercise:{node_id}",
                        "handler": "generate_block",
                        "depends_on": [],
                        "lecture_first": True,
                        "input_digest": input_digest_of(
                            {"kind": "block", "node": node_id, "course": course_id, "block": block}
                        ),
                    }
                )
    return dag


def paragraph_signals(paragraphs: list[str]) -> dict[str, bool]:
    """What the lecture's paragraphs hold: code, a procedure of steps, term-meaning pairs."""
    joined = "\n".join(paragraphs)
    code_lines = sum(len(m.group(1).strip().splitlines()) for m in _FENCE_RE.finditer(joined))
    return {
        "code": code_lines >= 2,
        "steps": len(_STEP_LINE_RE.findall(joined)) >= 3,
        "pairs": len(_PAIR_RE.findall(joined)) >= 3,
    }


def practice_type(paragraphs: list[str], position: int = 0) -> str:
    """The practice type by what the lecture holds: steps, pairs, code; else cloze or choice."""
    signals = paragraph_signals(paragraphs)
    if signals["steps"]:
        return "ordering"
    if signals["pairs"]:
        return "matching"
    if signals["code"]:
        return "cloze"
    return "cloze" if position % 2 else "choice"


def _brief(role: str, block_type: str, title: str) -> str:
    asks = {
        "check": f"Which statement about {title} is true?",
        "ordering": f"In what order do the steps of {title} go?",
        "matching": f"Which term of {title} goes with which meaning?",
        "cloze": f"Which token completes the code of {title}?",
        "choice": f"Which statement about {title} holds for the course case?",
    }
    if role == "teach":
        return f"Lecture: {title}"
    if role == "check":
        return asks["check"]
    return asks.get(block_type, asks["choice"])


def build_tree(
    blueprint: dict[str, Any],
    lectures: dict[str, dict[str, Any]],
    handles_by_topic: dict[str, list[str]],
) -> tuple[CourseTree, dict[str, dict[str, Any]]]:
    """The course tree code derives from the lectures, plus each topic's scenario row."""
    modules: list[ModuleNode] = []
    scenario: dict[str, dict[str, Any]] = {}
    test_types = list(BLOCK_TYPES["module_test"])
    for module in blueprint.get("modules", []):
        module_id = str(module.get("plan_node_id", ""))
        topics: list[TopicNode] = []
        module_handles: list[str] = []
        lessons: list[dict[str, Any]] = []
        for topic in module.get("topics", []):
            node = str(topic.get("plan_node_id", ""))
            title = str(topic.get("title", ""))
            handles = list(handles_by_topic.get(node, []))
            if not handles:
                continue
            module_handles += handles
            paragraphs = [str(p) for p in lectures.get(node, {}).get("paragraphs", [])]
            refs = [SourceRef(handle=h) for h in handles]
            practice = practice_type(paragraphs, len(lessons))
            blocks = [
                BlockSkeleton(
                    plan_node_id=f"{node}-b1",
                    role="teach",
                    block_type="reading",
                    brief=_brief("teach", "reading", title),
                    source_refs=refs,
                ),
                BlockSkeleton(
                    plan_node_id=f"{node}-b2",
                    role="check",
                    block_type="choice",
                    brief=_brief("check", "choice", title),
                    source_refs=refs,
                ),
                BlockSkeleton(
                    plan_node_id=f"{node}-b3",
                    role="practice",
                    block_type=practice,
                    brief=_brief("practice", practice, title),
                    source_refs=refs,
                ),
            ]
            topics.append(
                TopicNode(
                    plan_node_id=node,
                    kind="lesson",
                    title=title,
                    objectives=[str(topic.get("annotation", "")) or title],
                    through_line_note=str(topic.get("artefact_step", "")) or title,
                    blocks=blocks,
                )
            )
            lessons.append({**topic, "handles": handles, "practice": practice})
            scenario[node] = {
                "module": module_id,
                "title": title,
                "annotation": str(topic.get("annotation", "")),
                "introduces_terms": [str(t) for t in topic.get("introduces_terms", [])],
                "prepares_for": str(topic.get("artefact_step", "")),
                "requires": [str(r) for r in topic.get("requires", [])],
                "sections": [],
                "handles": handles,
            }
        if not topics:
            continue
        test_node = f"{module_id}-test"
        test_blocks: list[BlockSkeleton] = []
        for index, lesson in enumerate(lessons):
            block_type = test_types[index % len(test_types)]
            test_blocks.append(
                BlockSkeleton(
                    plan_node_id=f"{test_node}-b{index + 1}",
                    role="test",
                    block_type=block_type,
                    brief=_brief("test", block_type, str(lesson.get("title", ""))),
                    source_refs=[SourceRef(handle=h) for h in lesson["handles"]],
                )
            )
        topics.append(
            TopicNode(
                plan_node_id=test_node,
                kind="module_test",
                title=f"Module test: {module.get('title', '')}",
                objectives=[str(lesson.get("annotation", "")) or "test" for lesson in lessons],
                through_line_note=str(module.get("exit_knowledge", "")) or "module test",
                blocks=test_blocks,
            )
        )
        scenario[test_node] = {
            "module": module_id,
            "title": f"Module test: {module.get('title', '')}",
            "annotation": str(module.get("exit_knowledge", "")),
            "introduces_terms": [],
            "prepares_for": "",
            "requires": [str(lesson.get("plan_node_id", "")) for lesson in lessons],
            "sections": [],
            "handles": module_handles,
        }
        modules.append(
            ModuleNode(
                plan_node_id=module_id,
                title=str(module.get("title", "")),
                purpose=str(module.get("purpose", "")) or str(module.get("title", "")),
                entry_knowledge=str(module.get("entry_knowledge", "")) or "See the brief.",
                exit_knowledge=str(module.get("exit_knowledge", "")) or "See the brief.",
                topics=topics,
            )
        )
    tree = CourseTree(
        modules=modules,
        through_line=ThroughLine(
            case=str(blueprint.get("case", "")), learner=str(blueprint.get("learner", ""))
        ),
    )
    return tree, scenario
