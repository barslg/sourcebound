"""Prompt registry: one entry per agent, version pinned by content hash.

Day-1 holds no agents yet; the lifespan still calls
validate_registered_schemas so the wiring is live from the start.
Full agent entries and prompt files under prompts/ arrive with the
course pipeline.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from app.errors import ConfigurationError


@dataclass(frozen=True)
class PromptEntry:
    """One versioned prompt: stable prefix first for prefix caching."""

    agent: str
    name: str
    template: str
    response_schema: dict[str, Any] = field(default_factory=dict)
    version: str = ""
    cache_key: str = ""


_REGISTRY: dict[tuple[str, str], PromptEntry] = {}


def version_of(template: str) -> str:
    """Prompt version is the content hash: any edit invalidates the cache."""
    return hashlib.sha256(template.encode("utf-8")).hexdigest()[:16]


def register(entry: PromptEntry) -> PromptEntry:
    """Add one prompt version to the registry, stamping its content hash."""
    stamped = replace(entry, version=version_of(entry.template))
    _REGISTRY[(stamped.agent, stamped.name)] = stamped
    return stamped


def cache_key(agent: str, block_type: str, course_id: str) -> str:
    """Prefix-cache scope for one agent, block type, and course.

    The stable prompt prefix (system prompt, sources, plan, dossier) is
    shared across blocks of the same type in a course, so one cache entry
    serves them all; a new course or block type starts a new entry.
    """
    return f"{agent}:{block_type}:{course_id}"


# A prompt file must never pin a cache entry to one run: dates, uuids,
# and run ids belong in the dynamic part of the prompt, never in files.
_FORBIDDEN_PROMPT_PATTERNS = (
    re.compile(r"\b20\d{2}-\d{2}-\d{2}\b"),
    re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
    re.compile(r"\brun_[A-Za-z0-9_-]+\b"),
)


def validate_prompt_files(root: str | Path = "prompts") -> None:
    """Refuse startup when a prompt file contains a date, uuid, or run id."""
    base = Path(root)
    if not base.exists():
        return
    for path in sorted(base.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        for pattern in _FORBIDDEN_PROMPT_PATTERNS:
            if pattern.search(text):
                raise ConfigurationError(
                    f"prompt file {path} must not contain dates, uuids, or run ids"
                )


def validate_registered_schemas() -> None:
    """Validate every registered response schema against the strict subset.

    Raises app.llm.schema.SchemaViolation on the first invalid schema.
    With no agents registered yet this is a no-op by construction.
    """
    if not _REGISTRY:
        return
    from app.llm.schema import validate_strict_subset

    for entry in _REGISTRY.values():
        if entry.response_schema:
            validate_strict_subset(entry.response_schema, name=entry.name)
