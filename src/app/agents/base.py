"""Agents: one JSON-in/JSON-out module per pipeline role.

Each agent loads its system prompt from prompts/<agent>/system.md
(role, rules, output contract, refusal cases); the dynamic part is
assembled by code from typed inputs, never free text. Schemas are
strict-subset validated at startup. No clocks, uuids, or randomness
here: ids and digests are minted by the runs layer.
"""

from __future__ import annotations

from pathlib import Path

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
    "librarian": tree.LIBRARIAN_SCHEMA,
    "block_teach": tree.GENERATOR_SCHEMA,
    "block_example": tree.GENERATOR_SCHEMA,
    "block_check": tree.GENERATOR_SCHEMA,
    "block_practice": tree.GENERATOR_SCHEMA,
    "block_test": tree.GENERATOR_SCHEMA,
    "reader": tree.READER_SCHEMA,
    "saboteur": tree.SABOTEUR_SCHEMA,
    "continuity": tree.CONTINUITY_SCHEMA,
    "translator": tree.TRANSLATOR_SCHEMA,
}


def system_prompt(agent: str, root: Path | str = PROMPT_ROOT) -> str:
    """Read the versioned system prompt file for one agent."""
    path = Path(root) / agent / "system.md"
    if not path.exists():
        raise ConfigurationError(f"missing system prompt: {path}")
    return path.read_text(encoding="utf-8")


def prompt_version(agent: str, root: Path | str = PROMPT_ROOT) -> str:
    """Content hash of the agent's system prompt file."""
    return prompt_registry.version_of(f"{agent}:{system_prompt(agent, root)}")


def assemble(system: str, dynamic: str) -> str:
    """Stable system prefix first (prefix caching), typed dynamic part last."""
    return f"{system.rstrip()}\n\n## Task input (typed, assembled by code)\n{dynamic}"


# Keys code reads from each agent reply; the schema must require them all.
CODE_REQUIRED: dict[str, set[str]] = {
    "reader": {"verdicts"},
    "saboteur": {"sentence", "kind", "mutated"},
    "translator": {"segments"},
    "continuity": {"handoffs", "issues"},
}


def validate_agents(root: Path | str = PROMPT_ROOT) -> None:
    """Startup check: prompts, strict schemas, code-required keys subset."""
    for agent, required in CODE_REQUIRED.items():
        schema = AGENT_SCHEMAS.get(agent, {})
        req = schema.get("required", [])
        missing = required - (set(req) if isinstance(req, list) else set())
        if missing:
            raise ConfigurationError(
                f"agent {agent}: schema misses code-required {sorted(missing)}"
            )
    for agent, schema in AGENT_SCHEMAS.items():
        system_prompt(agent, root)
        validate_strict_subset(schema, name=agent)
        prompt_registry.register(
            prompt_registry.PromptEntry(
                agent=agent,
                name="system",
                template=system_prompt(agent, root),
                response_schema=dict(schema),
            )
        )
