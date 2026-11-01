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

_ARTICLE_REF = re.compile(
    r"(Articles?|Art\.|Artikkel|Artikel|Article|§|paragraph|para\.?|avsnitt|пункт|пункту|статья|статті)"
    r"\s*\d+",
    re.IGNORECASE,
)
_REGULATION_REF = re.compile(r"\b\d{3,4}/\d{1,4}\b")
_NON_CONTENT_PREFIXES = ("reuse notice", "source:", "source line", "note on reuse")

# Per-language negation/scope word tables (en/nb/uk). A kind whose table
# has no hit in the sentence raises, so the caller routes it to the
# saboteur model or holds instead of planting an English fallback.
_NEGATIONS: tuple[tuple[str, str], ...] = (
    (" shall not ", " shall "),
    (" shall ", " shall not "),
    (" must not ", " must "),
    (" must ", " must not "),
    (" is not ", " is "),
    (" is ", " is not "),
    (" are not ", " are "),
    (" are ", " are not "),
    (" skal ikke ", " skal "),
    (" skal ", " skal ikke "),
    (" må ikke ", " må "),
    (" må ", " må ikke "),
    (" er ikke ", " er "),
    (" er ", " er ikke "),
    (" повинні не ", " повинні "),
    (" повинні ", " не повинні "),
    (" не є ", " є "),
    (" є ", " не є "),
)
_SCOPES: tuple[tuple[str, str], ...] = (
    ("all ", "no "),
    ("every ", "no "),
    ("alle ", "ingen "),
    ("enhver ", "ingen "),
    ("всі ", "жодні "),
    ("кожен ", "жоден "),
)


def is_content_text(text: str) -> bool:
    """False for non-content passages (reuse notice, source line).

    Called at ingest (the flag is stored on the passage) and again in
    the picker, so a notice line can never become an exercise sentence.
    """
    head = " ".join(str(text).strip().lower().split())[:200]
    if "reuse notice:" in head or "only the official journal is authentic" in head:
        return False
    return not any(head.startswith(prefix) for prefix in _NON_CONTENT_PREFIXES)


def _protected_spans(sentence: str) -> list[tuple[int, int]]:
    """Spans of every reference shape whose digits must never mutate."""
    protected: list[tuple[int, int]] = []
    for match in _ARTICLE_REF.finditer(sentence):
        end = match.end()
        # parenthesised sub-numbers and plural lists: 5(1), 4, 5 and 6
        tail = sentence[end:]
        extra = re.match(r"(\s*\(\d+\))*(\s*(,|and|og|та|и)\s*\d+(\s*\(\d+\))?)*", tail)
        if extra:
            end += extra.end()
        protected.append((match.start(), end))
    for match in _REGULATION_REF.finditer(sentence):
        protected.append(match.span())
    return protected


def rewrite_allowed(kind: str) -> bool:
    """Only non-numeric kinds go to the saboteur model; code owns numbers."""
    return kind != "number_date"


def _mutate_number_date(sentence: str) -> str:
    """Bump a numeric value that is not part of a citation/article reference."""
    protected = _protected_spans(sentence)
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


