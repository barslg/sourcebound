"""T02c: the signed-out invite stash holds the invite id, not the token.

The raw token no longer appears in any session row, URL, or header
after the accept POST: the anonymous session carries the invite id,
the login return address points at the course page, and the callback
applies the invite server-side. Stored token hashes from the old
column are dropped on upgrade (they are single-use login state, and a
hash is not an id).
"""

from __future__ import annotations

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE sessions SET pending_invite_token = NULL")
    op.alter_column("sessions", "pending_invite_token", new_column_name="pending_invite_id")


def downgrade() -> None:
    op.alter_column("sessions", "pending_invite_id", new_column_name="pending_invite_token")
