"""T03 DTOs: courses, profiles, plan edits, sources, runs. Pure shapes, no logic."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CourseCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    language: str = Field(default="en", pattern="^(en|nb|uk)$")
    scope_tags: list[str] = Field(default_factory=list)


class ProfileRequest(BaseModel):
    methodology_codes: list[str] = Field(min_length=1)
    bloom_level: str = Field(default="understand")
    audience: str = Field(default="")
    dreyfus_start: str = Field(default="novice")
    constraints: list[dict[str, Any]] = Field(default_factory=list)


class PlanEditRequest(BaseModel):
    operations: list[dict[str, Any]] = Field(min_length=1)


class PlanSubmitRequest(BaseModel):
    queries: list[str] = Field(min_length=1)
    idempotency_key: str | None = None


class GenerateSubmitRequest(BaseModel):
    idempotency_key: str | None = None


class SourceFetchRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    course_id: str | None = None
