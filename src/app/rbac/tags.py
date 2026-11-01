"""Scope tag keys: a group tag is stored owner-qualified, shown as the author typed it.

A course id is its own key. A group tag ("oslo") is stored as
`<owner id>:<tag>` so two owners' groups never meet; every place that
compares a STORED tag with a tag an actor was granted (staff scopes are
plain names) compares display tags. Callers: services/invites.py (create,
list, accept), services/users.py, services/operations.py, rbac/roles.py,
controllers/t02.py.
"""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

GROUP_SEP = ":"


def is_course_id(tag: str) -> bool:
    try:
        UUID(tag)
    except ValueError:
        return False
    return True


def group_key(owner_id: UUID | str, tag: str) -> str:
    """The stored key of a group tag: owner-scoped, so two owners' "oslo" never meet."""
    return f"{owner_id}{GROUP_SEP}{tag}"


def display_tag(stored: str) -> str:
    """What the author typed: a course id as is, a group key without its owner prefix."""
    owner, sep, tag = stored.partition(GROUP_SEP)
    return tag if sep and is_course_id(owner) else stored


def display_tags(stored: Collection[str]) -> list[str]:
    """Sorted, de-duplicated display tags of stored tags."""
    return sorted({display_tag(tag) for tag in stored})


def group_keys_of(owner_id: UUID | str, tags: Collection[str]) -> list[str]:
    """Group keys one owner's course tags open (what a student grant is compared against)."""
    return [group_key(owner_id, tag) for tag in tags]
