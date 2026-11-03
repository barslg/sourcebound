"""Put the server's MODEL_API_KEY on file for one user, encrypted, as if typed on the account page.

The stand runs on the owner's own key, so the account page must show it as the
user's key rather than a hidden server fallback. Reads MODEL_API_KEY from the
environment or .env through Settings, never prints it, and refuses when the
user does not exist.

Usage: python scripts/seed_user_key.py --email <user email>
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from app.config import get_settings  # noqa: E402
from app.services.model_keys import key_status, set_key  # noqa: E402


async def seed(email: str) -> int:
    """Store the server key for the user; 0 when stored, 1 when nothing could be done."""
    settings = get_settings()
    secret = settings.model_api_key
    if secret is None or not secret.get_secret_value():
        print("MODEL_API_KEY is not set; nothing to seed")
        return 1
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            row = (
                await session.execute(
                    sa.text("SELECT id FROM users WHERE lower(email) = lower(:email)"),
                    {"email": email},
                )
            ).first()
            if row is None:
                print(f"no user with email {email}")
                return 1
            user_id = UUID(str(row[0]))
            if (await key_status(session, user_id))["is_set"]:
                print(f"{email}: a key is already on file, left as is")
                return 0
            status = await set_key(session, settings, user_id, secret.get_secret_value())
            await session.commit()
            print(f"{email}: key on file, ending in …{status['hint']}")
            return 0
    finally:
        await engine.dispose()


def main(argv: list[str]) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    args = parser.parse_args(argv)
    return asyncio.run(seed(args.email))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
