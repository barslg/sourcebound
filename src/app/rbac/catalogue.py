"""Permission catalogue: the declared list every role draws from.

`section.*` codes gate UI sections; the rest gate actions. The table
is synced at startup; a new permission is granted to no existing
non-admin role, while the administrator role is rewritten to all
codes on every start. Role saves with unknown codes are rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

ADMIN_ROLE_NAME = "administrator"
STUDENT_ROLE_NAME = "student"
STUDENT_PERMISSION_CODES: tuple[str, ...] = ("learn.read", "learn.answer", "learn.ask")


@dataclass(frozen=True)
class PermissionDef:
    """One permission: code, category, label, description."""

    code: str
    category: str
    label: str
    description: str


CATALOGUE: tuple[PermissionDef, ...] = (
    PermissionDef("section.courses", "sections", "Courses", "Open the courses section."),
    PermissionDef("section.sources", "sections", "Sources", "Open the sources section."),
    PermissionDef("section.runs", "sections", "Runs", "Open the generation runs section."),
    PermissionDef("section.learners", "sections", "Learners", "Open the learners section."),
    PermissionDef("section.users", "sections", "Users", "Open the users section."),
    PermissionDef("section.settings", "sections", "Settings", "Open the settings section."),
    PermissionDef("course.create", "courses", "Create courses", "Start a new course."),
    PermissionDef("course.edit", "courses", "Edit courses", "Change course content."),
    PermissionDef("course.plan", "courses", "Submit plans", "Submit plan generation."),
    PermissionDef("course.generate", "courses", "Submit generation", "Submit block generation."),
    PermissionDef("course.publish", "courses", "Publish courses", "Publish a course version."),
    PermissionDef("course.delete", "courses", "Delete courses", "Delete a course."),
    PermissionDef("source.create", "sources", "Add sources", "Add a source to the library."),
    PermissionDef("source.upload", "sources", "Upload sources", "Upload or fetch a source."),
    PermissionDef("source.edit", "sources", "Edit sources", "Change a source record."),
    PermissionDef("source.delete", "sources", "Delete sources", "Delete a source."),
    PermissionDef("source.approve", "sources", "Approve sources", "Approve a source candidate."),
    PermissionDef("run.start", "runs", "Start runs", "Start a generation run."),
    PermissionDef("run.cancel", "runs", "Cancel runs", "Cancel a running generation run."),
    PermissionDef("run.retry", "runs", "Retry runs", "Resume a run from its last good step."),
    PermissionDef("learner.view", "learners", "View learners", "See learner records."),
    PermissionDef("learner.report", "learners", "Learner reports", "Open teacher reports."),
    PermissionDef("user.list", "users", "List users", "See the user directory."),
    PermissionDef("user.ban", "users", "Ban users", "Ban an account from signing in."),
    PermissionDef("user.delete", "users", "Delete users", "Delete an account."),
    PermissionDef("role.edit", "roles", "Edit roles", "Create and change roles."),
    PermissionDef("role.assign", "roles", "Assign roles", "Grant or revoke roles on users."),
    PermissionDef("invite.create", "invites", "Create invites", "Create invite links."),
    PermissionDef("invite.revoke", "invites", "Revoke invites", "Revoke invite links."),
    PermissionDef(
        "learn.read", "learning", "Read enrolled courses", "Read courses you are enrolled in."
    ),
    PermissionDef(
        "learn.answer", "learning", "Answer blocks", "Submit answers in enrolled courses."
    ),
    PermissionDef(
        "learn.ask", "learning", "Ask the tutor", "Ask questions answered from course sources."
    ),
    PermissionDef("operation.run", "operations", "Run operations", "Submit long writes."),
    PermissionDef("operation.view", "operations", "View operations", "Inspect any operation."),
)


def codes(defs: tuple[PermissionDef, ...] = CATALOGUE) -> set[str]:
    """Every declared permission code."""
    return {entry.code for entry in defs}


async def sync_catalogue(session: AsyncSession, extra: tuple[PermissionDef, ...] = ()) -> None:
    """Upsert every catalogue code; never touch existing role grants.

    A brand-new permission is therefore granted to no existing
    non-admin role; only ensure_admin_role() widens the administrator.
    """
    for entry in (*CATALOGUE, *extra):
        await session.execute(
            sa.text(
                "INSERT INTO permissions (code, category, label, description)"
                " VALUES (:code, :category, :label, :description)"
                " ON CONFLICT (code) DO UPDATE SET"
                " category = EXCLUDED.category,"
                " label = EXCLUDED.label,"
                " description = EXCLUDED.description"
            ),
            {
                "code": entry.code,
                "category": entry.category,
                "label": entry.label,
                "description": entry.description,
            },
        )


async def ensure_admin_role(session: AsyncSession) -> UUID:
    """Create the administrator role when missing and grant it every code."""
    row = (
        await session.execute(
            sa.text("SELECT id FROM roles WHERE name = :name"), {"name": ADMIN_ROLE_NAME}
        )
    ).first()
    if row is None:
        created = (
            await session.execute(
                sa.text("INSERT INTO roles (name) VALUES (:name) RETURNING id"),
                {"name": ADMIN_ROLE_NAME},
            )
        ).one()
        role_id = UUID(str(created[0]))
    else:
        role_id = UUID(str(row[0]))
    await session.execute(
        sa.text("DELETE FROM role_permissions WHERE role_id = :role"), {"role": str(role_id)}
    )
    await session.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_code)"
            " SELECT :role, code FROM permissions"
            " ON CONFLICT DO NOTHING"
        ),
        {"role": str(role_id)},
    )
    return role_id
