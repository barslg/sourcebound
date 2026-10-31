"""Passages carry an is_content flag: reuse notices and source lines
are stored (they are part of the source) but never planted, searched
as exercise material, or offered to the spot-the-error picker.

Revision ID: 0015
Revises: 0014
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "passages",
        sa.Column("is_content", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("passages", "is_content")
