"""User, role, invite, and operation controllers (orchestration only)."""

from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa

from app.auth.ctx import RequestContext, require_auth
from app.errors import NotFoundError
from app.rbac.require import require
from app.schemas import t02 as dto


def parse_uuid_or_404(raw: str, *, what: str = "object") -> UUID:
    """Parse a path UUID; malformed values answer 404, never 500."""
    try:
        return UUID(raw)
    except ValueError as exc:
        raise NotFoundError(f"{what} not found") from exc


async def list_users(ctx: RequestContext) -> list[dto.UserResponse]:
    from app.services.users import list_users as list_use_case

    actor = require_auth(ctx)
    require(actor, "user.list")
    return [dto.UserResponse(**row) for row in await list_use_case(ctx.db, actor=actor)]


async def ban_user(ctx: RequestContext, *, user_id: str, reason: str) -> None:
    from app.services.users import ban_user as ban_use_case

    await ban_use_case(
        ctx.db,
        actor=require_auth(ctx),
        user_id=parse_uuid_or_404(user_id, what="user"),
        reason=reason,
    )


async def unban_user(ctx: RequestContext, *, user_id: str) -> None:
    from app.services.users import unban_user as unban_use_case

    await unban_use_case(
        ctx.db, actor=require_auth(ctx), user_id=parse_uuid_or_404(user_id, what="user")
    )


async def delete_user(
    ctx: RequestContext, *, user_id: str, heir_id: str | None, legacy: bool
) -> None:
    from app.services.users import delete_user as delete_use_case

    await delete_use_case(
        ctx.db,
        actor=require_auth(ctx),
        user_id=parse_uuid_or_404(user_id, what="user"),
        heir_id=parse_uuid_or_404(heir_id, what="heir") if heir_id else None,
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
        parse_uuid_or_404(body.user_id, what="user"),
        name,
        scope_tags=body.scope_tags,
        actor=require_auth(ctx),
    )


async def revoke_role(ctx: RequestContext, *, name: str, body: dto.RevokeRequest) -> None:
    from app.rbac.roles import revoke_role as revoke_use_case

    await revoke_use_case(
        ctx.db, parse_uuid_or_404(body.user_id, what="user"), name, actor=require_auth(ctx)
    )


async def create_invite(ctx: RequestContext, *, body: dto.InviteRequest) -> dto.InviteResponse:
    from app.rbac.tags import display_tag
    from app.services.invites import create_invite as create_use_case
    from app.services.invites import hash_invite_token

    actor = require_auth(ctx)
    raw_token = await create_use_case(
        ctx.db,
        actor=actor,
        course_tags=body.course_tags,
        max_uses=body.max_uses,
        expires_at=body.expires_at,
    )
    row = (
        await ctx.db.execute(
            sa.text(
                "SELECT course_tags, max_uses, used_count, expires_at, revoked_at, id"
                " FROM invites WHERE token_hash = :hash"
            ),
            {"hash": hash_invite_token(raw_token)},
        )
    ).one()
    return dto.InviteResponse(
        id=str(row[5]),
        token=raw_token,
        course_tags=[display_tag(t) for t in row[0] or []],
        max_uses=row[1],
        used_count=int(row[2]),
        expires_at=row[3].isoformat() if row[3] else None,
        revoked_at=row[4].isoformat() if row[4] else None,
    )


async def list_invites(ctx: RequestContext) -> list[dto.InviteListItem]:
    from app.services.invites import list_invites as list_use_case

    return [
        dto.InviteListItem(**row) for row in await list_use_case(ctx.db, actor=require_auth(ctx))
    ]


async def revoke_invite(ctx: RequestContext, *, invite_id: str) -> None:
    from app.services.invites import revoke_invite as revoke_use_case

    await revoke_use_case(
        ctx.db,
        actor=require_auth(ctx),
        invite_id=parse_uuid_or_404(invite_id, what="invite"),
    )


