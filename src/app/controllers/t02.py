"""User, role, invite, and operation controllers (orchestration only)."""

from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa

from app.auth.ctx import RequestContext, require_auth
from app.rbac.require import require
from app.schemas import t02 as dto


async def list_users(ctx: RequestContext) -> list[dto.UserResponse]:
    from app.services.users import list_users as list_use_case

    actor = require_auth(ctx)
    require(actor, "user.list")
    return [dto.UserResponse(**row) for row in await list_use_case(ctx.db)]


async def ban_user(ctx: RequestContext, *, user_id: str, reason: str) -> None:
    from app.services.users import ban_user as ban_use_case

    await ban_use_case(ctx.db, actor=require_auth(ctx), user_id=UUID(user_id), reason=reason)


async def delete_user(
    ctx: RequestContext, *, user_id: str, heir_id: str | None, legacy: bool
) -> None:
    from app.services.users import delete_user as delete_use_case

    await delete_use_case(
        ctx.db,
        actor=require_auth(ctx),
        user_id=UUID(user_id),
        heir_id=UUID(heir_id) if heir_id else None,
        legacy=legacy,
    )


async def list_roles(ctx: RequestContext) -> list[dto.RoleResponse]:
    from app.rbac.roles import list_roles as list_use_case

    actor = require_auth(ctx)
    require(actor, "role.edit")
    return [dto.RoleResponse(**row) for row in await list_use_case(ctx.db)]


async def create_role(ctx: RequestContext, *, body: dto.RoleRequest) -> dto.RoleResponse:
    from app.rbac.roles import create_role as create_use_case

    actor = require_auth(ctx)
    await create_use_case(
        ctx.db,
        name=body.name,
        permission_codes=body.permission_codes,
        crud=body.crud,
        scope_tags=body.scope_tags,
        actor=actor,
    )
    roles = await list_roles(ctx)
    return next(role for role in roles if role.name == body.name)


async def update_role(ctx: RequestContext, *, name: str, body: dto.RoleUpdateRequest) -> None:
    from app.rbac.roles import update_role as update_use_case

    await update_use_case(
        ctx.db,
        name=name,
        permission_codes=body.permission_codes,
        crud=body.crud,
        scope_tags=body.scope_tags,
        actor=require_auth(ctx),
    )


async def delete_role(ctx: RequestContext, *, name: str) -> None:
    from app.rbac.roles import delete_role as delete_use_case

    await delete_use_case(ctx.db, name=name, actor=require_auth(ctx))


async def assign_role(ctx: RequestContext, *, name: str, body: dto.AssignRequest) -> None:
    from app.rbac.roles import assign_role as assign_use_case

    await assign_use_case(
        ctx.db,
        UUID(body.user_id),
        name,
        scope_tags=body.scope_tags,
        actor=require_auth(ctx),
    )


async def revoke_role(ctx: RequestContext, *, name: str, body: dto.RevokeRequest) -> None:
    from app.rbac.roles import revoke_role as revoke_use_case

    await revoke_use_case(ctx.db, UUID(body.user_id), name, actor=require_auth(ctx))


async def create_invite(ctx: RequestContext, *, body: dto.InviteRequest) -> dto.InviteResponse:
    from app.services.invites import create_invite as create_use_case

    actor = require_auth(ctx)
    token = await create_use_case(
        ctx.db,
        actor=actor,
        course_tags=body.course_tags,
        max_uses=body.max_uses,
        expires_at=body.expires_at,
        granted_role=body.granted_role,
    )
    row = (
        await ctx.db.execute(
            sa.text(
                "SELECT token, course_tags, max_uses, used_count, expires_at, revoked_at"
                " FROM invites WHERE token = :token"
            ),
            {"token": str(token)},
        )
    ).one()
    return dto.InviteResponse(
        token=str(row[0]),
        course_tags=list(row[1] or []),
        max_uses=row[2],
        used_count=int(row[3]),
        expires_at=row[4].isoformat() if row[4] else None,
        revoked_at=row[5].isoformat() if row[5] else None,
    )


async def revoke_invite(ctx: RequestContext, *, token: str) -> None:
    from app.services.invites import revoke_invite as revoke_use_case

    await revoke_use_case(ctx.db, actor=require_auth(ctx), token=UUID(token))


async def accept_invite(ctx: RequestContext, *, token: str) -> list[str]:
    from app.services.invites import accept_invite as accept_use_case

    actor = require_auth(ctx)
    return await accept_use_case(ctx.db, user_id=actor.user_id, token=UUID(token))


async def submit_echo(ctx: RequestContext, *, message: str) -> str:
    from app.services.operations import submit_echo as submit_use_case
    from app.tasks.jobs import enqueue_operation

    actor = require_auth(ctx)
    database_url = ctx.settings.database_url

    async def _defer(operation_id: UUID) -> None:
        await enqueue_operation(operation_id, database_url)

    operation_id = await submit_use_case(
        ctx.db, actor=actor, payload={"message": message}, defer=_defer
    )
    return str(operation_id)


async def get_operation(ctx: RequestContext, *, operation_id: str) -> dto.OperationResponse:
    from app.services.operations import get_operation as get_use_case

    found = await get_use_case(ctx.db, actor=require_auth(ctx), operation_id=UUID(operation_id))
    return dto.OperationResponse(**found)
