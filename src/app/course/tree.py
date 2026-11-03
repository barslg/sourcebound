"""The course tree contract: one Pydantic model plus strict JSON schemas.

The tree (profile -> modules -> topics -> block skeletons) is the
contract the outline and the architect write, the author edits, the
generators read, and the reader checks. Code owns the structure; the
model fills plain text. Every generator and reader input is validated
here before use.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.blocks.registry import BLOCK_TYPES as REGISTRY_BLOCK_TYPES
from app.course.errorspot import MUTATION_KINDS

ROLES = ("teach", "example", "check", "practice", "test")
QUESTION_ROLES = frozenset({"check", "practice", "test"})
READING_ROLES = frozenset({"teach", "example"})
TOPIC_KINDS = ("lesson", "module_test")
TS_CONFIGS = {"en": "english", "nb": "norwegian", "uk": "simple"}
# Block-type registry per topic kind (item 14): block_type is not free
# text. Lessons teach with readings and examples and check with
# exercises (a flow passage may be shown as a diagram the browser
# draws from labelled nodes); module tests only assess. A figure is never planned: the
# 2026-11-02 big course drew invented numbers in 18 of 18 figures, and
# the type stays registered only to render courses that already hold one.
BLOCK_TYPES: dict[str, tuple[str, ...]] = {
    "lesson": (
        "reading",
        "diagram",
        "choice",
        "matching",
        "cloze",
        "ordering",
        "scenario_practice",
    ),
    "module_test": (
        "choice",
        "matching",
        "cloze",
        "ordering",
        "scenario_practice",
        "spot_the_error",
    ),
}
KNOWN_BLOCK_TYPES = frozenset(
    block_type for allowed in BLOCK_TYPES.values() for block_type in allowed
)


class SourceRef(BaseModel):
    """One block-to-passage link: passage handle at plan time."""

    handle: str = Field(pattern=r"^P[1-9][0-9]*$")


class ExampleSpec(BaseModel):
    """What a worked example shows on the course case: input, action, result."""

    input: str = ""
    action: str = ""
    result: str = ""

    def complete(self) -> bool:
        return bool(self.input.strip() and self.action.strip() and self.result.strip())


class BlockSkeleton(BaseModel):
    """One block before generation: role, topic-without-answer, refs."""

    plan_node_id: str = Field(min_length=1)
    role: str = Field(pattern="^(teach|example|check|practice|test)$")
    block_type: str = Field(min_length=1)
    brief: str = Field(min_length=1, max_length=500)
    source_refs: list[SourceRef] = Field(min_length=1)
    example_spec: ExampleSpec = Field(default_factory=ExampleSpec)


class TopicNode(BaseModel):
    """One topic: objectives, the misconception its checks target, the handoff."""

    plan_node_id: str = Field(min_length=1)
    kind: str = Field(pattern="^(lesson|module_test)$")
    title: str = Field(min_length=1)
    objectives: list[str] = Field(min_length=1)
    through_line_note: str = Field(min_length=1)
    misconception: str = ""
    blocks: list[BlockSkeleton] = Field(min_length=1)


class ModuleNode(BaseModel):
    """One module: purpose with entry/exit knowledge."""

    plan_node_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    entry_knowledge: str = Field(min_length=1)
    exit_knowledge: str = Field(min_length=1)
    topics: list[TopicNode] = Field(min_length=1)


class ThroughLineHandoff(BaseModel):
    """One per-topic handoff of the course-level case."""

    topic_plan_node_id: str = Field(min_length=1)
    note: str = Field(min_length=1)


class ThroughLine(BaseModel):
    """The course-level through-line: the learner, one case, per-topic handoffs."""

    case: str = ""
    learner: str = ""
    handoffs: list[ThroughLineHandoff] = Field(default_factory=list)


class CourseTree(BaseModel):
    """The whole plan: modules in order."""

    modules: list[ModuleNode] = Field(min_length=1)
    through_line: ThroughLine | None = None


class UncoveredPassage(BaseModel):
    """One content passage the plan deliberately leaves out, with why."""

    handle: str = Field(pattern=r"^P[1-9][0-9]*$")
    reason: str = Field(min_length=1)


class EstimateInputs(BaseModel):
    """Architect-declared generation size for the forecast."""

    blocks: int = Field(ge=1)
    calls: int = Field(ge=1)
    tokens: int = Field(ge=1)


class ArchitectOutput(BaseModel):
    """Validated architect reply: tree plus estimate inputs."""

    modules: list[ModuleNode] = Field(min_length=1)
    estimate_inputs: EstimateInputs
    through_line: ThroughLine | None = None
    uncovered: list[UncoveredPassage] = Field(default_factory=list)


class ScenarioTopic(BaseModel):
    """One topic of the scenario (planning layer 1): what it covers, in two sentences."""

    plan_node_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    annotation: str = Field(min_length=1)
    introduces_terms: list[str] = Field(default_factory=list)
    prepares_for: str = ""
    requires: list[str] = Field(default_factory=list)
    sections: list[str] = Field(min_length=1)


class ScenarioModule(BaseModel):
    """One module of the scenario with its sections and annotated topics."""

    plan_node_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    entry_knowledge: str = Field(min_length=1)
    exit_knowledge: str = Field(min_length=1)
    sections: list[str] = Field(min_length=1)
    topics: list[ScenarioTopic] = Field(min_length=1)


class AuthorQuestion(BaseModel):
    """A question whose answer changes the course; generation waits for it."""

    key: str = Field(min_length=1)
    question: str = Field(min_length=1)


class ScenarioOutput(BaseModel):
    """Validated outline reply: the learner, the case, annotated topics, leftovers, questions."""

    learner: str = Field(min_length=1)
    case: str = Field(min_length=1)
    modules: list[ScenarioModule] = Field(min_length=1)
    uncovered: list[dict[str, str]] = Field(default_factory=list)
    questions: list[AuthorQuestion] = Field(default_factory=list)


class ConstraintRow(BaseModel):
    """One typed profile constraint; never a free bullet."""

    kind: str = Field(min_length=1)
    key: str = Field(min_length=1)
    value: dict[str, Any] = Field(default_factory=dict)
    required: bool = False
    question: str | None = None
    answer: dict[str, Any] | None = None


def validate_tree(tree: CourseTree, prescription: dict[str, list[str]]) -> list[str]:
    """Check tree invariants in code; return blocking error strings."""
    errors: list[str] = []
    seen: set[str] = set()
    for module in tree.modules:
        if not module.purpose.strip():
            errors.append(f"module {module.plan_node_id}: purpose is empty")
        for topic in module.topics:
            expected = prescription.get(topic.kind)
            if expected is None:
                errors.append(f"topic {topic.plan_node_id}: no prescription for kind {topic.kind}")
            elif set(block.role for block in topic.blocks) != set(expected):
                # Every prescribed role once or more, no role outside the prescription:
                # a module test with three questions or a lesson with two practice
                # blocks is a valid plan, not a rejection.
                errors.append(
                    f"topic {topic.plan_node_id}: roles "
                    f"{sorted(set(block.role for block in topic.blocks))} do not cover "
                    f"prescription {sorted(set(expected))} for kind {topic.kind}"
                )
            allowed = BLOCK_TYPES.get(topic.kind)
            if allowed is None:
                errors.append(f"topic {topic.plan_node_id}: unknown topic kind {topic.kind}")
            for block in topic.blocks:
                if block.plan_node_id in seen:
                    errors.append(f"duplicate plan_node_id {block.plan_node_id}")
                seen.add(block.plan_node_id)
                if block.block_type not in KNOWN_BLOCK_TYPES:
                    errors.append(
                        f"block {block.plan_node_id}: unknown block type {block.block_type!r}"
                    )
                elif allowed is not None and block.block_type not in allowed:
                    errors.append(
                        f"block {block.plan_node_id}: block type {block.block_type!r}"
                        f" is not allowed for topic kind {topic.kind}"
                    )
                if not block.source_refs:
                    errors.append(f"block {block.plan_node_id}: needs at least one source ref")
                asks = block.brief.strip().endswith("?")
                if block.role in QUESTION_ROLES and not asks:
                    errors.append(
                        f"block {block.plan_node_id}: {block.role} brief must ask,"
                        " never contain the answer"
                    )
                if block.role not in QUESTION_ROLES and asks:
                    errors.append(
                        f"block {block.plan_node_id}: {block.role} brief is a topic, not a question"
                    )
                if block.role in READING_ROLES and block.block_type not in ("reading", "diagram"):
                    # the 2026-11-02 Muse API plan typed 24 of 30 examples as exercises
                    errors.append(
                        f"block {block.plan_node_id}: a {block.role} block is a reading,"
                        f" not {block.block_type!r}"
                    )
        errors += _variety_errors(module)
    node_ids = [module.plan_node_id for module in tree.modules]
    node_ids += [topic.plan_node_id for module in tree.modules for topic in module.topics]
    if len(set(node_ids)) != len(node_ids):
        errors.append("duplicate module/topic plan_node_id")
    return errors


TYPE_BY_PASSAGE = (
    "matching for pairs, ordering for a procedure's steps, cloze for a command"
    " or a request's fields, choice for a decision, scenario_practice for cases to classify"
)


def _variety_errors(module: ModuleNode) -> list[str]:
    """Exercise types follow the passages, never one habit; the test covers every lesson.

    The 2026-11-03 Meta API course (direct instruction) planned 54 exercises
    in two types and one-question module tests; matching and cloze stayed
    registered and unused. A module's exercises use more than one type
    once there are three of them, its test has at least one block per
    lesson, and a test block type never repeats while an allowed type
    is still unused.
    """
    errors: list[str] = []
    lessons = [topic for topic in module.topics if topic.kind == "lesson"]
    exercises = [
        block for topic in lessons for block in topic.blocks if block.role in QUESTION_ROLES
    ]
    types = {block.block_type for block in exercises}
    if len(exercises) >= 3 and len(types) == 1:
        errors.append(
            f"module {module.plan_node_id}: all {len(exercises)} exercises are"
            f" {exercises[0].block_type!r}; choose each type by what its passages hold"
            f" ({TYPE_BY_PASSAGE})"
        )
    for topic in module.topics:
        if topic.kind != "module_test":
            continue
        if lessons and len(topic.blocks) < len(lessons):
            errors.append(
                f"topic {topic.plan_node_id}: {len(topic.blocks)} test block for"
                f" {len(lessons)} lessons; one test block per lesson, on that lesson's objective"
            )
        allowed = set(BLOCK_TYPES["module_test"])
        seen: set[str] = set()
        for block in topic.blocks:
            if block.block_type in seen and not (allowed <= seen):
                errors.append(
                    f"topic {topic.plan_node_id}: the test repeats {block.block_type!r}"
                    f" while {sorted(allowed - seen)} are unused ({TYPE_BY_PASSAGE})"
                )
                break
            seen.add(block.block_type)
    return errors


def _strict_object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": sorted(properties),
        "additionalProperties": False,
    }


def _string(description: str = "") -> dict[str, Any]:
    node: dict[str, Any] = {"type": "string"}
    if description:
        node["description"] = description
    return node


def _strings(description: str = "") -> dict[str, Any]:
    node: dict[str, Any] = {"type": "array", "items": _string()}
    if description:
        node["description"] = description
    return node


RESEARCHER_SCHEMA: dict[str, Any] = _strict_object(
    {
        "query": _string(),
        "rephrased": _strings(
            "One rephrasing per source language, in that language, plus any keyword fold."
        ),
        "ranked": {
            "type": "array",
            "items": _strict_object(
                {
                    "handle": _string(),
                    "rank": {"type": "integer"},
                    "score": {"type": "number"},
                }
            ),
        },
        "gaps": _strings("What the query asks that no given passage answers; empty when covered."),
    }
)

_EXAMPLE_SPEC = _strict_object(
    {
        "input": _string("The state of the case before the step; empty unless role is example."),
        "action": _string("What is done, from the taught passages; empty unless role is example."),
        "result": _string("What the learner sees afterwards; empty unless role is example."),
    }
)

ARCHITECT_SCHEMA: dict[str, Any] = _strict_object(
    {
        "modules": {
            "type": "array",
            "items": _strict_object(
                {
                    "plan_node_id": _string(),
                    "title": _string(),
                    "purpose": _string(),
                    "entry_knowledge": _string(),
                    "exit_knowledge": _string(),
                    "topics": {
                        "type": "array",
                        "items": _strict_object(
                            {
                                "plan_node_id": _string(),
                                "kind": {"type": "string", "enum": ["lesson", "module_test"]},
                                "title": _string(),
                                "objectives": _strings(),
                                "through_line_note": _string(),
                                "misconception": _string(
                                    "What a learner at the stated level typically gets wrong"
                                    " here; one sentence, drawn from the passages."
                                ),
                                "blocks": {
                                    "type": "array",
                                    "items": _strict_object(
                                        {
                                            "plan_node_id": _string(),
                                            "role": {
                                                "type": "string",
                                                "enum": [
                                                    "teach",
                                                    "example",
                                                    "check",
                                                    "practice",
                                                    "test",
                                                ],
                                            },
                                            "block_type": {
                                                "type": "string",
                                                "enum": sorted(REGISTRY_BLOCK_TYPES),
                                            },
                                            "brief": _string(),
                                            "source_refs": {
                                                "type": "array",
                                                "items": _strict_object({"handle": _string()}),
                                            },
                                            "example_spec": _EXAMPLE_SPEC,
                                        }
                                    ),
                                },
                            }
                        ),
                    },
                }
            ),
        },
        "estimate_inputs": _strict_object(
            {
                "blocks": {"type": "integer"},
                "calls": {"type": "integer"},
                "tokens": {"type": "integer"},
            }
        ),
        "through_line": _strict_object(
            {
                "case": _string(),
                "handoffs": {
                    "type": "array",
                    "items": _strict_object({"topic_plan_node_id": _string(), "note": _string()}),
                },
            }
        ),
        "uncovered": {
            "type": "array",
            "items": _strict_object({"handle": _string(), "reason": _string()}),
        },
    }
)

OUTLINE_SCHEMA: dict[str, Any] = _strict_object(
    {
        "learner": _string(
            "Who learns and where they stand now: the profile's audience as one concrete person."
        ),
        "case": _string("The one thing the course builds, step by step, from the first topic on."),
        "modules": {
            "type": "array",
            "items": _strict_object(
                {
                    "plan_node_id": _string(),
                    "title": _string(),
                    "purpose": _string(),
                    "entry_knowledge": _string(),
                    "exit_knowledge": _string(),
                    "sections": _strings("Section ids (S<n>) this module is built from."),
                    "topics": {
                        "type": "array",
                        "items": _strict_object(
                            {
                                "plan_node_id": _string(),
                                "title": _string(),
                                "annotation": _string(
                                    "One or two sentences: what this topic teaches and how"
                                    " the case advances."
                                ),
                                "introduces_terms": _strings(
                                    "Terms first explained here, as the sources write them."
                                ),
                                "prepares_for": _string(
                                    "What the next topic will need from this one."
                                ),
                                "requires": _strings(
                                    "plan_node_ids of earlier topics this one builds on."
                                ),
                                "sections": _strings(
                                    "Section ids of this module that this topic covers."
                                ),
                            }
                        ),
                    },
                }
            ),
        },
        "uncovered": {
            "type": "array",
            "items": _strict_object({"section": _string(), "reason": _string()}),
        },
        "questions": {
            "type": "array",
            "items": _strict_object(
                {
                    "key": _string("snake_case id of the question"),
                    "question": _string(
                        "A question to the author whose answer changes the course"
                        " (version, environment, main case); empty list when none."
                    ),
                }
            ),
        },
    }
)

LIBRARIAN_SCHEMA: dict[str, Any] = _strict_object(
    {
        "container": _string(),
        "fields": _strict_object({"title": _string(), "body": _string()}),
        "address_rule": _strict_object(
            {
                "kind": {"type": "string", "enum": ["heading_path", "anchor_id"]},
                "selector": _string(),
            }
        ),
        "pagination": _strict_object(
            {
                "next_selector": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "description": "Next-page link selector, or null when single page.",
                },
                "max_pages": {"type": "integer"},
            }
        ),
    }
)

_CITATIONS = {
    "type": "array",
    "items": _strict_object({"passage_id": _string(), "quote": _string()}),
}
_LEFTOVERS = {
    "type": "array",
    "items": _strict_object(
        {
            "handle": _string(),
            "reason": _string("Why this assigned passage is not drawn on in this block."),
        }
    ),
}
_HANDOFF = _string(
    "After this block, what the learner now knows: the terms as the block named them and"
    " the state of the case. Two or three sentences for teach and example blocks; empty"
    " for exercises."
)


def _generator(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return _strict_object(
        {
            "block_type": {"type": "string", "enum": sorted(REGISTRY_BLOCK_TYPES)},
            "text": _string(),
            "citations": _CITATIONS,
            "answer": _string(),
            "handoff": _HANDOFF,
            "leftovers": _LEFTOVERS,
            **(extra or {}),
        }
    )


GENERATOR_SCHEMA: dict[str, Any] = _generator()

BLOCK_GENERATOR_SCHEMAS: dict[str, dict[str, Any]] = {
    "reading": _generator(),
    "figure": _generator({"numbers": {"type": "array", "items": {"type": "number"}}}),
    "diagram": _generator(
        {
            "nodes": {
                "type": "array",
                "items": _strict_object({"id": _string(), "label": _string()}),
            },
            "edges": {
                "type": "array",
                "items": _strict_object({"from": _string(), "to": _string(), "label": _string()}),
            },
        }
    ),
    "choice": _generator(
        {
            "options": {
                "type": "array",
                "items": _strict_object({"text": _string(), "correct": {"type": "boolean"}}),
            }
        }
    ),
    "matching": _generator(
        {
            "pairs": {
                "type": "array",
                "items": _strict_object({"left": _string(), "right": _string()}),
            }
        }
    ),
    "cloze": _generator({"blanks": _strings()}),
    "ordering": _generator(
        {"steps": _strings("The steps in their correct order; code shuffles them for the learner.")}
    ),
    "scenario_practice": _generator(
        {
            "items": {
                "type": "array",
                "items": _strict_object(
                    {"text": _string(), "passage_id": _string(), "tier": _string()}
                ),
            }
        }
    ),
    "spot_the_error": _generator({"sentences": _strings()}),
}


def _rubric_item() -> dict[str, Any]:
    return _strict_object(
        {
            "verdict": {"type": "string", "enum": ["pass", "hold", "unknown"]},
            "evidence": _string(
                "A verbatim span of the block text or of a cited passage that decides the item."
            ),
            "note": _string(
                "For a hold, one sentence naming what the writer must change; empty for a pass."
            ),
        }
    )


READER_SCHEMA: dict[str, Any] = _strict_object(
    {
        "verdicts": {
            "type": "array",
            "items": _strict_object(
                {
                    "plan_node_id": _string(),
                    "verdict": {"type": "string", "enum": ["pass", "hold", "unknown"]},
                    "items": _strict_object(
                        {
                            "task_fit": _rubric_item(),
                            "grounding": _rubric_item(),
                            "continuity": _rubric_item(),
                            "answer_key": _rubric_item(),
                            "audience_language": _rubric_item(),
                            "planted_error": _rubric_item(),
                            "coverage": _rubric_item(),
                        }
                    ),
                }
            ),
        }
    }
)

SABOTEUR_SCHEMA: dict[str, Any] = _strict_object(
    {
        "sentence": _string(),
        "kind": {"type": "string", "enum": sorted(MUTATION_KINDS)},
        "mutated": _string(),
    }
)

TRANSLATOR_SCHEMA: dict[str, Any] = _strict_object(
    {
        "segments": {
            "type": "array",
            "items": _strict_object({"id": _string(), "text": _string()}),
        },
    }
)

GLOSSARY_SCHEMA: dict[str, Any] = TRANSLATOR_SCHEMA

TUTOR_SCHEMA: dict[str, Any] = _strict_object(
    {
        "quotes": {
            "type": "array",
            "items": _strict_object({"handle": _string(), "quote": _string()}),
        },
    }
)
