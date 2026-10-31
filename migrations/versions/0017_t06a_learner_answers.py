"""T06a: learner_answers, one row per graded attempt.

Attempts never lock: every submission is a new row with the next
attempt number for (user, block). The row pins the block version that
was graded and the language it was served in; `result` holds the
grader's classification (error kind, hit / false alarm / miss for
find-the-error) so counters never re-run the grader. Deleting a user,
a block or a version deletes the answers (personal data cascades).

Revision ID: 0017
Revises: 0016
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learner_answers",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "block_id",
            UUID(as_uuid=True),
            sa.ForeignKey("blocks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "block_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("block_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("language", sa.String(8), nullable=False),
        sa.Column("payload", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("result", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.CheckConstraint("score >= 0 AND score <= 1", name="ck_learner_answers_score"),
        sa.UniqueConstraint("user_id", "block_id", "attempt_no", name="uq_learner_answers_attempt"),
        sa.Index("ix_learner_answers_block_id", "block_id"),
    )


def downgrade() -> None:
    op.drop_table("learner_answers")
