"""Estimator: code only, a forecast that is never a limit.

Blocks times calls times tokens through the registry's dated prices
plus a per-call time model. No model call, no quotas, no enforcement.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.llm.registry import STEPS, compute_cost_usd

SECONDS_PER_CALL = 8.0
TOKENS_PER_BLOCK_CALL = 1500


def forecast(plan: dict[str, Any]) -> dict[str, Any]:
    """Cost/time forecast for one plan's estimate inputs.

    plan carries blocks, calls, and tokens from the architect; calls
    default to two model calls per block (draft plus check) and tokens
    to the per-block default when the architect omits them.
    """
    blocks = max(int(plan.get("blocks", 0)), 0)
    calls = int(plan.get("calls", 0)) or blocks * 2
    tokens = int(plan.get("tokens", 0)) or calls * TOKENS_PER_BLOCK_CALL
    per_call_tokens = tokens // calls if calls else 0
    total_usd = Decimal("0")
    for step in ("block_teach", "block_exercise"):
        spec = STEPS[step]
        share, remainder = divmod(calls, 2)
        step_calls = share + (remainder if step == "block_teach" else 0)
        total_usd += Decimal(step_calls) * compute_cost_usd(
            spec.model, per_call_tokens, 0, spec.max_output_tokens // 2
        )
    return {
        "blocks": blocks,
        "calls": calls,
        "tokens": tokens,
        "cost_usd": str(total_usd.quantize(Decimal("0.000001"))),
        "seconds": round(calls * SECONDS_PER_CALL, 1),
        "note": "Forecast, not a limit.",
    }
