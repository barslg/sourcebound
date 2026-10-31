"""Sign-in use cases: begin a login, finish it, and gate unusable accounts."""

from __future__ import annotations

import secrets
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.oidc import Provider, pkce_pair
from app.errors import AuthError


@dataclass(frozen=True)
class UserRecord:
    """The account row services hand to controllers."""

    id: UUID
    email: str
    display_name: str
    avatar_url: str
    is_superadmin: bool
    is_legacy: bool


@dataclass(frozen=True)
class LoginOutcome:
    """What a finished login produced besides the account itself."""

    is_new: bool
    applied_invite_scopes: tuple[str, ...] | None


def _record(row: sa.Row[Any]) -> UserRecord:
    mapping = row._mapping
    return UserRecord(
        id=UUID(str(mapping["id"])),
        email=str(mapping["email"]),
        display_name=str(mapping["display_name"] or ""),
        avatar_url=str(mapping["avatar_url"] or ""),
        is_superadmin=bool(mapping["is_superadmin"]),
        is_legacy=bool(mapping["is_legacy"]),
    )


async def refuse_if_unusable(session: AsyncSession, user_id: UUID) -> None:
    """Refuse banned and legacy-holder accounts before they get a session."""
    row = (
        await session.execute(
            sa.text("SELECT banned_at, is_legacy FROM users WHERE id = :user"),
            {"user": str(user_id)},
        )
    ).first()
    if row is None:
        raise AuthError("unknown account")
    if row[0] is not None:
        raise AuthError("this account is banned and cannot sign in")
    if bool(row[1]):
        raise AuthError("this legacy account cannot sign in")


async def begin_login(
    session: AsyncSession, provider: Provider, *, next_url: str | None = None
) -> tuple[str, str]:
    """Store a one-time state plus PKCE verifier; return (url, state)."""
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier, challenge = pkce_pair()
    await session.execute(
        sa.text(
            "INSERT INTO auth_states (state, nonce, code_verifier, next_url)"
            " VALUES (:state, :nonce, :verifier, :next)"
        ),
        {"state": state, "nonce": nonce, "verifier": verifier, "next": next_url},
    )
    url = await provider.authorize_url(state=state, nonce=nonce, code_challenge=challenge)
    return url, state


async def finish_login(
    session: AsyncSession,
    provider: Provider,
    *,
    state: str,
    code: str,
    superadmin_emails: Collection[str] = (),
    pending_invite_token: str | None = None,
) -> tuple[UserRecord, LoginOutcome]:
    """Redeem a one-time state plus code; create or refresh the account.

    The first login creates the user with NO roles; later logins update
    name and avatar. Superadmin status is re-derived from config on
    every login. A banned or legacy identity is refused.
    """
    stored = (
        await session.execute(
            sa.text("SELECT nonce, code_verifier, next_url FROM auth_states WHERE state = :state"),
            {"state": state},
        )
    ).first()
    if stored is None:
        raise AuthError("this sign-in attempt already finished or never started")
    await session.execute(sa.text("DELETE FROM auth_states WHERE state = :state"), {"state": state})
    tokens = await provider.exchange_code(code=code, code_verifier=str(stored[1]))
    identity = await provider.verify_id_token(str(tokens["id_token"]), nonce=str(stored[0]))

    linked = (
        await session.execute(
            sa.text("SELECT user_id FROM identities WHERE provider = :p AND subject = :s"),
            {"p": identity.provider, "s": identity.subject},
        )
    ).first()
    is_new = False
    if linked is not None:
        user_id = UUID(str(linked[0]))
    else:
        same_email = (
            await session.execute(
                sa.text("SELECT id FROM users WHERE email = :email"), {"email": identity.email}
            )
        ).first()
        if same_email is not None:
            user_id = UUID(str(same_email[0]))
        else:
            created = (
                await session.execute(
                    sa.text(
                        "INSERT INTO users (email, display_name, avatar_url, is_superadmin)"
                        " VALUES (:email, :name, :avatar, :admin) RETURNING id"
                    ),
                    {
                        "email": identity.email,
                        "name": identity.name[:200],
                        "avatar": identity.avatar,
                        "admin": identity.email in {item.lower() for item in superadmin_emails},
                    },
                )
            ).one()
            user_id = UUID(str(created[0]))
            is_new = True
        await session.execute(
            sa.text(
                "INSERT INTO identities (user_id, provider, subject, email)"
                " VALUES (:user, :provider, :subject, :email)"
                " ON CONFLICT (provider, subject) DO UPDATE SET user_id = EXCLUDED.user_id"
            ),
            {
                "user": str(user_id),
                "provider": identity.provider,
                "subject": identity.subject,
                "email": identity.email,
            },
        )
    await session.execute(
        sa.text(
            "UPDATE users SET display_name = :name, avatar_url = :avatar,"
            " is_superadmin = :admin WHERE id = :user"
        ),
        {
            "name": identity.name[:200],
            "avatar": identity.avatar,
            "admin": identity.email in {item.lower() for item in superadmin_emails},
            "user": str(user_id),
        },
    )
    await refuse_if_unusable(session, user_id)

    applied: tuple[str, ...] | None = None
    if pending_invite_token:
        from app.services.invites import accept_invite

        try:
            pending_uuid = UUID(pending_invite_token)
        except ValueError as exc:
            raise AuthError("this invite link is no longer valid") from exc
        applied = tuple(await accept_invite(session, user_id=user_id, token=pending_uuid))
    row = (
        await session.execute(
            sa.text(
                "SELECT id, email, display_name, avatar_url, is_superadmin, is_legacy"
                " FROM users WHERE id = :user"
            ),
            {"user": str(user_id)},
        )
    ).one()
    return _record(row), LoginOutcome(is_new=is_new, applied_invite_scopes=applied)


async def user_created_at(session: AsyncSession, user_id: UUID) -> datetime | None:
    """Creation timestamp of an account (audit support)."""
    row = (
        await session.execute(
            sa.text("SELECT created_at FROM users WHERE id = :user"), {"user": str(user_id)}
        )
    ).first()
    return row[0] if row else None
