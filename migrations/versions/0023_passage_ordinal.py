"""T11b: passages.ordinal — document order stored at ingest.

Heading-path addresses sorted alphabetically read a page out of order
(P98 "Drive it…" before P99 "First run"); the ordinal is the position
of the passage in its source as ingested. Existing rows are backfilled
in address order (what every reader used before), so nothing moves.

Revision ID: 0023
Revises: 0022
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("passages", sa.Column("ordinal", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE passages p SET ordinal = r.n - 1 FROM (SELECT id, row_number() OVER"
        " (PARTITION BY source_id ORDER BY address_key) AS n FROM passages) r WHERE r.id = p.id"
    )


def downgrade() -> None:
    op.drop_column("passages", "ordinal")
