"""T03b through-line: the course-level case object lives on the course.

The plan tree carries a through-line (one case plus per-topic
handoffs, BRIEF §3); persisting it on courses keeps it beside the
plan it belongs to. Downgrade drops the column.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "courses",
        sa.Column(
            "through_line",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("courses", "through_line")
