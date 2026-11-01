"""Sign-in use cases: begin a login, finish it, and gate unusable accounts."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.oidc import Provider, pkce_pair
from app.errors import AuthError, ValidationError

LOGIN_STATE_TTL_S = 600


def normalize_email(email: str) -> str:
    """Lower-cased, trimmed email for ban and pin lookups."""
    return email.strip().lower()


def identity_digest(provider: str, subject: str) -> str:
    """SHA-256 hex identifying one federated identity for the bans table."""
    return hashlib.sha256(f"{provider}\0{subject}".encode()).hexdigest()


def validate_next_url(next_url: str | None) -> str | None:
    """Allow only relative frontend paths; refuse absolute URLs (open redirect)."""
    if next_url is None or next_url == "":
        return None
    if (
        next_url.startswith("/")
        and not next_url.startswith("//")
        and "\\" not in next_url
        and "://" not in next_url
    ):
        return next_url
    raise ValidationError("next_url must be a relative path")


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
    next_url: str | None = None


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


async def is_banned(session: AsyncSession, *, email: str, provider: str, subject: str) -> bool:
    """True when the email or the federated identity sits in the bans table."""
    row = (
        await session.execute(
            sa.text(
                "SELECT 1 FROM bans WHERE email = :email"
                " OR (provider = :provider AND subject_digest = :digest) LIMIT 1"
            ),
            {
                "email": normalize_email(email),
                "provider": provider,
                "digest": identity_digest(provider, subject),
            },
        )
    ).first()
    return row is not None


async def refuse_if_unusable(session: AsyncSession, user_id: UUID) -> None:
    """Refuse banned and legacy-holder accounts before they get a session."""
    row = (
        await session.execute(
            sa.text("SELECT email, banned_at, is_legacy FROM users WHERE id = :user"),
            {"user": str(user_id)},
        )
    ).first()
    if row is None:
        raise AuthError("unknown account")
    if row[1] is not None:
        raise AuthError("this account is banned and cannot sign in")
    if bool(row[2]):
        raise AuthError("this legacy account cannot sign in")
    identities = (
        await session.execute(
            sa.text("SELECT provider, subject FROM identities WHERE user_id = :user"),
            {"user": str(user_id)},
        )
    ).all()
    for provider, subject in identities:
        if await is_banned(
            session, email=str(row[0]), provider=str(provider), subject=str(subject)
        ):
            raise AuthError("this account is banned and cannot sign in")


async def begin_login(
    session: AsyncSession,
    provider: Provider,
    *,
    next_url: str | None = None,
    session_id: UUID | None = None,
) -> tuple[str, str]:
    """Store a one-time state plus PKCE verifier; return (url, state).

    The state is bound to the pre-login session that started it and
    carries a validated relative next_url for the post-login redirect.
    """
    safe_next = validate_next_url(next_url)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier, challenge = pkce_pair()
    await session.execute(
        sa.text(
            "INSERT INTO auth_states (state, nonce, code_verifier, next_url, session_id)"
            " VALUES (:state, :nonce, :verifier, :next, :sid)"
        ),
        {
            "state": state,
            "nonce": nonce,
            "verifier": verifier,
            "next": safe_next,
            "sid": str(session_id) if session_id else None,
        },
    )
    url = await provider.authorize_url(state=state, nonce=nonce, code_challenge=challenge)
    return url, state


async def _resolve_superadmin(
    session: AsyncSession,
    *,
    email: str,
    provider: str,
    subject: str,
    email_verified: bool,
    superadmin_emails: Collection[str],
) -> bool:
    """Superadmin only for a verified email whose subject matches the pinned one.

    The first verified login pins (provider, subject) for the email; a
    later login with a different subject is refused instead of promoted.
    """
    wanted = normalize_email(email) in {item.lower() for item in superadmin_emails}
    if not wanted:
        return False
    if not email_verified:
        raise AuthError("a verified email address is required for this account")
    pinned = (
        await session.execute(
            sa.text("SELECT provider, subject FROM superadmin_identities WHERE email = :email"),
            {"email": normalize_email(email)},
        )
    ).first()
    if pinned is None:
        await session.execute(
            sa.text(
                "INSERT INTO superadmin_identities (email, provider, subject)"
                " VALUES (:email, :provider, :subject)"
            ),
            {"email": normalize_email(email), "provider": provider, "subject": subject},
        )
        return True
    if str(pinned[0]) != provider or str(pinned[1]) != subject:
        raise AuthError("this sign-in does not match the known administrator identity")
    return True


async def finish_login(
    session: AsyncSession,
    provider: Provider,
    *,
    state: str,
    code: str,
    superadmin_emails: Collection[str] = (),
    pending_invite_id: str | None = None,
    expected_session_id: UUID | None = None,
) -> tuple[UserRecord, LoginOutcome]:
    """Redeem a one-time state plus code; create or refresh the account.

    The state must be fresh (10-minute TTL) and bound to the pre-login
    session that started it. Accounts are keyed only by (provider,
    subject): an unknown subject whose email already exists is refused
    and never linked. The first login creates the user with NO roles;
    later logins update name and avatar. A banned or legacy identity,
    or a banned email, is refused before any account is created.
    """
    stored = (
        await session.execute(
            sa.text(
                "SELECT nonce, code_verifier, next_url, session_id, created_at"
                " FROM auth_states WHERE state = :state"
            ),
            {"state": state},
        )
    ).first()
    if stored is None:
        raise AuthError("this sign-in attempt already finished or never started")
    created_at = stored[4]
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    if (datetime.now(UTC) - created_at).total_seconds() > LOGIN_STATE_TTL_S:
        await session.execute(
            sa.text("DELETE FROM auth_states WHERE state = :state"), {"state": state}
        )
        raise AuthError("this sign-in attempt expired; please try again")
    bound = str(stored[3]) if stored[3] is not None else None
    if bound is not None and (expected_session_id is None or str(expected_session_id) != bound):
        raise AuthError("this sign-in attempt was started in another session")
    await session.execute(sa.text("DELETE FROM auth_states WHERE state = :state"), {"state": state})
    next_url = validate_next_url(str(stored[2])) if stored[2] else None
    tokens = await provider.exchange_code(code=code, code_verifier=str(stored[1]))
    identity = await provider.verify_id_token(str(tokens["id_token"]), nonce=str(stored[0]))

    if await is_banned(
        session, email=identity.email, provider=identity.provider, subject=identity.subject
    ):
        raise AuthError("this account is banned and cannot sign in")

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
            raise AuthError("this sign-in does not match the known account for this email")
        created = (
            await session.execute(
                sa.text(
                    "INSERT INTO users (email, display_name, avatar_url, is_superadmin)"
                    " VALUES (:email, :name, :avatar, false) RETURNING id"
                ),
                {
                    "email": identity.email,
                    "name": identity.name[:200],
                    "avatar": identity.avatar,
                },
            )
        ).one()
        user_id = UUID(str(created[0]))
        is_new = True
        await session.execute(
            sa.text(
                "INSERT INTO identities (user_id, provider, subject, email)"
                " VALUES (:user, :provider, :subject, :email)"
            ),
            {
                "user": str(user_id),
                "provider": identity.provider,
                "subject": identity.subject,
                "email": identity.email,
            },
        )
    is_admin = await _resolve_superadmin(
        session,
        email=identity.email,
        provider=identity.provider,
        subject=identity.subject,
        email_verified=identity.email_verified,
        superadmin_emails=superadmin_emails,
    )
    await session.execute(
        sa.text(
            "UPDATE users SET display_name = :name, avatar_url = :avatar,"
            " is_superadmin = :admin WHERE id = :user"
        ),
        {
            "name": identity.name[:200],
            "avatar": identity.avatar,
            "admin": is_admin,
            "user": str(user_id),
        },
    )
    await refuse_if_unusable(session, user_id)

    applied: tuple[str, ...] | None = None
    if pending_invite_id:
        from app.services.invites import accept_invite_by_id

        try:
            stashed_id = UUID(pending_invite_id)
        except ValueError:
            stashed_id = None
        if stashed_id is not None:
            applied = tuple(
                await accept_invite_by_id(session, user_id=user_id, invite_id=stashed_id)
            )
    row = (
        await session.execute(
            sa.text(
                "SELECT id, email, display_name, avatar_url, is_superadmin, is_legacy"
                " FROM users WHERE id = :user"
            ),
            {"user": str(user_id)},
        )
    ).one()
    return _record(row), LoginOutcome(
        is_new=is_new, applied_invite_scopes=applied, next_url=next_url
    )
