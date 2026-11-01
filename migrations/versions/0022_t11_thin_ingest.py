"""T11: allow ingest_status 'thin' for navigation-soup URL pages.

A fetched page with no readable sentence is marked thin with a plain
message and can never be approved. Downgrade maps thin rows to
'failed' (message kept), the closest state the old code knows.

Revision ID: 0022
Revises: 0021
"""

from __future__ import annotations

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_sources_ingest_status", "sources", type_="check")
    op.create_check_constraint(
        "ck_sources_ingest_status",
        "sources",
        "ingest_status IN ('queued', 'parsing', 'ready', 'failed', 'thin')",
    )


def downgrade() -> None:
    op.execute("UPDATE sources SET ingest_status = 'failed' WHERE ingest_status = 'thin'")
    op.drop_constraint("ck_sources_ingest_status", "sources", type_="check")
    op.create_check_constraint(
        "ck_sources_ingest_status",
        "sources",
        "ingest_status IN ('queued', 'parsing', 'ready', 'failed')",
    )
