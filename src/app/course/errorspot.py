"""Find the AI error: plant one mutated sentence, grade by code.

Planted claims come first from real reader holds; otherwise code picks a
verified claim and a mutation kind from the closed list. Code owns
number/date swaps; the model only rewrites the other kinds. Some answers
are fully correct (seeded). Planted rows are flagged and excluded from
search, retrieval pairs, tutor retrieval, and translation memory.
"""

from __future__ import annotations

import re
from typing import Any

MUTATION_KINDS: tuple[str, ...] = (
    "number_date",
    "negation",
    "scope",
    "entity_swap",
    "cause_reversal",
    "bad_citation",
)

_EXCLUDED_STORES = ("search", "retrieval_pair", "tutor_retrieval", "translation_memory")

_HABITS = {
    "number_date": "Check every number against the passage.",
    "negation": "Watch for flipped must/must-not wording.",
    "scope": "Ask who the rule really covers.",
    "entity_swap": "Check names: GDPR is not the AI Act.",
    "cause_reversal": "Check which way the cause runs.",
    "bad_citation": "Open the citation before trusting the claim.",
}


def rewrite_allowed(kind: str) -> bool:
    """Only non-numeric kinds go to the saboteur model; code owns numbers."""
    return kind != "number_date"


def mutate_claim(sentence: str, *, kind: str) -> str:
    """Deterministic code mutation for one verified sentence.

    Every kind guarantees a changed sentence: targeted rewrites first,
    a kind-shaped generic fallback when the sentence carries none of
    the targeted wording.
    """
    if kind == "number_date":

        def _bump(match: re.Match[str]) -> str:
            return str(int(match.group(0)) + 1)

        mutated, count = re.subn(r"\d+", _bump, sentence, count=1)
        if count:
            return mutated
        return f"{sentence} (as of 2027)"
    if kind == "negation":
        if "must " in sentence:
            return sentence.replace("must ", "must not ", 1)
        return f"It is false that {sentence}"
    if kind == "scope":
        mutated = sentence.replace("Deployers", "Everyone", 1).replace("must", "never need to", 1)
        if mutated != sentence:
            return mutated
        return f"{sentence} This applies to no employer."
    if kind == "entity_swap":
        mutated = (
            sentence.replace("AI Act", "GDPR", 1)
            .replace("Deployers", "Vendors", 1)
            .replace("AI", "GDPR", 1)
        )
        if mutated != sentence:
            return mutated
        return f"{sentence} (under the GDPR, not the AI Act)"
    if kind == "cause_reversal":
        mutated = sentence.replace("train staff", "be trained by staff", 1)
        if mutated != sentence:
            return mutated
        return f"{sentence} (with cause and effect reversed)"
    return f"{sentence} [citation does not say this]"


def plant_error(
    *,
    verified: list[dict[str, Any]],
    reader_holds: list[dict[str, Any]],
    seed: int = 0,
    fully_correct: bool = False,
) -> dict[str, Any]:
    """Build one spot-the-error exercise with exactly one planted sentence."""
    if fully_correct or not verified:
        sentences = [str(item.get("sentence", "")) for item in verified if item.get("sentence")]
        while len(sentences) < 2:
            sentences.append("Staff apply the cited passage.")
        return {
            "sentences": sentences,
            "planted_sentence": "",
            "planted_index": -1,
            "kind": "negation",
            "passage": "",
            "true_sentence": "",
            "fully_correct": True,
        }
    holds = [hold for hold in reader_holds if hold.get("sentence")]
    kinds = list(MUTATION_KINDS)
    kind = kinds[seed % len(kinds)]
    if holds:
        first = holds[0]
        true_sentence = str(first.get("sentence", ""))
        passage = str(first.get("passage", "P1"))
        planted_sentence = str(first.get("mutated", "")) or mutate_claim(true_sentence, kind=kind)
    else:
        pick = verified[seed % len(verified)]
        true_sentence = str(pick.get("sentence", ""))
        passage = str(pick.get("passage", "P1"))
        planted_sentence = mutate_claim(true_sentence, kind=kind)
    companions = [
        str(item.get("sentence", ""))
        for item in verified
        if str(item.get("sentence", "")) != true_sentence
    ]
    sentences = [planted_sentence, true_sentence] + companions[:1]
    while len(sentences) < 2:
        sentences.append("Staff apply the cited passage.")
    return {
        "sentences": sentences,
        "planted_sentence": planted_sentence,
        "planted_index": 0,
        "kind": kind,
        "passage": passage,
        "true_sentence": true_sentence,
        "fully_correct": False,
    }


def is_excluded_from(row: dict[str, Any], store: str) -> bool:
    """Planted rows never enter search, pairs, tutor retrieval, or TM."""
    _ = store
    return bool(row.get("planted"))


def grade_spot_error(planted: dict[str, Any], *, submission: dict[str, Any]) -> dict[str, Any]:
    """Code grading with partial credit: sentence, kind, passage, fix."""
    if planted.get("fully_correct"):
        named_none = str(submission.get("sentence", "")).strip().lower() in ("", "none")
        score = 1.0 if named_none else 0.0
        return {
            "score": score,
            "feedback": {
                "wrong": str(submission.get("sentence", "")),
                "true": "no error",
                "passage": str(planted.get("passage", "")),
                "habit": "Say none when every sentence holds.",
            },
        }
    parts = 0.0
    if submission.get("sentence") == planted.get("planted_sentence"):
        parts += 0.4
    if submission.get("kind") == planted.get("kind"):
        parts += 0.25
    if submission.get("passage") == planted.get("passage"):
        parts += 0.15
    if str(submission.get("correct_value", "")).strip():
        parts += 0.2
    return {
        "score": round(min(parts, 1.0), 2),
        "feedback": {
            "wrong": str(planted.get("planted_sentence", "")),
            "true": str(planted.get("true_sentence", "")),
            "passage": str(planted.get("passage", "")),
            "habit": _HABITS.get(str(planted.get("kind", "")), "Check the passage."),
        },
    }


def teacher_report(attempts: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Hits vs false alarms per mutation kind for the teacher report."""
    report: dict[str, dict[str, int]] = {}
    for attempt in attempts:
        kind = str(attempt.get("kind", "negation"))
        slot = report.setdefault(kind, {"hits": 0, "false_alarms": 0})
        if attempt.get("hit"):
            slot["hits"] += 1
        else:
            slot["false_alarms"] += 1
    return report
