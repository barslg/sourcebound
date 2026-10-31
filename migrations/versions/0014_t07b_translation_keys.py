"""T07b: translated answer keys, run kind, translation run FK, TM course scope.

- translation_blocks.translated_answer_key: the per-translation answer
  key (index-keyed), so translated exercises grade like the source.
- runs.kind: new_run actually stores the kind it was given.
- course_translations.run_id becomes a real foreign key to runs.
- translation_memory.course_id scopes the memory per course.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "translation_blocks",
        sa.Column(
            "translated_answer_key", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )
    op.add_column("runs", sa.Column("kind", sa.Text(), nullable=True))
    # A run_id that points at no run would make the constraint fail to
    # build: clear the orphans first (the column is nullable by design).
    op.execute(
        "UPDATE course_translations SET run_id = NULL"
        " WHERE run_id IS NOT NULL AND NOT EXISTS"
        " (SELECT 1 FROM runs WHERE runs.id = course_translations.run_id)"
    )
    op.create_foreign_key(
        "fk_course_translations_run_id",
        "course_translations",
        "runs",
        ["run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "translation_memory",
        sa.Column("course_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_translation_memory_course_id", "translation_memory", ["course_id"])


def downgrade() -> None:
    op.drop_index("ix_translation_memory_course_id", table_name="translation_memory")
    op.drop_column("translation_memory", "course_id")
    op.drop_constraint("fk_course_translations_run_id", "course_translations", type_="foreignkey")
    op.drop_column("runs", "kind")
    op.drop_column("translation_blocks", "translated_answer_key")
