"""Backend sessions: opaque tokens in the database, never in JWT form.

The cookie carries a random token; only its SHA-256 hash is stored.
Expiry rolls forward on every authenticated request (rolling expiry
from config). No tokens live in the browser besides this cookie.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import Response
from sqlalchemy.ext.asyncio import AsyncSession

ABSOLUTE_SESSION_TTL_S = 30 * 24 * 3600


@dataclass(frozen=True)
class SessionRow:
    """One backend session: owner (possibly anonymous), CSRF, expiry, invite."""

    id: UUID
    user_id: UUID | None
    csrf_token: str
    expires_at: datetime
    pending_invite_id: str | None
    absolute_expires_at: datetime | None = None


def hash_token(token: str) -> str:
    """SHA-256 hex of a session token for storage and lookup."""
    return hashlib.sha256(token.encode()).hexdigest()


def sign_session_token(token: str, *, secret: str) -> str:
    """Append an HMAC signature so cookie forgery needs the session secret."""
    signature = hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()
    return f"{token}.{signature}"


def parse_session_cookie(value: str, *, secret: str | None) -> str | None:
    """Recover the raw token from a cookie value; None when the signature is bad."""
    if not secret:
        return value or None
    token, dot, signature = value.partition(".")
    if not dot or not token or not signature:
        return None
    expected = hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    return token


def cookie_name_for(app_env: str, base_name: str) -> str:
    """__Host- prefixed cookie outside local so the browser locks it to HTTPS."""
    if app_env == "local":
        return base_name
    bare = base_name.removeprefix("__Host-")
    return f"__Host-{bare}"


def cookie_secure_for(app_env: str, configured: bool) -> bool:
    """Secure cookies everywhere except local development."""
    return True if app_env != "local" else configured


def cookie_value_for(token: str, secret: str | None) -> str:
    """The cookie value for a fresh token: signed whenever a secret is set."""
    return sign_session_token(token, secret=secret) if secret else token


def set_session_cookie(
    response: Response,
    *,
    app_env: str,
    cookie_name: str,
    secret: str | None,
    token: str,
    ttl_s: int,
    secure_configured: bool = False,
) -> None:
    """Attach the backend session cookie: HttpOnly, Lax, Secure outside local."""
    response.set_cookie(
        cookie_name_for(app_env, cookie_name),
        cookie_value_for(token, secret),
        httponly=True,
        samesite="lax",
        secure=cookie_secure_for(app_env, secure_configured),
        max_age=ttl_s,
        path="/",
    )


def _parse(row: sa.Row[Any]) -> SessionRow:
    mapping = row._mapping
    raw_user: Any = mapping["user_id"]
    raw_expires: Any = mapping["expires_at"]
    raw_pending: Any = mapping["pending_invite_id"]
    raw_absolute: Any = mapping["absolute_expires_at"]
    return SessionRow(
        id=UUID(str(mapping["id"])),
        user_id=UUID(str(raw_user)) if raw_user is not None else None,
        csrf_token=str(mapping["csrf_token"]),
        expires_at=raw_expires,
        pending_invite_id=str(raw_pending) if raw_pending is not None else None,
        absolute_expires_at=raw_absolute,
    )


async def create_session(
    session: AsyncSession,
    user_id: UUID | None,
    *,
    ttl_s: int = 604800,
    absolute_ttl_s: int = ABSOLUTE_SESSION_TTL_S,
) -> tuple[str, str]:
    """Mint a session; return the (token, csrf_token) pair for the cookie.

    Rolling expiry extends on use; the absolute expiry never moves, so a
    session always ends even under continuous use.
    """
    token = secrets.token_urlsafe(48)
    csrf_token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    await session.execute(
        sa.text(
            "INSERT INTO sessions (token_hash, user_id, csrf_token, expires_at,"
            " absolute_expires_at)"
            " VALUES (:hash, :user, :csrf, :expires, :absolute)"
        ),
        {
            "hash": hash_token(token),
            "user": str(user_id) if user_id else None,
            "csrf": csrf_token,
            "expires": now + timedelta(seconds=ttl_s),
            "absolute": now + timedelta(seconds=absolute_ttl_s),
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
                "SELECT id, user_id, csrf_token, expires_at, pending_invite_id,"
                " absolute_expires_at FROM sessions WHERE token_hash = :hash"
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
    absolute = found.absolute_expires_at
    if absolute is not None and absolute.tzinfo is None:
        absolute = absolute.replace(tzinfo=UTC)
    if expires <= now or (absolute is not None and absolute <= now):
        await session.execute(
            sa.text("DELETE FROM sessions WHERE token_hash = :hash"),
            {"hash": hash_token(token)},
        )
        return None
    rolled = now + timedelta(seconds=ttl_s)
    if absolute is not None and rolled > absolute:
        rolled = absolute
    await session.execute(
        sa.text("UPDATE sessions SET expires_at = :expires WHERE id = :sid"),
        {"expires": rolled, "sid": str(found.id)},
    )
    return SessionRow(
        id=found.id,
        user_id=found.user_id,
        csrf_token=found.csrf_token,
        expires_at=rolled,
        pending_invite_id=found.pending_invite_id,
        absolute_expires_at=absolute,
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


async def set_pending_invite_id(
    session: AsyncSession, session_id: UUID, invite_id: str | None
) -> None:
    """Stash (or clear) the invite id a signed-out visitor accepted.

    The id is stored, never the raw token: the token appears in no
    session row, URL, or header after the accept POST.
    """
    await session.execute(
        sa.text("UPDATE sessions SET pending_invite_id = :invite WHERE id = :sid"),
        {"invite": invite_id, "sid": str(session_id)},
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
