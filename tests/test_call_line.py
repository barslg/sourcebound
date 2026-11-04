"""The console line of one model call: what was called, by whom, for how much."""

from __future__ import annotations

import logging
import re
from decimal import Decimal

import pytest

from app.llm.call_line import GREEN, RED, render_call_line

EVENT = {
    "event": "model_call",
    "timestamp": "2026-11-04 14:56:09",
    "agent": "reader",
    "model": "muse-spark-1.3",
    "endpoint": "POST https://api.meta.ai/v1/responses",
    "response_id": "resp_0a1b2c3d4e5f6071",
    "status": "completed",
    "tokens_input": 6738,
    "tokens_cached": 0,
    "tokens_output": 8387,
    "tokens_reasoning": 7711,
    "cost_usd": "0.067761",
    "duration_ms": 57859,
    "run_id": "3a627ca2-722e-448d-85a8-5e0cc605117e",
    "step_key": "reader:m3-t4",
}


def test_a_call_reads_as_one_line_with_model_endpoint_step_tokens_cost_and_response_id() -> None:
    assert render_call_line(dict(EVENT), color=False) == (
        "2026-11-04 14:56:09  model call  Muse Spark 1.3  POST https://api.meta.ai/v1/responses"
        "  reader:m3-t4  completed  57.9 s"
        "  in 6,738 · cached 0 · out 8,387 · reasoning 7,711  $0.0678  resp_0a1b2c3d4e5f6071"
    )


def test_a_call_without_step_or_response_id_names_its_agent_and_a_dash() -> None:
    line = render_call_line({**EVENT, "step_key": None, "response_id": None}, color=False)
    assert "  reader  completed  " in line and line.endswith("$0.0678  -")


def test_a_rejected_call_is_red_and_a_completed_one_green() -> None:
    assert GREEN + "completed" in render_call_line(dict(EVENT), color=True)
    rejected = render_call_line({**EVENT, "status": "rejected"}, color=True)
    assert RED + "rejected" in rejected and GREEN not in rejected


def test_the_worker_prints_the_call_line_and_drops_the_minute_revive_job(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.llm.calls import emit_model_call
    from app.logging import configure_logging

    configure_logging("INFO", log_format="pretty")
    emit_model_call(
        agent="reader",
        model="muse-spark-1.3",
        response_id="resp_1",
        status="completed",
        input_tokens=1,
        cached_tokens=0,
        output_tokens=2,
        reasoning_tokens=0,
        cost_usd=Decimal("0.0001"),
        duration_ms=1000,
        run_id=None,
        step_key="reader:m1-t1",
    )
    printed = re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)
    assert "model call  Muse Spark 1.3  POST " in printed and "{" not in printed
    periodic = logging.getLogger("procrastinate.periodic")
    record = periodic.makeRecord(
        "procrastinate.periodic",
        logging.INFO,
        "x",
        1,
        "Periodic job builds.revive[1] deferred",
        (),
        None,
    )
    assert not periodic.filter(record)
    other = periodic.makeRecord(
        "procrastinate.periodic", logging.INFO, "x", 1, "Starting job courses.build[2]", (), None
    )
    assert periodic.filter(other)
