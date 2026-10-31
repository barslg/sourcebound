"""Day-1 harness tables: RBAC, invites, runs, and the LLM call log.

Tables follow docs/HARNESS.md sections 3-4: the permission catalogue and
roles with scopes, invite links that grant the student role, generation
runs with resumable steps, and the LLM call log plus the
content-addressed reply store keyed by (model, prompt version, request).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, NUMERIC, UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _uuid_pk() -> UUID:
    return UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", _uuid_pk(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("display_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("is_superadmin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_table(
        "roles",
        sa.Column("id", _uuid_pk(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_table(
        "permissions",
        sa.Column("code", sa.Text(), primary_key=True),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
    )
    op.create_table(
        "role_permissions",
        sa.Column(
            "role_id", _uuid_pk(), sa.ForeignKey("roles.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "permission_code",
            sa.Text(),
            sa.ForeignKey("permissions.code", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("role_id", "permission_code"),
    )
    op.create_table(
        "user_roles",
        sa.Column(
            "user_id", _uuid_pk(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "role_id", _uuid_pk(), sa.ForeignKey("roles.id", ondelete="CASCADE"), nullable=False
        ),
        sa.PrimaryKeyConstraint("user_id", "role_id"),
    )
    op.create_table(
        "invites",
        sa.Column("id", _uuid_pk(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "token",
            _uuid_pk(),
            nullable=False,
            unique=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("scope", sa.String(200), nullable=False, server_default=""),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_by", _uuid_pk(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "runs",
        sa.Column("id", _uuid_pk(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("status", sa.String(50), nullable=False, server_default="running"),
        sa.Column("current_step", sa.String(200), nullable=True),
        sa.Column("error_marker", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_table(
        "run_steps",
        sa.Column("id", _uuid_pk(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "run_id", _uuid_pk(), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Index("ix_run_steps_run_id", "run_id"),
    )
    op.create_table(
        "llm_calls",
        sa.Column("id", _uuid_pk(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent", sa.String(200), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(100), nullable=False),
        sa.Column("tokens_input", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("tokens_cached", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("tokens_output", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("cost_usd", NUMERIC(12, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("request", JSONB(), nullable=False),
        sa.Column("response", JSONB(), nullable=True),
        sa.Column(
            "run_id", _uuid_pk(), sa.ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "step_id", _uuid_pk(), sa.ForeignKey("run_steps.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Index("ix_llm_calls_run_id", "run_id"),
    )
    op.create_table(
        "llm_replies",
        sa.Column("request_hash", sa.Text(), primary_key=True),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(100), nullable=False),
        sa.Column("response", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("llm_replies")
    op.drop_table("llm_calls")
    op.drop_table("run_steps")
    op.drop_table("runs")
    op.drop_table("invites")
    op.drop_table("user_roles")
    op.drop_table("role_permissions")
    op.drop_table("permissions")
    op.drop_table("roles")
    op.drop_table("users")
