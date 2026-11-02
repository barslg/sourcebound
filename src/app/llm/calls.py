"""Call log: every model call is recorded with tokens and registry cost.

The caller owns the transaction; this module never commits, so request
and job flows keep their one-session commit boundaries.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.engine.cursor import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.registry import compute_cost_usd
from app.models.llm import llm_calls

WIPED_REQUEST: dict[str, Any] = {"wiped": True}


def sanitize_json(value: Any) -> Any:
    """Recursively strip NUL bytes (Postgres JSONB rejects \\u0000)."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {str(k).replace("\x00", ""): sanitize_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_json(v) for v in value]
    return value


async def log_call(
    session: AsyncSession,
    *,
    agent: str,
    model: str,
    prompt_version: str,
    input_tokens: int,
    cached_tokens: int,
    output_tokens: int,
    duration_ms: int,
    request: dict[str, Any],
    response: dict[str, Any] | None,
    run_id: UUID | None = None,
    step_id: UUID | None = None,
    reasoning_tokens: int = 0,
    response_id: str | None = None,
    status: str = "completed",
    variant: int = 0,
    subject_user_id: UUID | None = None,
    extra_cost_usd: Decimal | None = None,
) -> UUID:
    """Insert one llm_calls row with cost from the registry; return its id.

    `extra_cost_usd` adds a per-unit charge the token price does not
    cover (a web search billed per query beside its tokens).
    """
    cost = compute_cost_usd(model, input_tokens, cached_tokens, output_tokens, reasoning_tokens)
    if extra_cost_usd is not None:
        cost = cost + extra_cost_usd
    result = await session.execute(
        llm_calls.insert()
        .values(
            id=uuid.uuid4(),
            agent=agent,
            model=model,
            prompt_version=prompt_version,
            tokens_input=input_tokens,
            tokens_cached=cached_tokens,
            tokens_output=output_tokens,
            tokens_reasoning=reasoning_tokens,
            cost_usd=cost,
            duration_ms=duration_ms,
            request=sanitize_json(request),
            response=sanitize_json(response),
            response_id=response_id,
            status=status,
            variant=variant,
            subject_user_id=subject_user_id,
            run_id=run_id,
            step_id=step_id,
        )
        .returning(llm_calls.c.id)
    )
    call_id = result.scalar()
    assert isinstance(call_id, uuid.UUID)
    return call_id


async def complete_background_call(
    session: AsyncSession,
    *,
    call_id: UUID,
    input_tokens: int,
    cached_tokens: int,
    output_tokens: int,
    reasoning_tokens: int,
    model: str,
    duration_ms: int,
    response: dict[str, Any] | None,
    status: str = "completed",
) -> None:
    """Flip a pending background row to completed (or rejected) with usage and registry cost."""
    cost = compute_cost_usd(model, input_tokens, cached_tokens, output_tokens, reasoning_tokens)
    await session.execute(
        llm_calls.update()
        .where(llm_calls.c.id == call_id)
        .values(
            status=status,
            tokens_input=input_tokens,
            tokens_cached=cached_tokens,
            tokens_output=output_tokens,
            tokens_reasoning=reasoning_tokens,
            cost_usd=cost,
            duration_ms=duration_ms,
            response=sanitize_json(response),
        )
    )


async def wipe_subject_calls(session: AsyncSession, subject_user_id: UUID) -> int:
    """Erase learner text for one subject, keeping tokens and cost for spend.

    Returns the number of rows wiped. The subject link is cleared so the
    rows no longer identify anyone; cost and token counts stay intact.
    """
    result = await session.execute(
        llm_calls.update()
        .where(llm_calls.c.subject_user_id == subject_user_id)
        .values(request=WIPED_REQUEST, response=None, subject_user_id=None)
    )
    assert isinstance(result, CursorResult)
    return int(result.rowcount or 0)
