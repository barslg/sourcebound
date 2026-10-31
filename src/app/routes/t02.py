"""User, role, and invite routes: parse and validate only."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from app.auth.ctx import RequestContext, get_ctx
from app.controllers import t02 as controller
from app.schemas import t02 as dto

users_router = APIRouter(prefix="/users", tags=["users"])
roles_router = APIRouter(prefix="/roles", tags=["roles"])
invites_router = APIRouter(prefix="/invites", tags=["invites"])


@users_router.get("", response_model=list[dto.UserResponse])
async def list_users(ctx: RequestContext = Depends(get_ctx)) -> list[dto.UserResponse]:  # noqa: B008 - FastAPI dependency injection idiom
    """List the user directory."""
    return await controller.list_users(ctx)


@users_router.post("/{user_id}/ban")
async def ban_user(
    user_id: str,
    body: dto.BanRequest,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, bool]:
    """Ban an account: no re-login, sessions revoked."""
    await controller.ban_user(ctx, user_id=user_id, reason=body.reason)
    return {"ok": True}


@users_router.delete("/{user_id}")
async def delete_user(
    user_id: str,
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


@invites_router.post("", response_model=dto.InviteResponse, status_code=201)
async def create_invite(
    body: dto.InviteRequest,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dto.InviteResponse:
    """Create an invite link on a course or course group."""
    return await controller.create_invite(ctx, body=body)


@invites_router.post("/{token}/revoke")
async def revoke_invite(token: str, ctx: RequestContext = Depends(get_ctx)) -> dict[str, bool]:  # noqa: B008 - FastAPI dependency injection idiom
    """Revoke an invite link."""
    await controller.revoke_invite(ctx, token=token)
    return {"ok": True}
