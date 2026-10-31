"""Table objects for the LLM call log and reply store.

Mirrors migration 0001; the migration itself is op-based so the schema
history never depends on these objects.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import MetaData, Table
from sqlalchemy.dialects.postgresql import JSONB, NUMERIC
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

metadata = MetaData()

llm_calls = Table(
    "llm_calls",
    metadata,
    sa.Column(
        "id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    ),
    sa.Column("agent", sa.String(200), nullable=False),
    sa.Column("model", sa.String(100), nullable=False),
    sa.Column("prompt_version", sa.String(100), nullable=False),
    sa.Column("tokens_input", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column("tokens_cached", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column("tokens_output", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column("tokens_reasoning", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column("cost_usd", NUMERIC(12, 6), nullable=False, server_default=sa.text("0")),
    sa.Column("duration_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column("request", JSONB(), nullable=False),
    sa.Column("response", JSONB(), nullable=True),
    sa.Column("response_id", sa.Text(), nullable=True),
    sa.Column("status", sa.String(20), nullable=False, server_default="completed"),
    sa.Column("variant", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column(
        "subject_user_id",
        PG_UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column(
        "run_id",
        PG_UUID(as_uuid=True),
        sa.ForeignKey("runs.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column(
        "step_id",
        PG_UUID(as_uuid=True),
        sa.ForeignKey("run_steps.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
)

llm_replies = Table(
    "llm_replies",
    metadata,
    sa.Column("request_hash", sa.Text(), primary_key=True),
    sa.Column("model", sa.String(100), nullable=False),
    sa.Column("prompt_version", sa.String(100), nullable=False),
    sa.Column("response", JSONB(), nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
)