def pick_sentence(
    verified: list[dict[str, Any]],
    *,
    seed: int = 0,
    topic_terms: list[str] | None = None,
) -> dict[str, Any]:
    """THE picker: one sentence + one kind, chosen by seed from content only.

    The saboteur receives exactly this sentence and kind, and the
    answer key is built from this same pick — there is no second path.
    Kinds the code cannot plant deterministically on this sentence
    (model kinds) stay eligible; number_date only when a mutable
    number exists outside every reference. The caller passes only the
    topic's cited passages as `verified`; among them the picker
    prefers sentences whose terms overlap the topic title/purpose
    (`topic_terms`), so an off-topic sentence never becomes the
    exercise when an on-topic one exists.
    """
    pool = [
        item
        for item in verified
        if item.get("sentence")
        and item.get("is_content", True)
        and is_content_text(str(item.get("sentence", "")))
    ]
    if not pool:
        raise ValueError("no content sentence available to plant")
    if topic_terms:
        wanted = {term.lower() for term in topic_terms if len(term) >= 4}
        preferred = [
            item
            for item in pool
            if wanted & set(re.findall(r"[^\W\d_]+", str(item["sentence"]).lower()))
        ]
        if preferred:
            pool = preferred
    item = pool[seed % len(pool)]
    sentence = str(item["sentence"])
    number_ok = True
    try:
        _mutate_number_date(sentence)
    except ValueError:
        number_ok = False
    kind = MUTATION_KINDS[seed % len(MUTATION_KINDS)]
    if kind == "number_date" and not number_ok:
        kind = "negation"
    return {
        "sentence": sentence,
        "passage": str(item.get("passage", "")),
        "kind": kind,
        "is_content": True,
        "fully_correct": seed % 4 == 3,
    }


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
        padded = f" {sentence} "
        for old, new in _NEGATIONS:
            if old in padded.lower():
                i = padded.lower().index(old)
                flipped = padded[:i] + new + padded[i + len(old) :]
                return flipped.strip() if sentence == sentence.strip() else flipped[1:-1]
        raise ValueError("negation needs a modal or copula to flip")
    if kind == "scope":
        for old, new in _SCOPES:
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
    fully_correct: bool | None = False,
    mutated: str | None = None,
    kind: str | None = None,
    pick: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one spot-the-error exercise with exactly one planted sentence.

    The planted sentence is the held sentence itself when a reader hold
    exists; the corrected sentence comes from the passage/true value.
    The original (true) sentence is never shown beside the planted one.
    Position is seeded, never fixed at 0. `fully_correct=None` seeds
    the choice (one exercise in four is fully correct); `pick` is the
    one picker's output — sentence, kind, and passage all come from it.
    """
    if fully_correct is None:
        fully_correct = seed % 4 == 3 and not reader_holds
    rng = random.Random(seed)
    sentences_pool = [
        str(i.get("sentence", ""))
        for i in verified
        if i.get("sentence")
        and i.get("is_content", True)
        and is_content_text(str(i.get("sentence", "")))
    ]
    if fully_correct or (not verified and not reader_holds):
        anchor = str(pick["sentence"]) if pick and pick.get("sentence") else ""
        if not anchor and sentences_pool:
            anchor = sentences_pool[0]
        companions = [s for s in sentences_pool if s != anchor]
        rng.shuffle(companions)
        sentences = companions[:2]
        if anchor:
            sentences.insert(rng.randrange(len(sentences) + 1), anchor)
        while len(sentences) < 2:
            sentences.append("No sentence available.")
        passage = str(pick.get("passage", "")) if pick else ""
        if not passage and anchor:
            passage = str(
                next((i.get("passage", "") for i in verified if i.get("sentence") == anchor), "")
            )
        return {
            "sentences": sentences,
            "planted_sentence": "",
            "planted_index": -1,
            "kind": kind or "negation",
            "passage": passage,
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
        chosen = pick or pick_sentence(verified, seed=seed)
        true_sentence = str(chosen.get("sentence", ""))
        passage = str(chosen.get("passage", ""))
        chosen_kind = str(kind or chosen.get("kind") or chosen_kind)
        if mutated is not None:
            planted_sentence = mutated
        elif chosen_kind == "number_date":
            planted_sentence = mutate_claim(true_sentence, kind="number_date")
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


def sentence_named_right(planted: dict[str, Any], submission: dict[str, Any]) -> bool:
    """True when the submission names the planted sentence (by text or index)."""
    named_index = submission.get("sentence_index", submission.get("planted_index"))
    if isinstance(named_index, str) and named_index.isdigit():
        named_index = int(named_index)
    return _norm(submission.get("sentence", "")) == _norm(planted.get("planted_sentence", "")) or (
        isinstance(named_index, int)
        and named_index == planted.get("planted_index")
        and named_index >= 0
    )


def classify_spot_attempt(planted: dict[str, Any], submission: dict[str, Any]) -> dict[str, Any]:
    """Hit, false alarm or miss of one attempt, by kind (the teacher counters).

    A hit is credited to the planted kind. A false alarm (naming a
    sentence that holds no error) is credited to the kind the learner
    claimed, `unspecified` when they claimed none. Missing the planted
    sentence is a miss of the planted kind. Naming none on a clean
    exercise is correct and counts nowhere.
    """
    claimed = _norm(submission.get("kind", ""))
    claimed_kind = claimed if claimed in MUTATION_KINDS else "unspecified"
    named = _norm(submission.get("sentence", "")) not in ("", "none") or isinstance(
        submission.get("sentence_index"), int
    )
    if planted.get("fully_correct"):
        return {
            "kind": None,
            "hit": False,
            "miss": False,
            "false_alarm": named,
            "false_alarm_kind": claimed_kind if named else None,
        }
    hit = sentence_named_right(planted, submission)
    return {
        "kind": str(planted.get("kind", "")),
        "hit": hit,
        "miss": not hit,
        "false_alarm": named and not hit,
        "false_alarm_kind": claimed_kind if named and not hit else None,
    }


def grade_spot_error(planted: dict[str, Any], *, submission: dict[str, Any]) -> dict[str, Any]:
    """Code grading with partial credit: sentence, kind, passage, fix."""
    if planted.get("fully_correct"):
        named_none = _norm(submission.get("sentence", "")) == "none"
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
    sentence_right = sentence_named_right(planted, submission)
    if not sentence_right:
        # kind, passage, and fix earn credit only behind the right sentence
        return {
            "score": 0.0,
            "feedback": {
                "wrong": str(planted.get("planted_sentence", "")),
                "true": str(planted.get("true_sentence", "")),
                "passage": str(planted.get("passage", "")),
                "habit": _HABITS.get(str(planted.get("kind", "")), "Check the passage."),
            },
        }
    parts = 0.4
    if _norm(submission.get("kind", "")) == _norm(planted.get("kind", "")):
        parts += 0.25
    if _norm(submission.get("passage", "")) == _norm(planted.get("passage", "")):
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
