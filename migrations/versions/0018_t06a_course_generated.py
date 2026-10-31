"""T06a: courses.status gains `generated` (every block has a current version).

The run sets it when generation leaves every block with a current
version whose content passes its type schema; re-planning or editing
the plan sets it back to draft. Downgrade maps `generated` to `draft`
before restoring the narrower check.

Revision ID: 0018
Revises: 0017
"""

from __future__ import annotations

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE courses DROP CONSTRAINT ck_courses_status")
    op.execute(
        "ALTER TABLE courses ADD CONSTRAINT ck_courses_status"
        " CHECK (status IN ('draft', 'generated', 'published'))"
    )


def downgrade() -> None:
    op.execute("UPDATE courses SET status = 'draft' WHERE status = 'generated'")
    op.execute("ALTER TABLE courses DROP CONSTRAINT ck_courses_status")
    op.execute(
        "ALTER TABLE courses ADD CONSTRAINT ck_courses_status"
        " CHECK (status IN ('draft', 'published'))"
    )
