"""T02 identity, authorisation, and operations tables.

Adds bans and legacy holders plus avatars on users, federated
identities, backend sessions, one-time OIDC states, the audit log,
operations with notifications, invite uses, scope tags on user_roles,
CRUD matrix plus scope tags on roles, and revocation plus course tags
and the granted role on invites. Downgrade removes exactly these
additions; tables from 0001 are untouched.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TEXT, UUID

revision = "0002"
down_revision = "0001"
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
    op.add_column("users", sa.Column("banned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("banned_reason", sa.Text(), nullable=False, server_default=""))
    op.add_column(
        "users",
        sa.Column("is_legacy", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("users", sa.Column("avatar_url", sa.Text(), nullable=False, server_default=""))

    op.create_table(
        "identities",
        sa.Column("id", _uuid_pk(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "user_id", _uuid_pk(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("email", sa.String(320), nullable=False, server_default=""),
        _now(),
        sa.UniqueConstraint("provider", "subject", name="uq_identities_provider_subject"),
        sa.Index("ix_identities_user_id", "user_id"),
    )
    op.create_table(
        "sessions",
        sa.Column("id", _uuid_pk(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "user_id", _uuid_pk(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("csrf_token", sa.Text(), nullable=False),
        sa.Column("pending_invite_token", _uuid_pk(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _now(),
        sa.Index("ix_sessions_user_id", "user_id"),
    )
    op.create_table(
        "auth_states",
        sa.Column("state", sa.Text(), primary_key=True),
        sa.Column("nonce", sa.Text(), nullable=False),
        sa.Column("code_verifier", sa.Text(), nullable=False),
        sa.Column("next_url", sa.Text(), nullable=True),
        _now(),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", _uuid_pk(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "actor_id", _uuid_pk(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("action", sa.String(200), nullable=False),
        sa.Column("entity", sa.String(100), nullable=False, server_default=""),
        sa.Column("entity_id", sa.Text(), nullable=True),
        sa.Column("meta", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        _now(),
        sa.Index("ix_audit_log_actor_id", "actor_id"),
    )
    op.create_table(
        "operations",
        sa.Column("id", _uuid_pk(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("kind", sa.String(100), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("params", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("progress", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("result", JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "actor_id", _uuid_pk(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        _now(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Index("ix_operations_actor_id", "actor_id"),
    )
    op.create_table(
        "notifications",
        sa.Column("id", _uuid_pk(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "user_id", _uuid_pk(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.String(100), nullable=False, server_default=""),
        sa.Column("payload", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        _now(),
        sa.Index("ix_notifications_user_id", "user_id"),
    )
    op.create_table(
        "invite_uses",
        sa.Column("id", _uuid_pk(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "invite_id", _uuid_pk(), sa.ForeignKey("invites.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "user_id", _uuid_pk(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        _now(),
        sa.UniqueConstraint("invite_id", "user_id", name="uq_invite_uses_invite_user"),
    )
    op.add_column(
        "user_roles",
        sa.Column("scope_tags", ARRAY(TEXT()), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "roles",
        sa.Column("crud", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column(
        "roles",
        sa.Column("scope_tags", ARRAY(TEXT()), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column("invites", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "invites",
        sa.Column("course_tags", ARRAY(TEXT()), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "invites",
        sa.Column("granted_role", sa.String(100), nullable=False, server_default="student"),
    )


def downgrade() -> None:
    op.drop_column("invites", "granted_role")
    op.drop_column("invites", "course_tags")
    op.drop_column("invites", "revoked_at")
    op.drop_column("roles", "scope_tags")
    op.drop_column("roles", "crud")
    op.drop_column("user_roles", "scope_tags")
    op.drop_table("invite_uses")
    op.drop_table("notifications")
    op.drop_table("operations")
    op.drop_table("audit_log")
    op.drop_table("auth_states")
    op.drop_table("sessions")
    op.drop_table("identities")
    op.drop_column("users", "avatar_url")
    op.drop_column("users", "is_legacy")
    op.drop_column("users", "banned_reason")
    op.drop_column("users", "banned_at")
