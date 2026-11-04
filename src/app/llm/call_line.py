"""The console line of one model call: what was called, by which step, for how much."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.llm.registry import display_name_of

GREEN = "\x1b[32m"
RED = "\x1b[31m"
CYAN = "\x1b[36m"
YELLOW = "\x1b[33m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"


def render_call_line(event: dict[str, Any], *, color: bool) -> str:
    """One model call: time, model, endpoint, step, status, seconds, tokens, cost, response id."""

    def paint(code: str, text: str) -> str:
        return f"{code}{text}{RESET}" if color else text

    status = str(event["status"])
    tokens = (
        f"in {int(event['tokens_input']):,} · cached {int(event['tokens_cached']):,}"
        f" · out {int(event['tokens_output']):,} · reasoning {int(event['tokens_reasoning']):,}"
    )
    return "  ".join(
        [
            str(event["timestamp"]),
            paint(DIM, "model call"),
            paint(BOLD, display_name_of(str(event["model"]))),
            paint(CYAN, str(event["endpoint"])),
            str(event.get("step_key") or event["agent"]),
            paint(GREEN if status == "completed" else RED, status),
            f"{int(event['duration_ms']) / 1000:.1f} s",
            paint(DIM, tokens),
            paint(YELLOW, f"${Decimal(str(event['cost_usd'])):.4f}"),
            str(event.get("response_id") or "-"),
        ]
    )
