"""Prompt registry: one entry per agent, version pinned by content hash.

Day-1 holds no agents yet; the lifespan still calls
validate_registered_schemas so the wiring is live from the start.
Full agent entries and prompt files under prompts/ arrive with the
course pipeline.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from typing import Any


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


def registered() -> dict[tuple[str, str], PromptEntry]:
    """Snapshot of every registered prompt version."""
    return dict(_REGISTRY)


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
