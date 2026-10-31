"""Startup work: catalogue sync plus the administrator rewrite."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.rbac.catalogue import ensure_admin_role, sync_catalogue


async def startup_sync(session: AsyncSession) -> None:
    """Sync the permission catalogue and rewrite the administrator role.

    Runs at startup in lifespan; a new permission is granted to no
    existing non-admin role by construction of sync_catalogue().
    """
    await sync_catalogue(session)
    await ensure_admin_role(session)
