"""T02b follow-up: bans keyed by identity, email as a plain lookup index.

One row per banned identity carries the last-known email, so a user
with several federated identities needs several rows sharing one
email. A unique email constraint forbids that; the plain index keeps
email lookups fast without constraining identity rows.
"""

from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF EXISTS: fresh databases from 0007 never had the constraint.
    op.execute("ALTER TABLE bans DROP CONSTRAINT IF EXISTS uq_bans_email")


def downgrade() -> None:
    op.create_unique_constraint("uq_bans_email", "bans", ["email"])
