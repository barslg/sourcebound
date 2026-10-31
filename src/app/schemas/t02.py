"""T02 DTOs: auth, users, roles, invites, operations. Pure shapes, no logic."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class MeResponse(BaseModel):
    id: str
    email: str
    display_name: str
    avatar_url: str
    is_superadmin: bool
    roles: list[str]
    permissions: list[str]
    scope_tags: list[str]
    privacy_line: str = ""


class CsrfResponse(BaseModel):
    csrf_token: str


class LoginDoneResponse(BaseModel):
    id: str
    email: str
    display_name: str
    is_new: bool
    applied_invite_scopes: list[str] | None = None
    next_url: str | None = None


class BanRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class UserRoleGrant(BaseModel):
    name: str
    scope_tags: list[str]


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str
    is_superadmin: bool
    is_legacy: bool
    banned_at: str | None
    banned_reason: str
    created_at: str | None
    roles: list[UserRoleGrant] = Field(default_factory=list)


class RoleRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    permission_codes: list[str] = Field(default_factory=list)
    crud: dict[str, list[str]] = Field(default_factory=dict)
    scope_tags: list[str] = Field(default_factory=list)


class RoleUpdateRequest(BaseModel):
    permission_codes: list[str] | None = None
    crud: dict[str, list[str]] | None = None
    scope_tags: list[str] | None = None


class RoleResponse(BaseModel):
    name: str
    permission_codes: list[str]
    crud: dict[str, list[str]]
    scope_tags: list[str]


class AssignRequest(BaseModel):
    user_id: str
    scope_tags: list[str] = Field(default_factory=list)


class RevokeRequest(BaseModel):
    user_id: str


class InviteRequest(BaseModel):
    course_tags: list[str] = Field(min_length=1)
    max_uses: int | None = Field(default=None, ge=1)
    expires_at: datetime | None = None


class InviteResponse(BaseModel):
    token: str
    course_tags: list[str]
    max_uses: int | None
    used_count: int
    expires_at: str | None
    revoked_at: str | None


class InviteUser(BaseModel):
    id: str
    display_name: str


class InviteListItem(BaseModel):
    id: str
    course_tags: list[str]
    course_titles: list[str] = []
    max_uses: int | None
    used_count: int
    expires_at: str | None
    revoked_at: str | None
    created_by: InviteUser | None = None
    accepted_by: list[InviteUser] = []


class InvitePreview(BaseModel):
    course_titles: list[str]
    usable: bool
    reason: str | None


class PermissionResponse(BaseModel):
    code: str
    category: str
    label: str
    description: str


class NotificationResponse(BaseModel):
    id: str
    kind: str
    payload: dict[str, object]
    read: bool
    created_at: str | None


class EchoRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10000)
    idempotency_key: str | None = Field(default=None, max_length=100)


class SubmitResponse(BaseModel):
    operation_id: str


class OperationResponse(BaseModel):
    operation_id: str
    kind: str
    status: str
    progress: float
    result: dict[str, object] | None = None
    error: str | None = None
