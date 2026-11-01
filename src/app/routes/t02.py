"""User, role, and invite routes: parse and validate only."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.auth.ctx import RequestContext, get_ctx
from app.controllers import t02 as controller
from app.routes.ids import invite_id_param, notification_id_param, user_id_param
from app.schemas import t02 as dto

users_router = APIRouter(prefix="/users", tags=["users"])
roles_router = APIRouter(prefix="/roles", tags=["roles"])
invites_router = APIRouter(prefix="/invites", tags=["invites"])
permissions_router = APIRouter(prefix="/permissions", tags=["permissions"])
notifications_router = APIRouter(prefix="/notifications", tags=["notifications"])


@users_router.get("", response_model=list[dto.UserResponse])
async def list_users(ctx: RequestContext = Depends(get_ctx)) -> list[dto.UserResponse]:  # noqa: B008 - FastAPI dependency injection idiom
    """List the user directory."""
    return await controller.list_users(ctx)


@users_router.post("/{user_id}/ban")
async def ban_user(
    user_id: Annotated[str, Depends(user_id_param)],
    body: dto.BanRequest,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, bool]:
    """Ban an account: no re-login, sessions revoked."""
    await controller.ban_user(ctx, user_id=user_id, reason=body.reason)
    return {"ok": True}


@users_router.post("/{user_id}/unban")
async def unban_user(
    user_id: str,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, bool]:
    """Lift a ban: sign-in works again."""
    await controller.unban_user(ctx, user_id=user_id)
    return {"ok": True}


@users_router.delete("/{user_id}")
async def delete_user(
    user_id: Annotated[str, Depends(user_id_param)],
    heir_id: str | None = None,
    legacy: bool = False,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, bool]:
    """Delete an account, moving ownership to an heir or keeping a legacy holder."""
    await controller.delete_user(ctx, user_id=user_id, heir_id=heir_id, legacy=legacy)
    return {"ok": True}


@roles_router.get("", response_model=list[dto.RoleResponse])
async def list_roles(ctx: RequestContext = Depends(get_ctx)) -> list[dto.RoleResponse]:  # noqa: B008 - FastAPI dependency injection idiom
    """List every role with its grants."""
    return await controller.list_roles(ctx)


@roles_router.post("", response_model=dto.RoleResponse, status_code=201)
async def create_role(
    body: dto.RoleRequest,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dto.RoleResponse:
    """Create a role; unknown codes and escalation are refused."""
    return await controller.create_role(ctx, body=body)


@roles_router.put("/{name}")
async def update_role(
    name: str,
    body: dto.RoleUpdateRequest,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, bool]:
    """Change a role's grants, matrix, or default scope."""
    await controller.update_role(ctx, name=name, body=body)
    return {"ok": True}


@roles_router.delete("/{name}", status_code=204)
async def delete_role(name: str, ctx: RequestContext = Depends(get_ctx)) -> Response:  # noqa: B008 - FastAPI dependency injection idiom
    """Delete a role."""
    await controller.delete_role(ctx, name=name)
    return Response(status_code=204)


@roles_router.post("/{name}/assign")
async def assign_role(
    name: str,
    body: dto.AssignRequest,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, bool]:
    """Grant a role scoped to tags."""
    await controller.assign_role(ctx, name=name, body=body)
    return {"ok": True}


@roles_router.post("/{name}/revoke")
async def revoke_role(
    name: str,
    body: dto.RevokeRequest,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, bool]:
    """Remove a role grant from a user."""
    await controller.revoke_role(ctx, name=name, body=body)
    return {"ok": True}


@users_router.delete("/{user_id}/roles/{name}")
async def revoke_user_role(
    user_id: Annotated[str, Depends(user_id_param)],
    name: str,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, bool]:
    """Remove one role grant from a user."""
    await controller.revoke_user_role(ctx, user_id=user_id, name=name)
    return {"ok": True}


@invites_router.get("", response_model=list[dto.InviteListItem])
async def list_invites(ctx: RequestContext = Depends(get_ctx)) -> list[dto.InviteListItem]:  # noqa: B008 - FastAPI dependency injection idiom
    """List invite links visible in the caller's scope."""
    return await controller.list_invites(ctx)


@invites_router.post("", response_model=dto.InviteResponse, status_code=201)
async def create_invite(
    body: dto.InviteRequest,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dto.InviteResponse:
    """Create an invite link on a course or course group."""
    return await controller.create_invite(ctx, body=body)


@invites_router.post("/{invite_id}/revoke")
async def revoke_invite(
    invite_id: Annotated[str, Depends(invite_id_param)],
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, bool]:
    """Revoke an invite link by id (what GET /invites returns)."""
    await controller.revoke_invite(ctx, invite_id=invite_id)
    return {"ok": True}


@permissions_router.get("", response_model=list[dto.PermissionResponse])
async def list_permissions(
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> list[dto.PermissionResponse]:
    """The permission catalogue with categories, for the roles editor."""
    return await controller.list_permissions(ctx)


@notifications_router.get("", response_model=list[dto.NotificationResponse])
async def list_notifications(
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> list[dto.NotificationResponse]:
    """The caller's notifications, newest first, with a read flag."""
    return await controller.list_notifications(ctx)


@notifications_router.post("/{notification_id}/read")
async def mark_notification_read(
    notification_id: Annotated[str, Depends(notification_id_param)],
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, bool]:
    """Mark one of the caller's notifications as read."""
    await controller.mark_notification_read(ctx, notification_id=notification_id)
    return {"ok": True}
