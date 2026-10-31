"""T03d: per-source ingest status with a failure message.

Uploads and URL intakes ingest inline (ready), audio waits for its
transcription job (queued) and lands ready or failed with the reason.
Existing rows predate the column and were all ingested inline, so
they upgrade as ready.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column("ingest_status", sa.String(20), nullable=False, server_default="ready"),
    )
    op.add_column("sources", sa.Column("ingest_message", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_sources_ingest_status",
        "sources",
        "ingest_status IN ('queued', 'parsing', 'ready', 'failed')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_sources_ingest_status", "sources", type_="check")
    op.drop_column("sources", "ingest_message")
    op.drop_column("sources", "ingest_status")
