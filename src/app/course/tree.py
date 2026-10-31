"""The course tree contract: one Pydantic model plus strict JSON schemas.

The tree (profile -> modules -> topics -> block skeletons) is the
contract the architect writes, the author edits, the generators read,
and the reader checks. Code owns the structure; the model fills plain
text. Every generator and reader input is validated here before use.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import BaseModel, Field

ROLES = ("teach", "example", "check", "practice", "test")
QUESTION_ROLES = frozenset({"check", "practice", "test"})
TOPIC_KINDS = ("lesson", "module_test")
TS_CONFIGS = {"en": "english", "nb": "norwegian", "uk": "simple"}
# Block-type registry per topic kind (item 14): block_type is not free
# text. Lessons teach with readings/figures/examples and check with
# exercises; module tests only assess.
BLOCK_TYPES: dict[str, tuple[str, ...]] = {
    "lesson": (
        "reading",
        "figure",
        "worked_example",
        "definition",
        "choice",
        "matching",
        "cloze",
        "scenario_practice",
        "numeric",
        "ordering",
    ),
    "module_test": (
        "choice",
        "matching",
        "cloze",
        "scenario_practice",
        "spot_the_error",
        "numeric",
        "ordering",
    ),
}
KNOWN_BLOCK_TYPES = frozenset(
    block_type for allowed in BLOCK_TYPES.values() for block_type in allowed
)


class SourceRef(BaseModel):
    """One block-to-passage link: passage handle at plan time."""

    handle: str = Field(pattern=r"^P[1-9][0-9]*$")


class BlockSkeleton(BaseModel):
    """One block before generation: role, topic-without-answer, refs."""

    plan_node_id: str = Field(min_length=1)
    role: str = Field(pattern="^(teach|example|check|practice|test)$")
    block_type: str = Field(min_length=1)
    brief: str = Field(min_length=1, max_length=500)
    source_refs: list[SourceRef] = Field(min_length=1)


class TopicNode(BaseModel):
    """One topic: objectives plus the through-line handoff."""

    plan_node_id: str = Field(min_length=1)
    kind: str = Field(pattern="^(lesson|module_test)$")
    title: str = Field(min_length=1)
    objectives: list[str] = Field(min_length=1)
    through_line_note: str = Field(min_length=1)
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
    """The course-level through-line: one case, per-topic handoffs."""

    case: str = ""
    handoffs: list[ThroughLineHandoff] = Field(default_factory=list)


class CourseTree(BaseModel):
    """The whole plan: modules in order."""

    modules: list[ModuleNode] = Field(min_length=1)
    through_line: ThroughLine | None = None


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
            elif Counter(block.role for block in topic.blocks) != Counter(expected):
                errors.append(
                    f"topic {topic.plan_node_id}: roles "
                    f"{sorted(block.role for block in topic.blocks)} do not match "
                    f"prescription {sorted(expected)} for kind {topic.kind}"
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
    node_ids = [module.plan_node_id for module in tree.modules]
    node_ids += [topic.plan_node_id for module in tree.modules for topic in module.topics]
    if len(set(node_ids)) != len(node_ids):
        errors.append("duplicate module/topic plan_node_id")
    return errors


def _answer_strings(answer_key: Any) -> list[str]:
    """Every string leaf of an answer key (server-side, any shape)."""
    found: list[str] = []
    if isinstance(answer_key, str):
        found.append(answer_key)
    elif isinstance(answer_key, dict):
        for value in answer_key.values():
            found.extend(_answer_strings(value))
    elif isinstance(answer_key, list):
        for value in answer_key:
            found.extend(_answer_strings(value))
    return found


def _normalize(text: str) -> str:
    import re as _re
    import unicodedata as _ud

    folded = _ud.normalize("NFKC", text.lower())
    return " ".join(_re.sub(r"[^0-9a-zа-яёіїєґæøå]+", " ", folded).split())


def brief_contains_answer(brief: str, answer_key: Any) -> bool:
    """True when a block brief leaks its answer key (generation-time check).

    A brief must be a topic, never the answer: any substantial answer
    string appearing verbatim in the brief — or all of its content
    words showing up there — fails the block. Short fragments (under
    4 chars, fewer than 3 content words) never count, so ordinary
    vocabulary overlap does not false-positive.
    """
    haystack = _normalize(brief)
    hay_words = set(haystack.split())
    for raw in _answer_strings(answer_key):
        needle = _normalize(raw)
        if len(needle) < 4:
            continue
        if needle in haystack:
            return True
        content = [word for word in needle.split() if len(word) > 3]
        if len(content) >= 3 and all(word in hay_words for word in content):
            return True
    return False


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


RESEARCHER_SCHEMA: dict[str, Any] = _strict_object(
    {
        "query": _string(),
        "rephrased": {"type": "array", "items": _string()},
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
        "web_candidates": {
            "type": "array",
            "items": _strict_object(
                {
                    "url": _string(),
                    "title": _string(),
                    "why": _string(),
                }
            ),
        },
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
                                "objectives": {"type": "array", "items": _string()},
                                "through_line_note": _string(),
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
                                            "block_type": _string(),
                                            "brief": _string(),
                                            "source_refs": {
                                                "type": "array",
                                                "items": _strict_object({"handle": _string()}),
                                            },
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

GENERATOR_SCHEMA: dict[str, Any] = _strict_object(
    {
        "block_type": _string(),
        "text": _string(),
        "citations": {
            "type": "array",
            "items": _strict_object({"passage_id": _string(), "quote": _string()}),
        },
        "answer": _string(),
    }
)

READER_SCHEMA: dict[str, Any] = _strict_object(
    {
        "verdicts": {
            "type": "array",
            "items": _strict_object(
                {
                    "plan_node_id": _string(),
                    "verdict": {
                        "type": "string",
                        "enum": ["pass", "hold", "unknown"],
                    },
                    "items": {
                        "type": "array",
                        "items": _strict_object(
                            {
                                "item": _string(),
                                "status": _string(),
                                "evidence": _string(),
                            }
                        ),
                    },
                }
            ),
        }
    }
)

SABOTEUR_SCHEMA: dict[str, Any] = _strict_object(
    {
        "sentence": _string(),
        "kind": _string(),
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

CONTINUITY_SCHEMA: dict[str, Any] = _strict_object(
    {
        "handoffs": {
            "type": "array",
            "items": _strict_object({"topic_plan_node_id": _string(), "note": _string()}),
        },
        "issues": {"type": "array", "items": _string()},
    }
)