async def accept_invite(ctx: RequestContext, *, token: str) -> list[str]:
    from app.services.invites import accept_invite as accept_use_case
    from app.services.invites import hash_invite_token

    actor = require_auth(ctx)
    return await accept_use_case(ctx.db, user_id=actor.user_id, token_hash=hash_invite_token(token))


async def stash_pending_invite(ctx: RequestContext, *, token: str) -> tuple[str, str | None]:
    """Stash an invite id on an anonymous session; return (login_url, fresh_token).

    The invite id (never the raw token) is stored server-side, and the
    login return address points at the course page, so the token appears
    in no row, URL, or header after the accept POST.
    """
    from urllib.parse import quote

    from app.auth.session import create_session, session_id_for_token, set_pending_invite_id
    from app.services.invites import hash_invite_token, invite_details

    invite_id, course_tags = await invite_details(ctx.db, token_hash=hash_invite_token(token))
    fresh_token: str | None = None
    session_id = ctx.login_session.id if ctx.login_session else None
    if session_id is None:
        fresh_token, _ = await create_session(ctx.db, None, ttl_s=ctx.settings.session_ttl_s)
        session_id = await session_id_for_token(ctx.db, fresh_token)
    assert session_id is not None
    await set_pending_invite_id(ctx.db, session_id, str(invite_id))
    course_path = f"/courses/{quote(course_tags[0])}" if course_tags else "/"
    return f"/api/v1/auth/login?provider=google&next={course_path}", fresh_token


async def submit_echo(
    ctx: RequestContext, *, message: str, idempotency_key: str | None = None
) -> str:
    from app.services.operations import submit_echo as submit_use_case
    from app.tasks.jobs import enqueue_operation

    actor = require_auth(ctx)
    database_url = ctx.settings.database_url

    async def _defer(operation_id: UUID) -> None:
        await enqueue_operation(operation_id, database_url)

    operation_id = await submit_use_case(
        ctx.db,
        actor=actor,
        payload={"message": message},
        defer=_defer,
        idempotency_key=idempotency_key,
    )
    return str(operation_id)


async def get_operation(ctx: RequestContext, *, operation_id: str) -> dto.OperationResponse:
    from app.services.operations import get_operation as get_use_case

    found = await get_use_case(
        ctx.db,
        actor=require_auth(ctx),
        operation_id=parse_uuid_or_404(operation_id, what="operation"),
    )
    return dto.OperationResponse(**found)


async def revoke_user_role(ctx: RequestContext, *, user_id: str, name: str) -> None:
    from app.rbac.roles import revoke_role as revoke_use_case

    await revoke_use_case(
        ctx.db, parse_uuid_or_404(user_id, what="user"), name, actor=require_auth(ctx)
    )


async def list_permissions(ctx: RequestContext) -> list[dto.PermissionResponse]:
    from app.rbac.catalogue import CATALOGUE

    require(require_auth(ctx), "role.edit")
    return [dto.PermissionResponse(**entry.__dict__) for entry in CATALOGUE]


async def list_notifications(ctx: RequestContext) -> list[dto.NotificationResponse]:
    from app.services.notifications import list_notifications as list_use_case

    return [
        dto.NotificationResponse(**row)
        for row in await list_use_case(ctx.db, actor=require_auth(ctx))
    ]


async def mark_notification_read(ctx: RequestContext, *, notification_id: str) -> None:
    from app.services.notifications import mark_notification_read as mark_use_case

    await mark_use_case(
        ctx.db,
        actor=require_auth(ctx),
        notification_id=parse_uuid_or_404(notification_id, what="notification"),
    )


async def clear_notifications(ctx: RequestContext) -> int:
    from app.services.notifications import clear_notifications as clear_use_case

    return await clear_use_case(ctx.db, actor=require_auth(ctx))


async def invite_preview(ctx: RequestContext, *, token: str) -> dto.InvitePreview:
    from app.services.invites import hash_invite_token
    from app.services.invites import invite_preview as preview_use_case

    return dto.InvitePreview(**await preview_use_case(ctx.db, token_hash=hash_invite_token(token)))
