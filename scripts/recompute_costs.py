"""Recompute llm_calls.cost_usd from the registry prices for every token-model row.

Usage: DATABASE_URL=... .venv/bin/python scripts/recompute_costs.py [--dry-run]
Reasoning tokens were billed twice until 2026-11-04 (they are part of the
API's output count); this rewrites every stored cost with the current formula.
"""

from __future__ import annotations

import asyncio
import os
import sys
from decimal import Decimal
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from app.llm.registry import MODELS, compute_cost_usd  # noqa: E402


async def main(dry_run: bool) -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with AsyncSession(engine) as session:
        rows = (
            await session.execute(
                sa.text(
                    "SELECT id, model, tokens_input, tokens_cached, tokens_output,"
                    " tokens_reasoning, cost_usd FROM llm_calls"
                )
            )
        ).all()
        before = Decimal(0)
        after = Decimal(0)
        changed = 0
        for row in rows:
            spec = MODELS.get(str(row.model))
            if spec is None or spec.unit != "token":
                continue
            cost = compute_cost_usd(
                str(row.model),
                int(row.tokens_input),
                int(row.tokens_cached),
                int(row.tokens_output),
                int(row.tokens_reasoning),
            )
            before += Decimal(row.cost_usd)
            after += cost
            if cost != Decimal(row.cost_usd):
                changed += 1
                if not dry_run:
                    await session.execute(
                        sa.text("UPDATE llm_calls SET cost_usd = :c WHERE id = :id"),
                        {"c": cost, "id": str(row.id)},
                    )
        if not dry_run:
            await session.commit()
        print(f"rows {len(rows)}, changed {changed}, total {before} -> {after}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main("--dry-run" in sys.argv))
