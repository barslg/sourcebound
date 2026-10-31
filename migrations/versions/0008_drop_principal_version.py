"""T02b follow-up: drop the principal version counter.

Loading principals fresh on every call made the counter obsolete; the
counter row caused write deadlocks when a bump shared a transaction
with later work, so the table goes away instead of staying vestigial.
"""

from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("principal_version")


def downgrade() -> None:
    import sqlalchemy as sa

    op.create_table(
        "principal_version",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
    )
    op.execute("INSERT INTO principal_version (id, version) VALUES (1, 1)")
