"""T07c: translations belong to block content, and remember their variant.

- translation_blocks.source_digest: digest of the source content and key
  the row translated; learner reads match on it, so a snapshot or a
  publish keeps the translation and a block edit makes it stale.
- translation_blocks.variant: the model variant that produced the row,
  reused when the row is ok and raised by one when it was held.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("translation_blocks", sa.Column("source_digest", sa.Text(), nullable=True))
    op.add_column(
        "translation_blocks",
        sa.Column("variant", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("translation_blocks", "variant")
    op.drop_column("translation_blocks", "source_digest")
