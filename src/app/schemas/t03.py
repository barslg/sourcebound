"""T03 DTOs: courses, profiles, plan edits, sources, runs. Pure shapes, no logic."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CourseCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    language: str = Field(default="en", pattern="^(en|nb|uk)$")
    scope_tags: list[str] = Field(default_factory=list)


class VisibilityRequest(BaseModel):
    public: bool


class ProfileRequest(BaseModel):
    methodology_codes: list[str] = Field(min_length=1)
    bloom_level: str = Field(default="understand")
    audience: str = Field(default="")
    dreyfus_start: str = Field(default="novice")
    constraints: list[dict[str, Any]] = Field(default_factory=list)


class PlanEditRequest(BaseModel):
    operations: list[dict[str, Any]] = Field(min_length=1)
    base_version: int | None = None


class BlockBriefRequest(BaseModel):
    brief: str = Field(min_length=1, max_length=10000)


class PlanSubmitRequest(BaseModel):
    queries: list[str] = Field(min_length=1)
    idempotency_key: str | None = None


class GenerateSubmitRequest(BaseModel):
    idempotency_key: str | None = None


class BuildSubmitRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    queries: list[str] = Field(default_factory=list)
    cap_usd: Decimal | None = Field(default=None, gt=0, le=1000)
    idempotency_key: str | None = None
    continue_build: bool = Field(default=False, alias="continue")


class SourceFetchRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    course_id: str | None = None


class HumanQueueResolveRequest(BaseModel):
    action: str = Field(pattern="^(approve|dismiss)$")


class TranslateRequest(BaseModel):
    languages: list[str] = Field(min_length=1)
    glossary: dict[str, str] = Field(default_factory=dict)
    idempotency_key: str | None = None


class SourceLanguageRequest(BaseModel):
    language: str = Field(min_length=2, max_length=10)
    passage_id: str | None = None


class CallTreeNode(BaseModel):
    id: str
    kind: str
    label: str
    status: str | None = None
    facts: dict[str, Any] = Field(default_factory=dict)
    children: list[CallTreeNode] = Field(default_factory=list)
    deps: list[str] = Field(default_factory=list)
