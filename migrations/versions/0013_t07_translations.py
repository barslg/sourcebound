"""T07 course translations, per-block states, and translation memory.

course_translations holds one row per (course version, target language)
with the glossary term table used and the workflow run that produced it.
translation_blocks carries the per-block translated content plus its
ok/held/failed state and reason. translation_memory caches translated
segments across blocks and runs; planted sentences never land here.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "course_translations",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "course_version_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("course_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_language", sa.Text(), nullable=False, server_default="en"),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="partial"),
        sa.Column("glossary", JSONB(), nullable=False, server_default="{}"),
        sa.Column("run_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('ready', 'partial', 'failed')",
            name="ck_course_translations_status",
        ),
        sa.UniqueConstraint(
            "course_version_id", "language", name="uq_course_translations_version_lang"
        ),
        sa.Index("ix_course_translations_version_id", "course_version_id"),
    )
    op.create_table(
        "translation_blocks",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "translation_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("course_translations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plan_node_id", sa.Text(), nullable=False),
        sa.Column("block_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="failed"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("content", JSONB(), nullable=False, server_default="{}"),
        sa.CheckConstraint(
            "status IN ('ok', 'held', 'failed')",
            name="ck_translation_blocks_status",
        ),
        sa.UniqueConstraint("translation_id", "plan_node_id", name="uq_translation_blocks_row"),
        sa.Index("ix_translation_blocks_translation_id", "translation_id"),
    )
    op.create_table(
        "translation_memory",
        sa.Column("source_hash", sa.Text(), primary_key=True),
        sa.Column("source_language", sa.Text(), nullable=False),
        sa.Column("target_language", sa.Text(), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("translated_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Index("ix_translation_memory_lang_pair", "source_language", "target_language"),
    )


def downgrade() -> None:
    op.drop_table("translation_memory")
    op.drop_table("translation_blocks")
    op.drop_table("course_translations")
