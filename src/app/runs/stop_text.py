"""Plain sentences for stopped runs: no class names, no schema paths.

The technical text stays in run_steps.error; the run record, GET /runs
and the run page carry only what a person can act on.
"""

from __future__ import annotations

import re

_STEP_LABELS = {
    "researcher": "researcher",
    "architect": "planner",
    "outline": "outline",
    "blueprint": "blueprint",
    "lectures": "lecture writer",
    "blockplan": "block plan",
    "illustrations": "illustrator",
    "reader": "reader",
    "block": "block writer",
}
_STEP_ERROR = re.compile(r"^(\w+(?:Error|Exception)):\s*(.*)$", re.DOTALL)
_LEGACY_MARKER = re.compile(
    r"^stopped at ([\w:.\-]+): (\w+(?:Error|Exception)):\s*(.*)$", re.DOTALL
)
_SCHEMA_TRACE = re.compile(
    r"\(\s*'[^)]*\)|\bschema\b|rejected|after validation|Input should|Field required"
)

EMPTY_PLAN = "The planner returned no modules. Add sources with more content and try again."
MODEL_DOWN = "The model service did not answer. Try again in a minute."
CAP_REACHED = "The spending cap was reached. Raise the cap and continue the build."


def _label(step_key: str) -> str:
    return _STEP_LABELS.get(step_key.split(":", 1)[0], "step")


def plain_stop(step_key: str, error_class: str, message: str) -> tuple[str, str | None]:
    """The sentence and the machine code (empty_plan, model_down, cap_reached, bad_result)."""
    if error_class == "SpendCapReached":
        return CAP_REACHED, "cap_reached"
    if error_class in {"LLMError", "ReadTimeout", "ConnectError", "HTTPStatusError"}:
        return MODEL_DOWN, "model_down"
    if _SCHEMA_TRACE.search(message):
        if step_key.startswith("architect") and "modules" in message and "at least 1" in message:
            return EMPTY_PLAN, "empty_plan"
        label = _label(step_key)
        return (
            f"The {label} returned a result that did not pass the checks. Try again.",
            "bad_result",
        )
    text = message.strip() or "A step stopped unexpectedly."
    return text[:500], None


def plain_marker(raw: str | None) -> tuple[str | None, str | None]:
    """Sentence and code for a stored stop marker (old raw markers are rewritten on read)."""
    if not raw:
        return raw, None
    match = _LEGACY_MARKER.match(raw)
    if match is None:
        return raw, None
    return plain_stop(match.group(1), match.group(2), match.group(3))


def plain_step_error(step_key: str, raw: str | None) -> str | None:
    """A step's stored error as a sentence; the raw text never leaves run_steps."""
    if not raw:
        return raw
    match = _STEP_ERROR.match(raw)
    if match is None:
        return plain_stop(step_key, "", raw)[0]
    return plain_stop(step_key, match.group(1), match.group(2))[0]
