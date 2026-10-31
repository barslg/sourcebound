"""T03 course model, library with addressed passages, and run additions.

Courses own modules, topics, and blocks; blocks version their content
with the answer key kept server-side. Sources are immutable by content
sha256 and chunked by code into addressed passages with a Postgres
full-text index (no embeddings anywhere). Runs gain the compiled DAG,
estimate, and stop marker; steps gain the per-run step key with the
input digest; operations gain the per-actor idempotency key.
Downgrade removes exactly these additions.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TEXT

revision = "0004"
down_revision = "0003"
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
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "course_groups",
        _uuid_pk(),
        sa.Column("tag", sa.Text(), nullable=False, unique=True),
        _now(),
    )
    op.create_table(
        "courses",
        _uuid_pk(),
        sa.Column(
            "owner_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("language", sa.String(8), nullable=False, server_default="en"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("scope_tags", ARRAY(TEXT()), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "published_version_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        _now(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("status IN ('draft', 'published')", name="ck_courses_status"),
        sa.CheckConstraint("language IN ('en', 'nb', 'uk')", name="ck_courses_language"),
        sa.Index("ix_courses_owner_id", "owner_id"),
    )
    op.create_table(
        "modules",
        _uuid_pk(),
        sa.Column(
            "course_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False, server_default=""),
        sa.Column("entry_knowledge", sa.Text(), nullable=False, server_default=""),
        sa.Column("exit_knowledge", sa.Text(), nullable=False, server_default=""),
        _now(),
        sa.UniqueConstraint("course_id", "ordinal", name="uq_modules_course_ordinal"),
        sa.Index("ix_modules_course_id", "course_id"),
    )
    # The ordinal is unique per course but reorderable inside one
    # transaction: DEFERRABLE INITIALLY DEFERRED so a swap of two
    # ordinals commits without a transient violation.
    op.execute(
        "ALTER TABLE modules DROP CONSTRAINT uq_modules_course_ordinal,"
        " ADD CONSTRAINT uq_modules_course_ordinal"
        " UNIQUE (course_id, ordinal) DEFERRABLE INITIALLY DEFERRED"
    )
    op.create_table(
        "topics",
        _uuid_pk(),
        sa.Column(
            "module_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("modules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("kind", sa.String(20), nullable=False, server_default="lesson"),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("objectives", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("through_line_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("plan_node_id", sa.Text(), nullable=False),
        sa.Column("plan_digest", sa.Text(), nullable=False, server_default=""),
        _now(),
        sa.CheckConstraint("kind IN ('lesson', 'module_test')", name="ck_topics_kind"),
        sa.UniqueConstraint("plan_node_id", name="uq_topics_plan_node_id"),
        sa.Index("ix_topics_module_id", "module_id"),
    )
    op.create_table(
        "blocks",
        _uuid_pk(),
        sa.Column(
            "topic_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("topics.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plan_node_id", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("block_type", sa.String(50), nullable=False, server_default="reading"),
        sa.Column("brief", sa.Text(), nullable=False),
        sa.Column(
            "current_version_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("hold_reason", sa.Text(), nullable=True),
        _now(),
        sa.CheckConstraint(
            "role IN ('teach', 'example', 'check', 'practice', 'test')",
            name="ck_blocks_role",
        ),
        sa.CheckConstraint("status IN ('draft', 'held', 'accepted')", name="ck_blocks_status"),
        sa.UniqueConstraint("plan_node_id", name="uq_blocks_plan_node_id"),
        sa.Index("ix_blocks_topic_id", "topic_id"),
    )
    op.create_table(
        "block_versions",
        _uuid_pk(),
        sa.Column(
            "block_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("blocks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("content", JSONB(), nullable=False),
        sa.Column("answer_key", JSONB(), nullable=True),
        sa.Column("schema_version", sa.String(20), nullable=False, server_default="1"),
        sa.Column(
            "run_step_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("run_steps.id", ondelete="SET NULL"),
            nullable=True,
        ),
        _now(),
        sa.UniqueConstraint("block_id", "version_no", name="uq_block_versions_block_no"),
        sa.Index("ix_block_versions_block_id", "block_id"),
    )
    op.create_table(
        "course_versions",
        _uuid_pk(),
        sa.Column(
            "course_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("snapshot", JSONB(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        _now(),
        sa.Index("ix_course_versions_course_id", "course_id"),
    )
    op.create_table(
        "methodologies",
        sa.Column("code", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "compatible_with", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("prescription", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_table(
        "course_profiles",
        sa.Column(
            "course_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "methodology_codes", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("bloom_level", sa.String(30), nullable=False, server_default="understand"),
        sa.Column("audience", sa.Text(), nullable=False, server_default=""),
        sa.Column("dreyfus_start", sa.String(30), nullable=False, server_default="novice"),
    )
    op.create_table(
        "profile_constraints",
        _uuid_pk(),
        sa.Column(
            "course_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("answer", JSONB(), nullable=True),
        _now(),
        sa.UniqueConstraint("course_id", "key", name="uq_profile_constraints_course_key"),
        sa.Index("ix_profile_constraints_course_id", "course_id"),
    )
    op.create_table(
        "sources",
        _uuid_pk(),
        sa.Column("content_sha256", sa.Text(), nullable=False, unique=True),
        sa.Column("kind", sa.String(20), nullable=False, server_default="upload"),
        sa.Column("media", sa.String(20), nullable=False, server_default="text"),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("licence", sa.Text(), nullable=False, server_default=""),
        sa.Column("approval", sa.String(20), nullable=False, server_default="pending"),
        sa.Column(
            "approved_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("parser_spec", JSONB(), nullable=True),
        sa.Column("as_of", sa.Date(), nullable=True),
        sa.Column("storage_path", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "supersedes_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sources.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        _now(),
        sa.CheckConstraint("kind IN ('upload', 'url')", name="ck_sources_kind"),
        sa.CheckConstraint(
            "media IN ('pdf', 'text', 'markdown', 'image', 'audio')",
            name="ck_sources_media",
        ),
        sa.CheckConstraint(
            "approval IN ('pending', 'approved', 'held')", name="ck_sources_approval"
        ),
        sa.Index("ix_sources_sha256", "content_sha256"),
    )
    op.create_table(
        "passages",
        _uuid_pk(),
        sa.Column(
            "source_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("address", JSONB(), nullable=False),
        sa.Column("address_key", sa.Text(), nullable=False),
        sa.Column("ts_config", sa.Text(), nullable=False, server_default="english"),
        sa.Column("tsv", sa.dialects.postgresql.TSVECTOR(), nullable=True),
        _now(),
        sa.CheckConstraint(
            "address->>'kind' IN ('pdf', 'md', 'web', 'av', 'figure')",
            name="ck_passages_address_kind",
        ),
        sa.UniqueConstraint("source_id", "address_key", name="uq_passages_source_address"),
        sa.Index("ix_passages_source_id", "source_id"),
    )
    op.execute("CREATE INDEX ix_passages_tsv ON passages USING GIN (tsv)")
    op.execute("CREATE INDEX ix_passages_text_trgm ON passages USING GIN (text gin_trgm_ops)")
    op.execute(
        "CREATE OR REPLACE FUNCTION passages_tsv_trigger() RETURNS trigger AS $$"
        " BEGIN NEW.tsv := to_tsvector(NEW.ts_config::regconfig, COALESCE(NEW.text, ''));"
        " RETURN NEW; END; $$ LANGUAGE plpgsql"
    )
    op.execute(
        "CREATE TRIGGER trg_passages_tsv BEFORE INSERT OR UPDATE OF text, ts_config"
        " ON passages FOR EACH ROW EXECUTE FUNCTION passages_tsv_trigger()"
    )
    op.create_table(
        "citations",
        _uuid_pk(),
        sa.Column(
            "block_version_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("block_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "passage_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("passages.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("claim_path", sa.Text(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("quote_start", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("quote_end", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("verified_by", sa.String(20), nullable=False, server_default="code"),
        _now(),
        sa.Index("ix_citations_block_version_id", "block_version_id"),
    )
    op.create_table(
        "retrieval_pairs",
        _uuid_pk(),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("query_lang", sa.String(8), nullable=False, server_default="en"),
        sa.Column(
            "passage_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("passages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ranker", sa.String(30), nullable=False, server_default="fts"),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("label", sa.String(20), nullable=False, server_default="retrieved"),
        sa.Column("label_source", sa.String(30), nullable=False, server_default="pipeline"),
        _now(),
        sa.Index("ix_retrieval_pairs_passage_id", "passage_id"),
    )
    op.create_table(
        "source_candidates",
        _uuid_pk(),
        sa.Column(
            "course_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("query", sa.Text(), nullable=False, server_default=""),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("snippet", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        _now(),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_source_candidates_status",
        ),
        sa.Index("ix_source_candidates_course_id", "course_id"),
    )
    op.create_table(
        "human_queue",
        _uuid_pk(),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False, server_default=""),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("payload", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        _now(),
        sa.CheckConstraint("status IN ('open', 'done')", name="ck_human_queue_status"),
    )
    op.add_column("runs", sa.Column("compiled_dag", JSONB(), nullable=True))
    op.add_column("runs", sa.Column("dag_digest", sa.Text(), nullable=True))
    op.add_column("runs", sa.Column("prompt_snapshot", JSONB(), nullable=True))
    op.add_column("runs", sa.Column("estimate", JSONB(), nullable=True))
    op.add_column("runs", sa.Column("stop_marker", sa.Text(), nullable=True))
    op.add_column("run_steps", sa.Column("step_key", sa.Text(), nullable=True))
    op.add_column(
        "run_steps",
        sa.Column("depends_on", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column("run_steps", sa.Column("input_digest", sa.Text(), nullable=True))
    op.add_column("run_steps", sa.Column("output", JSONB(), nullable=True))
    op.add_column(
        "run_steps", sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("0"))
    )
    op.create_unique_constraint("uq_run_steps_run_step_key", "run_steps", ["run_id", "step_key"])
    op.add_column("operations", sa.Column("idempotency_key", sa.Text(), nullable=True))
    op.create_unique_constraint(
        "uq_operations_actor_idempotency", "operations", ["actor_id", "idempotency_key"]
    )
    op.create_foreign_key(
        "fk_courses_published_version",
        "courses",
        "course_versions",
        ["published_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_blocks_current_version",
        "blocks",
        "block_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_blocks_current_version", "blocks", type_="foreignkey")
    op.drop_constraint("fk_courses_published_version", "courses", type_="foreignkey")
    op.drop_constraint("uq_operations_actor_idempotency", "operations", type_="unique")
    op.drop_column("operations", "idempotency_key")
    op.drop_constraint("uq_run_steps_run_step_key", "run_steps", type_="unique")
    op.drop_column("run_steps", "attempt")
    op.drop_column("run_steps", "output")
    op.drop_column("run_steps", "input_digest")
    op.drop_column("run_steps", "depends_on")
    op.drop_column("run_steps", "step_key")
    op.drop_column("runs", "stop_marker")
    op.drop_column("runs", "estimate")
    op.drop_column("runs", "prompt_snapshot")
    op.drop_column("runs", "dag_digest")
    op.drop_column("runs", "compiled_dag")
    op.drop_table("human_queue")
    op.drop_table("source_candidates")
    op.drop_table("retrieval_pairs")
    op.drop_table("citations")
    op.execute("DROP TRIGGER IF EXISTS trg_passages_tsv ON passages")
    op.execute("DROP FUNCTION IF EXISTS passages_tsv_trigger()")
    op.execute("DROP INDEX IF EXISTS ix_passages_text_trgm")
    op.execute("DROP INDEX IF EXISTS ix_passages_tsv")
    op.drop_table("passages")
    op.drop_table("sources")
    op.drop_table("profile_constraints")
    op.drop_table("course_profiles")
    op.drop_table("methodologies")
    op.drop_table("course_versions")
    op.drop_table("block_versions")
    op.drop_table("blocks")
    op.drop_table("topics")
    op.execute("ALTER TABLE modules DROP CONSTRAINT uq_modules_course_ordinal")
    op.drop_table("modules")
    op.drop_table("courses")
    op.drop_table("course_groups")
