"""Call log: every model call is recorded with tokens and registry cost.

The caller owns the transaction; this module never commits, so request
and job flows keep their one-session commit boundaries.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any
from uuid import UUID

import sqlalchemy as sa
import structlog
from sqlalchemy.engine.cursor import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.llm.registry import MODELS, compute_cost_usd
from app.models.llm import llm_calls

WIPED_REQUEST: dict[str, Any] = {"wiped": True}

_log = structlog.get_logger("model_call")


def endpoint_for(model: str) -> str:
    """The HTTP request one call of this model makes, e.g. `POST https://api.meta.ai/v1/responses`."""
    settings = get_settings()
    spec = MODELS.get(model)
    if spec is not None and spec.provider == "anthropic":
        return f"POST {settings.anthropic_api_base_url}/v1/messages"
    if spec is not None and spec.unit == "audio_hour":
        return f"POST {settings.model_api_base_url}/audio/transcriptions"
    return f"POST {settings.model_api_base_url}/responses"


def emit_model_call(
    *,
    agent: str,
    model: str,
    response_id: str | None,
    status: str,
    input_tokens: int,
    cached_tokens: int,
    output_tokens: int,
    reasoning_tokens: int,
    cost_usd: Decimal,
    duration_ms: int,
    run_id: UUID | None,
    step_key: str | None,
) -> None:
    """One structured stdout line per recorded call: metadata only, never prompt, reply or key."""
    _log.info(
        "model_call",
        agent=agent,
        model=model,
        endpoint=endpoint_for(model),
        response_id=response_id,
        status=status,
        tokens_input=input_tokens,
        tokens_cached=cached_tokens,
        tokens_output=output_tokens,
        tokens_reasoning=reasoning_tokens,
        cost_usd=str(cost_usd),
        duration_ms=duration_ms,
        run_id=str(run_id) if run_id is not None else None,
        step_key=step_key,
    )


def sanitize_json(value: Any) -> Any:
    """Recursively strip NUL bytes (Postgres JSONB rejects \\u0000)."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {str(k).replace("\x00", ""): sanitize_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_json(v) for v in value]
    return value


async def _step_key(session: AsyncSession, step_id: UUID | None) -> str | None:
    """The step key a call belongs to, for the log line."""
    if step_id is None:
        return None
    key = (
        await session.execute(
            sa.text("SELECT step_key FROM run_steps WHERE id = :step"), {"step": str(step_id)}
        )
    ).scalar()
    return str(key) if key is not None else None


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
    emit_model_call(
        agent=agent,
        model=model,
        response_id=response_id,
        status=status,
        input_tokens=input_tokens,
        cached_tokens=cached_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        cost_usd=cost,
        duration_ms=duration_ms,
        run_id=run_id,
        step_key=await _step_key(session, step_id),
    )
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
    row = (
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
            .returning(
                llm_calls.c.agent, llm_calls.c.response_id, llm_calls.c.run_id, llm_calls.c.step_id
            )
        )
    ).one()
    emit_model_call(
        agent=row.agent,
        model=model,
        response_id=row.response_id,
        status=status,
        input_tokens=input_tokens,
        cached_tokens=cached_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        cost_usd=cost,
        duration_ms=duration_ms,
        run_id=row.run_id,
        step_key=await _step_key(session, row.step_id),
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
