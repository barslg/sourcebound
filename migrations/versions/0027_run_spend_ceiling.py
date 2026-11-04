"""A run may carry a spend ceiling the provider enforces before every paid call.

`runs.spend_ceiling_usd` is NULL for every run a person drives by hand; the
course build sets it before it drives a child run, so the cap holds even for a
manual resume of that run.

Revision ID: 0027
Revises: 0026
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("spend_ceiling_usd", sa.Numeric(12, 6), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "spend_ceiling_usd")
