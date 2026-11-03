"""Per-user Meta API keys: stored encrypted, used for the runs the user starts.

An author adds their own key on the account page; every paid operation
they start (plan, generate, translate, resume) and every tutor question
on a course they own is billed to that key. The key leaves this module
only inside a Settings copy handed to the provider; it is never returned
to a client and never logged. Callers: controllers/auth.py (account
page), services/operations.py (runs), controllers/learn.py (tutor).
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import ConfigurationError, ValidationError

MIN_KEY_CHARS = 16
MAX_KEY_CHARS = 512
NO_KEY_MESSAGE = "Add your Meta API key on your account page before running this."


def _fernet(settings: Settings) -> Fernet:
    secret = settings.model_key_secret
    if secret is None or not secret.get_secret_value():
        raise ConfigurationError("MODEL_KEY_SECRET is not set")
    digest = hashlib.sha256(secret.get_secret_value().encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _clean(raw: str) -> str:
    key = raw.strip()
    if len(key) < MIN_KEY_CHARS or len(key) > MAX_KEY_CHARS or any(c.isspace() for c in key):
        raise ValidationError("that does not look like a Meta API key")
    return key


async def key_status(session: AsyncSession, user_id: UUID) -> dict[str, Any]:
    """Whether the user has a key, and its last four characters; never the key."""
    row = (
        await session.execute(
            sa.text("SELECT hint, updated_at FROM user_model_keys WHERE user_id = :user"),
            {"user": str(user_id)},
        )
    ).first()
    if row is None:
        return {"is_set": False, "hint": "", "updated_at": None}
    return {"is_set": True, "hint": str(row[0]), "updated_at": row[1]}


async def set_key(
    session: AsyncSession, settings: Settings, user_id: UUID, raw: str
) -> dict[str, Any]:
    """Store (or replace) the user's key encrypted; returns the status."""
    key = _clean(raw)
    ciphertext = _fernet(settings).encrypt(key.encode("utf-8")).decode("ascii")
    await session.execute(
        sa.text(
            "INSERT INTO user_model_keys (user_id, ciphertext, hint, updated_at)"
            " VALUES (:user, :cipher, :hint, now())"
            " ON CONFLICT (user_id) DO UPDATE SET ciphertext = EXCLUDED.ciphertext,"
            " hint = EXCLUDED.hint, updated_at = now()"
        ),
        {"user": str(user_id), "cipher": ciphertext, "hint": key[-4:]},
    )
    return await key_status(session, user_id)


async def delete_key(session: AsyncSession, user_id: UUID) -> dict[str, Any]:
    """Forget the user's key; runs they start afterwards are refused until a new one."""
    await session.execute(
        sa.text("DELETE FROM user_model_keys WHERE user_id = :user"), {"user": str(user_id)}
    )
    return await key_status(session, user_id)


async def _decrypted(session: AsyncSession, settings: Settings, user_id: UUID) -> str | None:
    row = (
        await session.execute(
            sa.text("SELECT ciphertext FROM user_model_keys WHERE user_id = :user"),
            {"user": str(user_id)},
        )
    ).first()
    if row is None:
        return None
    try:
        return _fernet(settings).decrypt(str(row[0]).encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ConfigurationError(
            "the stored key cannot be read with this MODEL_KEY_SECRET"
        ) from exc


async def settings_for_user(
    session: AsyncSession, settings: Settings, user_id: UUID | None
) -> Settings:
    """Settings whose model key is the user's own.

    Without a user key: the replay ledger and the server key stay valid
    only in local and test environments (the mock model, the stand);
    elsewhere the run is refused with the account-page message.
    """
    key = await _decrypted(session, settings, user_id) if user_id is not None else None
    if key is not None:
        return settings.model_copy(update={"model_api_key": SecretStr(key)})
    if settings.model_replay_ledger:
        return settings
    server_key = settings.model_api_key
    if settings.app_env in ("local", "test") and server_key and server_key.get_secret_value():
        return settings
    raise ConfigurationError(NO_KEY_MESSAGE)


async def settings_for_course_owner(
    session: AsyncSession, settings: Settings, course_id: UUID
) -> Settings:
    """Settings whose model key is the course owner's (the tutor is billed to the author)."""
    row = (
        await session.execute(
            sa.text("SELECT owner_id FROM courses WHERE id = :course"), {"course": str(course_id)}
        )
    ).first()
    owner = UUID(str(row[0])) if row is not None and row[0] is not None else None
    return await settings_for_user(session, settings, owner)
