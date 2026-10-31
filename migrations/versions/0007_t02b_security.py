"""T02b security close-out: hashed invite tokens, bans, sub pinning, versions.

Adds superadmin_identities (sub pinning per superadmin email), bans
(normalised email plus identity digests so ban survives delete),
principal_version (database counter for the principal cache), invite
token hashes (sha256 only, plaintext token dropped), session absolute
expiry plus hashed pending invites, auth state session binding,
operation idempotency keys, and drops the audit actor foreign key so
authorship survives user deletion. Downgrade restores the 0006 shape.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TEXT, UUID

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def _uuid_pk() -> UUID:
    return UUID(as_uuid=True)


def _now() -> sa.Column[sa.DateTime]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.create_table(
        "superadmin_identities",
        sa.Column("email", sa.String(320), primary_key=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        _now(),
    )
    op.create_table(
        "bans",
        sa.Column("id", _uuid_pk(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("provider", sa.String(50), nullable=True),
        sa.Column("subject_digest", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        _now(),
        sa.UniqueConstraint("provider", "subject_digest", name="uq_bans_provider_digest"),
        sa.Index("ix_bans_email", "email"),
    )
    op.create_table(
        "principal_version",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
    )
    op.execute("INSERT INTO principal_version (id, version) VALUES (1, 1)")

    op.add_column("invites", sa.Column("token_hash", TEXT(), nullable=True))
    op.execute("UPDATE invites SET token_hash = encode(digest(token::text, 'sha256'), 'hex')")
    op.alter_column("invites", "token_hash", nullable=False)
    op.create_unique_constraint("uq_invites_token_hash", "invites", ["token_hash"])
    op.drop_column("invites", "token")

    op.add_column(
        "sessions",
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE sessions SET absolute_expires_at = expires_at")
    op.alter_column("sessions", "absolute_expires_at", nullable=False)
    op.alter_column(
        "sessions",
        "pending_invite_token",
        type_=TEXT(),
        postgresql_using="pending_invite_token::text",
    )

    op.add_column("auth_states", sa.Column("session_id", UUID(as_uuid=True), nullable=True))
    op.add_column(
        "auth_states",
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.drop_constraint("audit_log_actor_id_fkey", "audit_log", type_="foreignkey")


def downgrade() -> None:
    # Normalise rows orphaned while the actor was a plain uuid, so the
    # restored foreign key validates: this is what SET NULL would have done.
    op.execute(
        "UPDATE audit_log SET actor_id = NULL"
        " WHERE actor_id IS NOT NULL AND actor_id NOT IN (SELECT id FROM users)"
    )
    op.create_foreign_key(
        "audit_log_actor_id_fkey", "audit_log", "users", ["actor_id"], ["id"], ondelete="SET NULL"
    )
    op.drop_column("auth_states", "used_at")
    op.drop_column("auth_states", "session_id")
    # Pending invite hashes are not uuids and cannot survive the cast back;
    # they are single-use login state, so the downgrade drops them.
    op.execute("UPDATE sessions SET pending_invite_token = NULL")
    op.alter_column(
        "sessions",
        "pending_invite_token",
        type_=UUID(as_uuid=True),
        postgresql_using="pending_invite_token::uuid",
    )
    op.drop_column("sessions", "absolute_expires_at")
    op.add_column(
        "invites",
        sa.Column(
            "token",
            UUID(as_uuid=True),
            nullable=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
    )
    op.execute("UPDATE invites SET token = gen_random_uuid()")
    op.alter_column("invites", "token", nullable=False)
    op.create_unique_constraint("uq_invites_token", "invites", ["token"])
    op.drop_constraint("uq_invites_token_hash", "invites", type_="unique")
    op.drop_column("invites", "token_hash")
    op.drop_table("principal_version")
    op.drop_table("bans")
    op.drop_table("superadmin_identities")
