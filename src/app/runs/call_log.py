"""The run's model-call log: what the owner (and a judge) sees of every paid call.

Reads the `llm_calls` ledger by run id only, so a learner or tutor call
(no run) can never appear. Callers: the two run-call routes via the
controller in `app.controllers.t03`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import NotFoundError
from app.llm.calls import endpoint_for
from app.llm.registry import display_name_of

# A call served from the reply store is booked at zero tokens, zero time, zero cost.
CACHED_REPLY_SQL = (
    "(c.status = 'completed' AND c.tokens_input = 0 AND c.tokens_output = 0"
    " AND c.tokens_reasoning = 0 AND c.duration_ms = 0)"
)

# What the detail view may show of a stored request: the prompt, its input and the output format.
REQUEST_KEYS = (
    "model",
    "instructions",
    "input",
    "text",
    "reasoning",
    "max_output_tokens",
    "background",
    "tools",
    "include",
    "query",
    "tool",
    "searches",
    "source_id",
    "segments",
)


def shown_request(request: Any) -> dict[str, Any]:
    """The stored request without cache keys, hashes or the hashed learner identifier."""
    if not isinstance(request, dict):
        return {}
    return {key: request[key] for key in REQUEST_KEYS if key in request}


async def list_run_calls(
    session: AsyncSession, *, run_id: UUID, limit: int, offset: int
) -> dict[str, Any]:
    """Per-agent totals plus one page of this run's calls, newest first."""
    summary_rows = (
        await session.execute(
            sa.text(
                "SELECT c.agent, c.model, COUNT(*), SUM(c.tokens_input), SUM(c.tokens_cached),"
                " SUM(c.tokens_output), SUM(c.tokens_reasoning), SUM(c.cost_usd)"
                " FROM llm_calls c WHERE c.run_id = :run"
                " GROUP BY c.agent, c.model ORDER BY SUM(c.cost_usd) DESC, c.agent, c.model"
            ),
            {"run": str(run_id)},
        )
    ).all()
    total = sum(int(row[2]) for row in summary_rows)
    rows = (
        await session.execute(
            sa.text(
                "SELECT c.id, c.created_at, c.agent, s.step_key, c.model, c.response_id,"
                f" c.status, {CACHED_REPLY_SQL}, c.tokens_input, c.tokens_cached,"
                " c.tokens_output, c.tokens_reasoning, c.cost_usd, c.duration_ms"
                " FROM llm_calls c LEFT JOIN run_steps s ON s.id = c.step_id"
                " WHERE c.run_id = :run ORDER BY c.created_at DESC, c.id DESC"
                " LIMIT :limit OFFSET :offset"
            ),
            {"run": str(run_id), "limit": limit, "offset": offset},
        )
    ).all()
    return {
        "summary": [
            {
                "agent": row[0],
                "model": row[1],
                "display_name": display_name_of(str(row[1])),
                "calls": int(row[2]),
                "tokens_input": int(row[3]),
                "tokens_cached": int(row[4]),
                "tokens_output": int(row[5]),
                "tokens_reasoning": int(row[6]),
                "cost_usd": str(row[7]),
            }
            for row in summary_rows
        ],
        "calls": [
            {
                "id": str(row[0]),
                "created_at": row[1].isoformat(),
                "agent": row[2],
                "step_key": row[3],
                "model": row[4],
                "display_name": display_name_of(str(row[4])),
                "endpoint": endpoint_for(str(row[4])),
                "response_id": row[5],
                "status": row[6],
                "cached_reply": bool(row[7]),
                "tokens_input": int(row[8]),
                "tokens_cached": int(row[9]),
                "tokens_output": int(row[10]),
                "tokens_reasoning": int(row[11]),
                "cost_usd": str(row[12]),
                "duration_ms": int(row[13]),
            }
            for row in rows
        ],
        "total": total,
        "next_offset": offset + len(rows) if offset + len(rows) < total else None,
    }


async def get_run_call(session: AsyncSession, *, run_id: UUID, call_id: UUID) -> dict[str, Any]:
    """One call's request and reply, only when the call belongs to this run."""
    row = (
        await session.execute(
            sa.text(
                "SELECT c.id, c.agent, c.model, c.request, c.response, c.status"
                " FROM llm_calls c WHERE c.id = :call AND c.run_id = :run"
                " AND c.subject_user_id IS NULL"
            ),
            {"call": str(call_id), "run": str(run_id)},
        )
    ).first()
    if row is None:
        raise NotFoundError("call not found")
    return {
        "id": str(row[0]),
        "agent": row[1],
        "model": row[2],
        "display_name": display_name_of(str(row[2])),
        "status": row[5],
        "request": shown_request(row[3]),
        "response": row[4],
    }


async def run_numbers(session: AsyncSession, *, course_id: UUID | None) -> dict[str, Any]:
    """Blocks accepted/held and quotes verified verbatim, for the run's course; None when unknown.

    blocks.status is the verdict the checks and the reader wrote (accepted
    or held). A citation row with a quote exists only after code matched
    that quote verbatim against the stored passage (verified_by = 'code');
    a plan-time link has no quote and is not counted. Held blocks write no
    citation rows, so they never inflate the quote counts.
    """
    unknown: dict[str, Any] = {
        "blocks_accepted": None,
        "blocks_held": None,
        "quotes_verbatim": None,
        "quotes_total": None,
    }
    if course_id is None:
        return unknown
    blocks = (
        await session.execute(
            sa.text(
                "SELECT b.status, COUNT(*) FROM blocks b"
                " JOIN topics t ON t.id = b.topic_id JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :course GROUP BY b.status"
            ),
            {"course": str(course_id)},
        )
    ).all()
    quotes = (
        await session.execute(
            sa.text(
                "SELECT COUNT(*) FILTER (WHERE ci.verified_by = 'code'), COUNT(*)"
                " FROM citations ci JOIN blocks b ON b.current_version_id = ci.block_version_id"
                " JOIN topics t ON t.id = b.topic_id JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :course AND ci.quote <> ''"
            ),
            {"course": str(course_id)},
        )
    ).one()
    by_status = {str(status): int(count) for status, count in blocks}
    if not by_status:
        return unknown
    return {
        "blocks_accepted": by_status.get("accepted", 0),
        "blocks_held": by_status.get("held", 0),
        "quotes_verbatim": int(quotes[0]),
        "quotes_total": int(quotes[1]),
    }
