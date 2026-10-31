"""T03b scoped sources, per-course plan ids, run course link, BCP-47 language.

Sources stop being global: course_sources binds one course to one
source with its own approval (approver, timestamp), so retrieval and
the architect only ever see approved passages of their own course.
plan_node_id stops being globally unique (the second planned course
died with IntegrityError after a paid call): uniqueness is per
parent, which is per course through the module/topic chain. runs
gains course_id so run reads and resume answer 404 outside scope.
The course language check becomes a BCP-47 pattern; the service
layer keeps the en/nb/uk launch set. Downgrade removes exactly
these additions and restores the 0004 shape.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TEXT  # noqa: F401 - re-exported for history parity

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def _uuid_pk() -> sa.Column[sa.UUID]:
    return sa.Column(
        "id",
        sa.dialects.postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def _now(name: str = "created_at") -> sa.Column[sa.DateTime]:
    return sa.Column(
        name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )


def upgrade() -> None:
    op.create_table(
        "course_sources",
        _uuid_pk(),
        sa.Column(
            "course_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("approval", sa.String(20), nullable=False, server_default="pending"),
        sa.Column(
            "approved_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        _now(),
        sa.CheckConstraint(
            "approval IN ('pending', 'approved', 'held')",
            name="ck_course_sources_approval",
        ),
        sa.UniqueConstraint("course_id", "source_id", name="uq_course_sources_course_source"),
        sa.Index("ix_course_sources_course_id", "course_id"),
        sa.Index("ix_course_sources_source_id", "source_id"),
    )
    op.add_column(
        "runs",
        sa.Column(
            "course_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index("ix_runs_course_id", "runs", ["course_id"])
    # Plan ids are unique per course, not per database: the same model
    # labels repeat across courses, so the global constraint broke the
    # second plan. Per-parent uniqueness is per course through the chain.
    op.drop_constraint("uq_topics_plan_node_id", "topics", type_="unique")
    op.create_unique_constraint("uq_topics_module_node", "topics", ["module_id", "plan_node_id"])
    op.drop_constraint("uq_blocks_plan_node_id", "blocks", type_="unique")
    op.create_unique_constraint("uq_blocks_topic_node", "blocks", ["topic_id", "plan_node_id"])
    # Launch languages stay en/nb/uk in the service layer; the database
    # accepts any BCP-47 tag so later languages need no schema change.
    op.drop_constraint("ck_courses_language", "courses", type_="check")
    op.create_check_constraint(
        "ck_courses_language",
        "courses",
        "language ~ '^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$'",
    )


def downgrade() -> None:
    op.drop_constraint("ck_courses_language", "courses", type_="check")
    op.create_check_constraint("ck_courses_language", "courses", "language IN ('en', 'nb', 'uk')")
    op.drop_constraint("uq_blocks_topic_node", "blocks", type_="unique")
    op.create_unique_constraint("uq_blocks_plan_node_id", "blocks", ["plan_node_id"])
    op.drop_constraint("uq_topics_module_node", "topics", type_="unique")
    op.create_unique_constraint("uq_topics_plan_node_id", "topics", ["plan_node_id"])
    op.drop_index("ix_runs_course_id", table_name="runs")
    op.drop_column("runs", "course_id")
    op.drop_table("course_sources")
