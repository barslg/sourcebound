"""Code checks on the shape of an exercise: what a learner sees, not what the key quotes.

Called by the runs executor (`generate_block_step.judge`) on every
exercise draft and by `course.replay` over stored blocks. A returned line
is a Fix list line the writer can act on; the prefixes are listed in
`course.replay.CODE_HOLD_PREFIXES` so a recheck recomputes them. The key is
quoted from the sources (`grounding.taught_holds`); nothing here may make a
learner-visible text copy a passage to satisfy it.
"""

from __future__ import annotations

import re
from typing import Any

from app.course.grounding import QUOTED_SPAN_WORDS, _contiguous, words_of

CLOZE_BLANK_MAX_WORDS = 3
# New plans ask for four pairs (architect prompt); a stored three-pair block is kept,
# since its rewrite cannot add a pair its passages lack (a gate a rewrite cannot satisfy).
MIN_MATCHING_PAIRS = 3
PASSAGE_SHAPED = 0.8
_PROMPT_START_RE = re.compile(r"^\s*(?:>>>|\.\.\.|\$ |# )")
_CODE_START_RE = re.compile(
    r"^(?:>>>|\.\.\.|#|(?:for|while|if|elif|else|try|except|finally|with|def|class|return|del"
    r"|import|from|print|raise|break|continue|pass|assert|lambda)\b)"
)
_CODE_SHAPE_RE = re.compile(r"[=]|\w\(.*\)")
_PROSE_WORDS = 8
_CORRECT_TIERS = frozenset({"correct", "right", "true"})


def cloze_holds(blanks: list[str]) -> list[str]:
    """Hold a cloze whose blank is a sentence: a blank is a term or a code token.

    The grader compares the blank exactly, so a learner can only type back
    one to three words (a term, a command, a code token); a longer blank
    is unanswerable (9 of 9 cloze blocks of the 2026-11-04 course) and one
    that starts with a prompt asks for the transcript's decoration.
    """
    holds: list[str] = []
    for blank in blanks:
        count = len(blank.split())
        if _PROMPT_START_RE.match(blank):
            holds.append(
                f"cloze: the blank {blank[:60]!r} starts with a prompt character -"
                " a blank is a term or a code token without the prompt"
            )
        elif count > CLOZE_BLANK_MAX_WORDS:
            holds.append(
                f"cloze: the blank {blank[:60]!r} is {count} words - a blank is a term or a code"
                f" token of at most {CLOZE_BLANK_MAX_WORDS} words, taken verbatim from a taught"
                " passage"
            )
    return holds


def matching_holds(pairs: list[Any]) -> list[str]:
    """Hold a matching below the floor of MIN_MATCHING_PAIRS pairs (two are guessed by elimination).

    The floor is what code enforces (three, so a stored block with three pairs is kept); the
    architect's prompt asks new plans for four.
    """
    count = len([p for p in pairs if isinstance(p, dict)])
    if count < MIN_MATCHING_PAIRS:
        return [
            f"matching: {count} pairs - at least {MIN_MATCHING_PAIRS} real pairs are needed"
            " (ask about fewer, firmer facts only in a choice)"
        ]
    return []


def _step_is_code(step: str) -> bool:
    head = step.strip()
    if not head:
        return False
    if _CODE_START_RE.match(head):
        return True
    return len(head.split()) < _PROSE_WORDS and bool(_CODE_SHAPE_RE.search(head))


def ordering_holds(steps: list[str]) -> list[str]:
    """Hold an ordering whose items mix code lines and prose steps.

    A learner sorts code lines by what runs first, or prose steps by what
    happens first; a list of both is a branch or a listing cut into
    pieces (7 of 9 orderings of the 2026-11-04 course). The check
    establishes one shape across the items, nothing about the order.
    """
    kinds = [_step_is_code(step) for step in steps]
    if any(kinds) and not all(kinds):
        code = next(s for s, k in zip(steps, kinds, strict=True) if k)
        prose = next(s for s, k in zip(steps, kinds, strict=True) if not k)
        return [
            f"ordering: the items mix code and prose ({code[:40]!r} is code, {prose[:40]!r} is a"
            " sentence) - use one procedure whose steps are all code lines or all sentences"
        ]
    return []


def passage_shaped(text: str, taught_words: list[str]) -> bool:
    """True when at least PASSAGE_SHAPED of the text's words sit in four-word runs of the sources.

    Such a text reads as a copied passage line: it is the shape the
    learner can tell from a writer's own sentence.
    """
    words = words_of(text)
    if len(words) < QUOTED_SPAN_WORDS:
        return _contiguous(words, taught_words)  # a short line is copied when it is verbatim
    marked = [False] * len(words)
    for i in range(len(words) - QUOTED_SPAN_WORDS + 1):
        if _contiguous(words[i : i + QUOTED_SPAN_WORDS], taught_words):
            for j in range(i, i + QUOTED_SPAN_WORDS):
                marked[j] = True
    return sum(marked) / len(words) >= PASSAGE_SHAPED


def giveaway_holds(output: dict[str, Any], taught_words: list[str], block_type: str) -> list[str]:
    """Hold a choice or scenario whose copied-passage shape splits exactly along right and wrong.

    When every correct option is a passage line and no wrong one is (or
    the other way round) the learner answers by looking, not by knowing
    (17 of 24 such blocks in the 2026-11-04 course). The check
    establishes that the shape tells nothing about the key; whether the
    options are true or false is the reader's call.
    """
    if block_type == "choice":
        rows = [
            (str(o.get("text", "")), bool(o.get("correct")))
            for o in output.get("options", [])
            if isinstance(o, dict)
        ]
    elif block_type == "scenario_practice":
        rows = [
            (str(i.get("text", "")), str(i.get("tier", "")).lower() in _CORRECT_TIERS)
            for i in output.get("items", [])
            if isinstance(i, dict)
        ]
    else:
        return []
    right = [passage_shaped(text, taught_words) for text, correct in rows if correct]
    wrong = [passage_shaped(text, taught_words) for text, correct in rows if not correct]
    if not right or not wrong:
        return []
    if all(right) and not any(wrong):
        return [
            "giveaway: only the correct options are copied passage lines - write every option in"
            " your own words in one shape and keep the quote in `answer` and the citations"
        ]
    if not any(right) and all(wrong):
        return [
            "giveaway: only the wrong options are copied passage lines - write every option in"
            " your own words in one shape and keep the quote in `answer` and the citations"
        ]
    return []


def exercise_holds(output: dict[str, Any], block_type: str, taught_texts: list[str]) -> list[str]:
    """Every shape check that belongs to the exercise's block type."""
    if block_type == "cloze":
        return cloze_holds(
            [str(b) for b in output.get("blanks", []) if isinstance(output.get("blanks"), list)]
        )
    if block_type == "matching":
        pairs = output.get("pairs")
        return matching_holds(pairs if isinstance(pairs, list) else [])
    if block_type == "ordering":
        steps = output.get("steps")
        return ordering_holds([str(x) for x in steps] if isinstance(steps, list) else [])
    return giveaway_holds(output, words_of(" \n ".join(taught_texts)), block_type)
