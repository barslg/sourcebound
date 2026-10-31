"""One UUID dependency for every id-taking route.

A malformed id is a 404, never a 500: the dependency parses the path
value and raises NotFoundError before any controller runs. Every
id-named path parameter in the OpenAPI spec routes through here.
"""

from __future__ import annotations

from uuid import UUID

from app.errors import NotFoundError


def parse_uuid_or_404(value: str) -> UUID:
    """Parse one path id; malformed values answer 404."""
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise NotFoundError("not found") from None


def _validated(value: str) -> str:
    parse_uuid_or_404(value)
    return value


def course_id_param(course_id: str) -> str:
    return _validated(course_id)


def run_id_param(run_id: str) -> str:
    return _validated(run_id)


def block_id_param(block_id: str) -> str:
    return _validated(block_id)


def source_id_param(source_id: str) -> str:
    return _validated(source_id)


def candidate_id_param(candidate_id: str) -> str:
    return _validated(candidate_id)


def item_id_param(item_id: str) -> str:
    return _validated(item_id)


def operation_id_param(operation_id: str) -> str:
    return _validated(operation_id)


def user_id_param(user_id: str) -> str:
    return _validated(user_id)


def invite_id_param(invite_id: str) -> str:
    return _validated(invite_id)


def notification_id_param(notification_id: str) -> str:
    return _validated(notification_id)
