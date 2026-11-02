"""Scenario planning: topics.scenario — the annotated line the outline wrote for each topic.

The outline (planning layer 1) writes per topic: annotation, the terms
it introduces, what it prepares the next topic for, the earlier topics
it requires and the passages of its own sections. Generators and the
reader read it from here; it is empty for topics written before this
migration and for author-added topics.

Revision ID: 0024
Revises: 0023
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "topics",
        sa.Column(
            "scenario",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("topics", "scenario")
