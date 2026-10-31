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


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str
    is_superadmin: bool
    is_legacy: bool
    banned_at: str | None
    banned_reason: str
    created_at: str | None


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
    granted_role: str = Field(default="student", max_length=100)


class InviteResponse(BaseModel):
    token: str
    course_tags: list[str]
    max_uses: int | None
    used_count: int
    expires_at: str | None
    revoked_at: str | None


class EchoRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10000)


class SubmitResponse(BaseModel):
    operation_id: str


class OperationResponse(BaseModel):
    operation_id: str
    kind: str
    status: str
    progress: float
    result: dict[str, object] | None = None
    error: str | None = None
