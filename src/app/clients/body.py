"""Request-body builder choice: one digest shape per model's provider.

The reply store and the replay ledger hash the exact request body, so the
provider and the replay client must build it identically. Callers:
providers/llm.py and clients/replay.py.
"""

from __future__ import annotations

from typing import Any

from app.clients.anthropic import build_anthropic_body
from app.clients.meta import build_request_body
from app.llm.registry import get_model


def build_body(*, model: str, **options: Any) -> dict[str, Any]:
    """The request body of the provider that serves `model`."""
    if get_model(model).provider == "anthropic":
        return build_anthropic_body(model=model, **options)
    return build_request_body(model=model, **options)
