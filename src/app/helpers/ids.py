"""One UUID parser for path, query and body ids: malformed means 404, never 500."""

from __future__ import annotations

from uuid import UUID

from app.errors import NotFoundError


def parse_uuid_or_404(value: str) -> UUID:
    """Parse one id; a malformed value answers 404."""
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise NotFoundError("not found") from None
