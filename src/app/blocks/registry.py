"""Block-type registry: the seven T05 packages plus learner serializers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

BLOCK_TYPES: tuple[str, ...] = (
    "reading",
    "figure",
    "choice",
    "matching",
    "cloze",
    "ordering",
    "scenario_practice",
    "spot_the_error",
)


def learner_serializers() -> dict[str, Callable[[str, dict[str, Any], dict[str, Any]], Any]]:
    """Every learner-facing serializer: content in, answer key never out."""
    from importlib import import_module

    def _render(block_type: str, content: dict[str, Any], answer_key: dict[str, Any]) -> Any:
        _ = answer_key
        module = import_module(f"app.blocks.{block_type}.render")
        return module.render_for_learner(content)

    return {"card": _render}
