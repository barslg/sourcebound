"""T06b: two pins, one migration.

blocks.accepted_version_id pins an author's accept to one block version:
a reader verdict replayed in a fresh run must not re-hold a version the
author already accepted, while a new version (rewrite, rework) differs
from the pinned id and is judged again.

learner_block_views stores the language each learner was served per
block at GET time, so an answer posted without ?lang= is graded against
the key of the language the learner actually saw. Deleting the learner
deletes the rows. Downgrade drops both; block statuses stay as they are.

Revision ID: 0019
Revises: 0018
"""

from __future__ import annotations

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE blocks ADD COLUMN accepted_version_id uuid"
        " REFERENCES block_versions(id) ON DELETE SET NULL"
    )
    op.execute(
        "CREATE TABLE learner_block_views ("
        " user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,"
        " block_id uuid NOT NULL REFERENCES blocks(id) ON DELETE CASCADE,"
        " language varchar(8) NOT NULL,"
        " updated_at timestamptz NOT NULL DEFAULT now(),"
        " PRIMARY KEY (user_id, block_id))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE learner_block_views")
    op.execute("ALTER TABLE blocks DROP COLUMN accepted_version_id")
