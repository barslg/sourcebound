"""Find the AI error: plant one mutated sentence, grade by code.

Planted claims come first from real reader holds: the held (false)
sentence itself is planted and the correction comes from the passage.
Otherwise code picks a verified claim and a mutation kind from the
closed list. Code owns number/date swaps; the model only rewrites the
other kinds. Some answers are fully correct (seeded). Planted rows are
flagged and excluded from search, retrieval pairs, tutor retrieval,
and translation memory.
"""

from __future__ import annotations

import random
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
    "entity_swap": "Check names and instruments against the passage.",
    "cause_reversal": "Check which way the cause runs.",
    "bad_citation": "Open the citation before trusting the claim.",
}

_ARTICLE_REF = re.compile(r"(Article|Art\.|§|paragraph|para\.?)\s*\d+", re.IGNORECASE)


def rewrite_allowed(kind: str) -> bool:
    """Only non-numeric kinds go to the saboteur model; code owns numbers."""
    return kind != "number_date"


def _mutate_number_date(sentence: str) -> str:
    """Bump a numeric value that is not part of a citation/article reference."""
    # protect digits that are part of an article/citation reference
    protected: list[tuple[int, int]] = []
    for m in re.finditer(r"(Article|Art\.|§)\s*\d+", sentence):
        protected.append(m.span())
    for match in re.finditer(r"\d+(?:[.,]\d+)?", sentence):
        if any(start <= match.start() < end for start, end in protected):
            continue
        value = match.group(0)
        try:
            bumped = str(int(value) + 1) if "." not in value and "," not in value else value
        except ValueError:
            continue
        if bumped != value:
            return sentence[: match.start()] + bumped + sentence[match.end() :]
    raise ValueError("no mutable numeric value outside citation references")


def mutate_claim(sentence: str, *, kind: str) -> str:
    """Deterministic code mutation; non-numeric kinds prefer word-level flips.

    No fallback announces the error and no language, date, or course
    fact is hardcoded: when no targeted wording exists the caller must
    route the kind to the saboteur model instead of planting a
    self-announcing sentence.
    """
    if kind == "number_date":
        return _mutate_number_date(sentence)
    if kind == "negation":
        for old, new in (
            (" shall ", " shall not "),
            (" must ", " must not "),
            (" is ", " is not "),
            (" are ", " are not "),
        ):
            if old in sentence:
                return sentence.replace(old, new, 1)
        raise ValueError("negation needs a modal or copula to flip")
    if kind == "scope":
        for old, new in (("all ", "no "), ("every ", "no "), ("shall ", "need never ")):
            if old in sentence.lower():
                i = sentence.lower().index(old)
                return sentence[:i] + new + sentence[i + len(old) :]
        raise ValueError("scope needs a quantifier to flip")
    if kind == "entity_swap":
        raise ValueError("entity swap is rewritten by the saboteur model")
    if kind == "cause_reversal":
        raise ValueError("cause reversal is rewritten by the saboteur model")
    if kind == "bad_citation":
        raise ValueError("bad citation is rewritten by the saboteur model")
    raise ValueError(f"unknown mutation kind: {kind}")


def plant_error(
    *,
    verified: list[dict[str, Any]],
    reader_holds: list[dict[str, Any]],
    seed: int = 0,
    fully_correct: bool = False,
    mutated: str | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """Build one spot-the-error exercise with exactly one planted sentence.

    The planted sentence is the held sentence itself when a reader hold
    exists; the corrected sentence comes from the passage/true value.
    The original (true) sentence is never shown beside the planted one.
    Position is seeded, never fixed at 0.
    """
    rng = random.Random(seed)
    sentences_pool = [str(i.get("sentence", "")) for i in verified if i.get("sentence")]
    if fully_correct or (not verified and not reader_holds):
        sentences = list(sentences_pool)
        while len(sentences) < 2:
            sentences.append(sentences_pool[0] if sentences_pool else "No sentence available.")
            if len(sentences) >= 2:
                break
        return {
            "sentences": sentences[: max(2, len(sentences))],
            "planted_sentence": "",
            "planted_index": -1,
            "kind": kind or "negation",
            "passage": str(verified[0].get("passage", "")) if verified else "",
            "true_sentence": "",
            "fully_correct": True,
        }
    chosen_kind = kind or MUTATION_KINDS[seed % len(MUTATION_KINDS)]
    holds = [h for h in reader_holds if h.get("sentence")]
    if holds:
        first = holds[0]
        planted_sentence = str(first["sentence"])
        true_sentence = str(first.get("true_sentence") or first.get("correction") or "")
        passage = str(first.get("passage", ""))
        if not true_sentence:
            # correction comes from the passage text when supplied
            true_sentence = str(first.get("passage_text", ""))
    else:
        pick = verified[seed % len(verified)]
        true_sentence = str(pick.get("sentence", ""))
        passage = str(pick.get("passage", ""))
        if mutated is not None:
            planted_sentence = mutated
        elif chosen_kind in ("entity_swap", "cause_reversal", "bad_citation"):
            raise ValueError(f"{chosen_kind} requires a saboteur rewrite")
        else:
            try:
                planted_sentence = mutate_claim(true_sentence, kind=chosen_kind)
            except ValueError:
                planted_sentence = mutate_claim(true_sentence, kind="negation")
                chosen_kind = "negation"
    companions = [s for s in sentences_pool if s != true_sentence and s != planted_sentence]
    rng.shuffle(companions)
    sentences = companions[:2]
    planted_index = rng.randrange(len(sentences) + 1)
    sentences.insert(planted_index, planted_sentence)
    return {
        "sentences": sentences,
        "planted_sentence": planted_sentence,
        "planted_index": planted_index,
        "kind": chosen_kind,
        "passage": passage,
        "true_sentence": true_sentence,
        "fully_correct": False,
    }


def is_excluded_from(row: dict[str, Any], store: str) -> bool:
    """Planted rows never enter search, pairs, tutor retrieval, or TM."""
    if store not in _EXCLUDED_STORES:
        return False
    return bool(row.get("planted"))


def _norm(value: Any) -> str:
    return " ".join(str(value).strip().lower().split())


def grade_spot_error(planted: dict[str, Any], *, submission: dict[str, Any]) -> dict[str, Any]:
    """Code grading with partial credit: sentence, kind, passage, fix."""
    if planted.get("fully_correct"):
        named_none = _norm(submission.get("sentence", "")) in ("", "none")
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
    named_index = submission.get("sentence_index", submission.get("planted_index"))
    if isinstance(named_index, str) and named_index.isdigit():
        named_index = int(named_index)
    if submission.get("sentence") == planted.get("planted_sentence") or (
        isinstance(named_index, int)
        and named_index == planted.get("planted_index")
        and named_index >= 0
    ):
        parts += 0.4
    if submission.get("kind") == planted.get("kind"):
        parts += 0.25
    if submission.get("passage") == planted.get("passage"):
        parts += 0.15
    correct = _norm(submission.get("correct_value", ""))
    if correct and correct == _norm(planted.get("true_sentence", "")):
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
    """Hits vs false alarms per mutation kind; a miss is neither."""
    report: dict[str, dict[str, int]] = {}
    for attempt in attempts:
        kind = str(attempt.get("kind", "negation"))
        slot = report.setdefault(kind, {"hits": 0, "false_alarms": 0})
        if attempt.get("hit"):
            slot["hits"] += 1
        elif attempt.get("false_alarm"):
            slot["false_alarms"] += 1
        # a plain miss (hit=False without false_alarm) counts as neither
    return report
