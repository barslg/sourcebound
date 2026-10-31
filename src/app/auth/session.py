"""Backend sessions: opaque tokens in the database, never in JWT form.

The cookie carries a random token; only its SHA-256 hash is stored.
Expiry rolls forward on every authenticated request (rolling expiry
from config). No tokens live in the browser besides this cookie.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class SessionRow:
    """One backend session: owner (possibly anonymous), CSRF, expiry, invite."""

    id: UUID
    user_id: UUID | None
    csrf_token: str
    expires_at: datetime
    pending_invite_token: str | None


def hash_token(token: str) -> str:
    """SHA-256 hex of a session token for storage and lookup."""
    return hashlib.sha256(token.encode()).hexdigest()


def _parse(row: sa.Row[Any]) -> SessionRow:
    mapping = row._mapping
    raw_user: Any = mapping["user_id"]
    raw_expires: Any = mapping["expires_at"]
    raw_pending: Any = mapping["pending_invite_token"]
    return SessionRow(
        id=UUID(str(mapping["id"])),
        user_id=UUID(str(raw_user)) if raw_user is not None else None,
        csrf_token=str(mapping["csrf_token"]),
        expires_at=raw_expires,
        pending_invite_token=str(raw_pending) if raw_pending is not None else None,
    )


async def create_session(
    session: AsyncSession, user_id: UUID | None, *, ttl_s: int = 604800
) -> tuple[str, str]:
    """Mint a session; return the (token, csrf_token) pair for the cookie."""
    token = secrets.token_urlsafe(48)
    csrf_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl_s)
    await session.execute(
        sa.text(
            "INSERT INTO sessions (token_hash, user_id, csrf_token, expires_at)"
            " VALUES (:hash, :user, :csrf, :expires)"
        ),
        {
            "hash": hash_token(token),
            "user": str(user_id) if user_id else None,
            "csrf": csrf_token,
            "expires": expires_at,
        },
    )
    return token, csrf_token


async def load_session(
    session: AsyncSession, token: str, *, ttl_s: int = 604800
) -> SessionRow | None:
    """Look a session up by token; roll its expiry forward when still valid."""
    row = (
        await session.execute(
            sa.text(
                "SELECT id, user_id, csrf_token, expires_at, pending_invite_token"
                " FROM sessions WHERE token_hash = :hash"
            ),
            {"hash": hash_token(token)},
        )
    ).first()
    if row is None:
        return None
    found = _parse(row)
    now = datetime.now(UTC)
    expires = found.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if expires <= now:
        await session.execute(
            sa.text("DELETE FROM sessions WHERE token_hash = :hash"),
            {"hash": hash_token(token)},
        )
        return None
    rolled = now + timedelta(seconds=ttl_s)
    await session.execute(
        sa.text("UPDATE sessions SET expires_at = :expires WHERE id = :sid"),
        {"expires": rolled, "sid": str(found.id)},
    )
    return SessionRow(
        id=found.id,
        user_id=found.user_id,
        csrf_token=found.csrf_token,
        expires_at=rolled,
        pending_invite_token=found.pending_invite_token,
    )


async def destroy_session(session: AsyncSession, token: str) -> None:
    """Delete one session (logout)."""
    await session.execute(
        sa.text("DELETE FROM sessions WHERE token_hash = :hash"),
        {"hash": hash_token(token)},
    )


async def revoke_user_sessions(session: AsyncSession, user_id: UUID) -> None:
    """Delete every session of a user (ban, password-equivalent events)."""
    await session.execute(
        sa.text("DELETE FROM sessions WHERE user_id = :user"), {"user": str(user_id)}
    )


async def set_pending_invite(
    session: AsyncSession, session_id: UUID, invite_token: str | None
) -> None:
    """Stash (or clear) the invite a signed-out visitor accepted."""
    await session.execute(
        sa.text("UPDATE sessions SET pending_invite_token = :token WHERE id = :sid"),
        {"token": invite_token, "sid": str(session_id)},
    )


async def session_id_for_token(session: AsyncSession, token: str) -> UUID | None:
    """The session id behind a token (for rows the caller just created)."""
    row = (
        await session.execute(
            sa.text("SELECT id FROM sessions WHERE token_hash = :hash"),
            {"hash": hash_token(token)},
        )
    ).first()
    return UUID(str(row[0])) if row else None
