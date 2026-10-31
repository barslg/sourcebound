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

PROMPT_ROOT = Path("prompts")

AGENT_SCHEMAS: dict[str, dict[str, object]] = {
    "researcher": tree.RESEARCHER_SCHEMA,
    "architect": tree.ARCHITECT_SCHEMA,
    "librarian": tree.LIBRARIAN_SCHEMA,
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


def validate_agents(root: Path | str = PROMPT_ROOT) -> None:
    """Startup check: every agent has a prompt file and a strict schema."""
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
