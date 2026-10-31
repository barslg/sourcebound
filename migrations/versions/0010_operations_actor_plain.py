"""T02b follow-up: operations keep their actor as a plain uuid.

Cancelling instead of reassigning only preserves history when the
actor id survives the user deletion; the SET NULL foreign key wiped
it, so the constraint goes away like the audit actor one.
"""

from __future__ import annotations

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("operations_actor_id_fkey", "operations", type_="foreignkey")


def downgrade() -> None:
    # Normalise rows orphaned while the column was a plain uuid, so the
    # restored foreign key validates: this is what SET NULL would have done.
    op.execute(
        "UPDATE operations SET actor_id = NULL"
        " WHERE actor_id IS NOT NULL AND actor_id NOT IN (SELECT id FROM users)"
    )
    op.create_foreign_key(
        "operations_actor_id_fkey",
        "operations",
        "users",
        ["actor_id"],
        ["id"],
        ondelete="SET NULL",
    )
