"""Grading-parity submissions for translated exercises, NOT built from the key.

A translated exercise must score a correct translated answer like the
English one. The submissions here come from the translator's own raw
replies in the ledger (what the model returned for each segment), never
from the stored translated answer key, which is the thing under test.
Callers: tests/test_t07_live.py (live acceptance) and
tests/test_t07c_product.py (the same extraction on the mock path).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


async def raw_segments(
    session: AsyncSession, *, run_id: UUID, node: str, language: str
) -> dict[str, str]:
    """Segment id to the text the model returned for one block, from the ledger."""
    rows = (
        await session.execute(
            sa.text(
                "SELECT lc.response FROM llm_calls lc JOIN run_steps rs ON rs.id = lc.step_id"
                " WHERE lc.run_id = :run AND lc.agent = 'translator'"
                " AND lc.status = 'completed' AND rs.step_key = :key ORDER BY lc.created_at"
            ),
            {"run": str(run_id), "key": f"translator:{node}:{language}"},
        )
    ).all()
    found: dict[str, str] = {}
    for (response,) in rows:
        for item in (response or {}).get("segments", []):
            found[str(item["id"])] = str(item["text"])
    return found


def english_submissions(block_type: str, key: dict[str, Any], content: dict[str, Any]) -> Any:
    """A correct and a wrong English submission, read from the SOURCE key."""
    if block_type == "choice":
        correct = key["correct"]
        right = int(correct[0] if isinstance(correct, list) else correct)
        wrong = (right + 1) % len(content["options"])
        return {"picked": right}, {"picked": wrong}
    if block_type == "cloze":
        return {"blanks": list(key["blanks"])}, {"blanks": ["wrong"] * len(key["blanks"])}
    return (
        {
            "sentence": key["planted_sentence"],
            "kind": key["kind"],
            "passage": key["passage"],
            "correct_value": key["true_sentence"],
        },
        {
            "sentence": content["sentences"][
                (int(key["planted_index"]) + 1) % len(content["sentences"])
            ],
            "kind": key["kind"],
            "passage": key["passage"],
            "correct_value": "x",
        },
    )


def translated_submissions(
    block_type: str,
    source_key: dict[str, Any],
    translated_content: dict[str, Any],
    raw: dict[str, str],
) -> Any:
    """A correct and a wrong translated submission from the raw model replies."""
    if block_type == "choice":
        correct = source_key["correct"]
        index = int(correct[0] if isinstance(correct, list) else correct)
        options = list(translated_content["options"])
        right = options.index(raw[f"options[{index}]"])
        return {"picked": right}, {"picked": (right + 1) % len(options)}
    if block_type == "cloze":
        count = len(source_key["blanks"])
        right_blanks = [raw[f"key:blanks[{i}]"] for i in range(count)]
        return {"blanks": right_blanks}, {"blanks": list(source_key["blanks"])}
    planted = int(source_key["planted_index"])
    return (
        {
            "sentence": raw[f"sentences[{planted}]"],
            "kind": source_key["kind"],
            "passage": source_key["passage"],
            "correct_value": raw["key:true_sentence"],
        },
        {
            "sentence": raw[f"sentences[{(planted + 1) % len(translated_content['sentences'])}]"],
            "kind": source_key["kind"],
            "passage": source_key["passage"],
            "correct_value": "x",
        },
    )
