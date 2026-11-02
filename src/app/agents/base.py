"""Agents: one JSON-in/JSON-out module per pipeline role.

Each agent loads its system prompt from prompts/<agent>/system.md
(role, rules, output contract, refusal cases); the dynamic part is
assembled by code from typed inputs, never free text. Schemas are
strict-subset validated at startup. No clocks, uuids, or randomness
here: ids and digests are minted by the runs layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.course import tree
from app.errors import ConfigurationError
from app.llm import prompts as prompt_registry
from app.llm.schema import validate_strict_subset


def _default_prompt_root() -> Path:
    """Absolute prompt dir from the installed package location.

    The old relative `prompts/` broke every start outside the repo
    root (notably the Docker image); resolving from this file keeps
    one layout in dev, tests, and the image.
    """
    return Path(__file__).resolve().parents[3] / "prompts"


PROMPT_ROOT = _default_prompt_root()

AGENT_SCHEMAS: dict[str, dict[str, object]] = {
    "researcher": tree.RESEARCHER_SCHEMA,
    "architect": tree.ARCHITECT_SCHEMA,
    "outline": tree.OUTLINE_SCHEMA,
    "librarian": tree.LIBRARIAN_SCHEMA,
    "block_teach": tree.GENERATOR_SCHEMA,
    "block_example": tree.GENERATOR_SCHEMA,
    "block_check": tree.GENERATOR_SCHEMA,
    "block_practice": tree.GENERATOR_SCHEMA,
    "block_test": tree.GENERATOR_SCHEMA,
    "reader": tree.READER_SCHEMA,
    "saboteur": tree.SABOTEUR_SCHEMA,
    "continuity": tree.CONTINUITY_SCHEMA,
    "tutor": tree.TUTOR_SCHEMA,
    "translator": tree.TRANSLATOR_SCHEMA,
}


def system_prompt(agent: str, root: Path | str | None = None) -> str:
    """Read the versioned system prompt file for one agent (default: PROMPT_ROOT now)."""
    path = Path(PROMPT_ROOT if root is None else root) / agent / "system.md"
    if not path.exists():
        raise ConfigurationError(f"missing system prompt: {path}")
    return path.read_text(encoding="utf-8")


def prompt_version(agent: str, root: Path | str | None = None) -> str:
    """Content hash of the agent's system prompt file."""
    return prompt_registry.version_of(f"{agent}:{system_prompt(agent, root)}")


def assemble(dynamic: str) -> str:
    """The typed dynamic part of a call; the system prompt travels as `instructions`.

    The provider sends prompts/<agent>/system.md as the request's
    instructions (the stable, cached prefix) and this text as the input,
    so a step digest changes only when the typed input does.
    """
    return f"## Task input (typed, assembled by code)\n{dynamic}"


# Keys code reads from each agent reply; the schema must require them all.
CODE_REQUIRED: dict[str, set[str]] = {
    "researcher": {"query", "rephrased", "ranked", "web_candidates"},
    "architect": {"modules", "estimate_inputs", "through_line"},
    "outline": {"case", "modules", "uncovered"},
    "librarian": {"container", "fields", "address_rule", "pagination"},
    "block_teach": {"block_type", "text", "citations", "answer"},
    "block_example": {"block_type", "text", "citations", "answer"},
    "block_check": {"block_type", "text", "citations", "answer"},
    "block_practice": {"block_type", "text", "citations", "answer"},
    "block_test": {"block_type", "text", "citations", "answer"},
    "reader": {"verdicts"},
    "saboteur": {"sentence", "kind", "mutated"},
    "translator": {"segments"},
    "continuity": {"handoffs", "issues"},
    "tutor": {"quotes"},
}


def _check_nested_required(schema: Any, *, agent: str, path: str = "") -> None:
    """Every object node's properties must all be required (nested check)."""
    if isinstance(schema, dict):
        props = schema.get("properties")
        if isinstance(props, dict):
            req = schema.get("required", [])
            missing = set(props) - (set(req) if isinstance(req, list) else set())
            if missing:
                raise ConfigurationError(
                    f"agent {agent}: schema at {path or '$'} misses required {sorted(missing)}"
                )
            for key, sub in props.items():
                _check_nested_required(sub, agent=agent, path=f"{path}.{key}")
        items = schema.get("items")
        if isinstance(items, dict):
            _check_nested_required(items, agent=agent, path=f"{path}[]")
    elif isinstance(schema, list):
        for sub in schema:
            _check_nested_required(sub, agent=agent, path=path)


def _check_enums() -> None:
    """Closed vocabularies in schemas must equal the registries (no drift)."""
    from app.blocks.registry import BLOCK_TYPES
    from app.course import tree as _tree
    from app.course.errorspot import MUTATION_KINDS

    architect_blocks = _tree.ARCHITECT_SCHEMA["properties"]["modules"]["items"]["properties"][
        "topics"
    ]["items"]["properties"]["blocks"]["items"]["properties"]
    if architect_blocks["block_type"].get("enum") != sorted(BLOCK_TYPES):
        raise ConfigurationError("architect block_type enum drifted from the block registry")
    if architect_blocks["role"].get("enum") != list(_tree.ROLES):
        raise ConfigurationError("architect role enum drifted from ROLES")
    if _tree.SABOTEUR_SCHEMA["properties"]["kind"].get("enum") != sorted(MUTATION_KINDS):
        raise ConfigurationError("saboteur kind enum drifted from MUTATION_KINDS")
    for block_type, schema in _tree.BLOCK_GENERATOR_SCHEMAS.items():
        if block_type not in BLOCK_TYPES:
            raise ConfigurationError(f"generator schema for unregistered type {block_type}")
        validate_strict_subset(schema, name=f"generator:{block_type}")
        _check_nested_required(schema, agent=f"generator:{block_type}")


def validate_agents(root: Path | str | None = None) -> None:
    """Startup check: prompts, strict schemas, code-required keys subset."""
    for agent, required in CODE_REQUIRED.items():
        schema = AGENT_SCHEMAS.get(agent, {})
        req = schema.get("required", [])
        missing = required - (set(req) if isinstance(req, list) else set())
        if missing:
            raise ConfigurationError(
                f"agent {agent}: schema misses code-required {sorted(missing)}"
            )
    _check_enums()
    for agent, schema in AGENT_SCHEMAS.items():
        system_prompt(agent, root)
        validate_strict_subset(schema, name=agent)
        _check_nested_required(schema, agent=agent)
        prompt_registry.register(
            prompt_registry.PromptEntry(
                agent=agent,
                name="system",
                template=system_prompt(agent, root),
                response_schema=dict(schema),
            )
        )
