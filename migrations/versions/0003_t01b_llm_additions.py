"""T01b LLM-layer additions on llm_calls.

Adds reasoning-token billing, background polling state (response id plus
pending/completed status), the new-draft variant, and the learner subject
whose text is wiped on account deletion while cost is kept.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_calls",
        sa.Column("tokens_reasoning", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column("llm_calls", sa.Column("response_id", sa.Text(), nullable=True))
    op.add_column(
        "llm_calls",
        sa.Column("status", sa.String(20), nullable=False, server_default="completed"),
    )
    op.add_column(
        "llm_calls",
        sa.Column("variant", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "llm_calls",
        sa.Column(
            "subject_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_llm_calls_subject_user_id", "llm_calls", ["subject_user_id"])
    # Backfill rows written before the status column existed.
    op.execute(sa.text("UPDATE llm_calls SET status = 'completed' WHERE status IS NULL"))


def downgrade() -> None:
    op.drop_index("ix_llm_calls_subject_user_id", table_name="llm_calls")
    op.drop_column("llm_calls", "subject_user_id")
    op.drop_column("llm_calls", "variant")
    op.drop_column("llm_calls", "status")
    op.drop_column("llm_calls", "response_id")
    op.drop_column("llm_calls", "tokens_reasoning")
