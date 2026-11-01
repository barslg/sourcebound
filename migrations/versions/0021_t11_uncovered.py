"""T11: courses.uncovered — plan passages deliberately left out, with reasons.

The architect declares which content passages its plan does not cite
and why; the author sees that list on the Plan page. Downgrade drops
the column (the previous code never reads it).

Revision ID: 0021
Revises: 0020
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "courses",
        sa.Column(
            "uncovered",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("courses", "uncovered")
