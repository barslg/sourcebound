"""Learner and teacher-report DTOs: pure shapes, no logic."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ModuleTestSubmit(BaseModel):
    answers: dict[str, dict[str, Any]] = Field(default_factory=dict)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
