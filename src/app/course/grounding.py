"""Code checks that bind generated blocks to their passages.

Every function here is called by the runs executor (the production
caller) on a generator reply before the block is persisted as draft;
a returned hold string becomes the block's hold_reason. The checks
compare the block text and answer key against the *stored* passage
text, never against model-supplied text.
"""

from __future__ import annotations

import re
from typing import Any

HANDLE_MARKER_RE = re.compile(r"⟦(P[1-9][0-9]*)⟧")
_BULLET_RE = re.compile(r"^\s*(?:[-•*]|\d+[.)])\s+(.*\S)\s*$")
_TOKEN_RE = re.compile(r"[^\W\d_]+(?:[-'][^\W\d_]+)*|\d+(?:[.,]\d+)?", re.UNICODE)
# Words as a learner reads them: slashes and dashes stay inside commands
# ("/new", "--yolo"), surrounding punctuation and quotes drop.
_WORD_RE = re.compile(r"[^\s]+")
_EDGE_PUNCT = ".,;:!?()[]{}\"'“”‘’«»…"
QUOTED_SPAN_WORDS = 4

_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "your",
        "you",
        "are",
        "was",
        "were",
        "will",
        "would",
        "must",
        "shall",
        "should",
        "can",
        "could",
        "may",
        "might",
        "not",
        "only",
        "also",
        "than",
        "then",
        "when",
        "which",
        "what",
        "who",
        "how",
        "why",
        "does",
        "did",
        "has",
        "have",
        "had",
        "been",
        "being",
        "its",
        "it",
        "in",
        "on",
        "of",
        "to",
        "a",
        "an",
        "is",
        "be",
        "or",
        "as",
        "at",
        "by",
        "per",
        "via",
        "one",
        "two",
        "any",
        "all",
        "each",
        "every",
        "other",
        "more",
        "most",
        "such",
        "same",
        "about",
        "between",
        "under",
        "over",
        "after",
        "before",
        "during",
        "within",
    }
)


def normalize(text: str) -> str:
    """Lowercased, whitespace-collapsed text for containment checks."""
    return " ".join(str(text).lower().split())


def content_terms(text: str) -> list[str]:
    """Content words of a text: length >= 4, no stopwords, in order."""
    out: list[str] = []
    for token in _TOKEN_RE.findall(str(text).lower()):
        if len(token) >= 4 and token not in _STOPWORDS and token not in out:
            out.append(token)
    return out


def list_items(passage_text: str) -> list[str]:
    """Numbered or bulleted lines of one passage, without their markers.

    A passage that ends a sentence with a colon and continues as a list
    is one claim unit: these items are the part of the claim a teach or
    example block must carry, not optional decoration.
    """
    items: list[str] = []
    for line in str(passage_text).splitlines():
        match = _BULLET_RE.match(line)
        if match:
            items.append(match.group(1).strip())
    return items


def has_list(passage_text: str) -> bool:
    """True when the passage carries a numbered or bulleted list."""
    return bool(list_items(passage_text))


_QUANTITY_RE = re.compile(
    r"(?<![\w.-])(?:[$€£]\s?\d[\d.,]*|\d[\d.,]*\s?(?:%|percent|ms|s|m|h|km|kg|g|mb|gb|kb|tokens?|"
    r"seconds?|minutes?|hours?|days?|weeks?|months?|years?|times|steps?|users?|people|cases?|items?)"
    r"(?![\w-])|(?:19|20)\d\d(?![\w.-]))",
    re.IGNORECASE,
)
_FIGURE_REF_RE = re.compile(r"\b(?:figure|fig\.|table|chart|diagram)\s*\d", re.IGNORECASE)


def has_numbers(passage_text: str) -> bool:
    """True when the passage carries a quantity worth drawing: a number with a unit, a
    percentage, a currency amount, a year, or a figure/table reference — never a bare
    label digit ("stage 3 of 3") or a version number ("muse-spark-1.2")."""
    text = str(passage_text)
    return bool(_QUANTITY_RE.search(text) or _FIGURE_REF_RE.search(text))


def passage_kind(passage_text: str) -> str:
    """The structural kind of one passage: has_numbers / has_list / prose."""
    if has_numbers(passage_text):
        return "has_numbers"
    if has_list(passage_text):
        return "has_list"
    return "prose"


def allowed_block_types(passage_text: str) -> tuple[str, ...]:
    """Block types code allows for one passage kind (figure gating)."""
    if passage_kind(passage_text) == "prose":
        return ("reading", "choice", "matching", "cloze", "scenario_practice", "spot_the_error")
    return (
        "reading",
        "figure",
        "choice",
        "matching",
        "cloze",
        "scenario_practice",
        "spot_the_error",
    )


def item_label(item: str) -> tuple[str, str]:
    """(label, rest) of one list item: the words before a colon, else two content terms."""
    head, colon, rest = item.partition(":")
    if colon and len(head.split()) <= 6:
        return head.strip(), rest.strip()
    terms = content_terms(item)
    return " ".join(terms[:2]), item


def _item_carried(item: str, block_norm: str) -> bool:
    """The item's label is named and, when it has one, its first content term too."""
    label, rest = item_label(item)
    if not label:
        return True
    if normalize(label) not in block_norm:
        terms = content_terms(label)
        if not terms or not all(term in block_norm for term in terms):
            return False
    rest_terms = content_terms(rest)
    return not rest_terms or rest_terms[0] in block_norm


def coverage_holds(block_text: str, cited: dict[str, str]) -> list[str]:
    """Hold a teach/example block that drops an item of a cited passage's list.

    `cited` maps handle to passage text (list units already joined).
    Every item of a cited list must be named in the block: its label
    (the words before the colon, or the item's first two content terms)
    and the first content term of its text. A paraphrase that names
    every option passes; a block that stops at the colon holds. No word
    quotas: a prose passage imposes no requirement beyond its citations.
    """
    holds: list[str] = []
    block_norm = normalize(block_text)
    for handle, passage_text in cited.items():
        for item in list_items(passage_text):
            if not _item_carried(item, block_norm):
                holds.append(
                    f"coverage: cited passage {handle} list item {item_label(item)[0]!r} "
                    "is not named in the block text"
                )
    return holds


def adjacency_holds(block_text: str, citations: list[dict[str, Any]]) -> list[str]:
    """Hold a teach block whose text moves from one passage to another without a marker.

    Each citation's quote is located in the block text; where two
    located quotes from different passages follow each other, a
    ⟦P<n>⟧ marker naming the second passage must stand between them.
    Citation order is irrelevant; a marker parked at the end covers no
    boundary; a quote that is not in the text (a paraphrase) locates
    nothing and the reader judges it.
    """
    spans: list[tuple[int, str]] = []
    lowered = block_text.lower()
    for citation in citations:
        handle = str(citation.get("handle", ""))
        quote = str(citation.get("quote", "")).strip().lower()
        if not handle or not quote:
            continue
        at = lowered.find(quote)
        if at < 0 and len(quote) > 40:
            at = lowered.find(quote[:40])
        if at >= 0:
            spans.append((at, handle))
    spans.sort()
    markers = [(m.start(), m.group(1)) for m in HANDLE_MARKER_RE.finditer(block_text)]
    holds: list[str] = []
    for (start, first), (end, second) in zip(spans, spans[1:], strict=False):
        if first == second:
            continue
        between = [h for at, h in markers if start < at <= end]
        if second not in between:
            holds.append(
                f"false adjacency: text from {first} runs into text from {second} "
                f"without a ⟦{second}⟧ marker between them"
            )
    return list(dict.fromkeys(holds))


def taught_handles_for(plan: dict[str, Any], node_id: str) -> tuple[list[str], str] | None:
    """Handles taught by a node's topic, from the frozen plan.

    Lessons: teach/example blocks of the same topic. Module tests: any
    teach/example block of the module. Returns (handles, topic_kind),
    or None when the plan carries no teach handles for the node (the
    caller falls back to the block's own citations).
    """
    for module in plan.get("modules", []) if isinstance(plan, dict) else []:
        topics = module.get("topics", []) if isinstance(module, dict) else []
        for topic in topics:
            blocks = topic.get("blocks", []) if isinstance(topic, dict) else []
            if not any(str(b.get("plan_node_id", "")) == node_id for b in blocks):
                continue
            kind = str(topic.get("kind", "lesson"))
            pool = topics if kind == "module_test" else [topic]
            handles: list[str] = []
            for item in pool:
                for block in item.get("blocks", []):
                    if str(block.get("role", "")) not in ("teach", "example"):
                        continue
                    raw = block.get("source_handles") or [
                        str(ref.get("handle", ""))
                        for ref in block.get("source_refs", [])
                        if isinstance(ref, dict)
                    ]
                    for handle in raw:
                        if handle and handle not in handles:
                            handles.append(str(handle))
            return (handles, kind) if handles else None
    return None


_CORRECT_TIERS = frozenset({"correct", "right", "true"})
_CLAUSE_SPLIT_RE = re.compile(r"\s*(?:;|—|–| - |\.\s+|\n)\s*")


def answer_key_claims(output: dict[str, Any], block_type: str = "") -> list[str]:
    """The claims an exercise's key asserts.

    Choice: the correct options. Cloze: the blanks. Matching: the right
    sides. Scenario practice: the items labelled correct and every
    clause of the free `answer` (that is where its key lives). Never a
    distractor (the exercise's own invention), a question item or a
    label: those assert nothing about the sources.
    """
    claims: list[str] = []
    for option in output.get("options", []) if isinstance(output.get("options"), list) else []:
        if isinstance(option, dict) and option.get("correct"):
            claims.append(str(option.get("text", "")))
    for blank in output.get("blanks", []) if isinstance(output.get("blanks"), list) else []:
        claims.append(str(blank))
    for pair in output.get("pairs", []) if isinstance(output.get("pairs"), list) else []:
        if isinstance(pair, dict):
            claims.append(str(pair.get("right", "")))
    if block_type == "scenario_practice":
        for item in output.get("items", []) if isinstance(output.get("items"), list) else []:
            if isinstance(item, dict) and str(item.get("tier", "")).lower() in _CORRECT_TIERS:
                claims.append(str(item.get("text", "")))
        claims += _CLAUSE_SPLIT_RE.split(str(output.get("answer", "")))
    return [claim.strip() for claim in claims if claim.strip()]


def words_of(text: str) -> list[str]:
    """Lowercased words with their edge punctuation dropped; commands keep their slashes."""
    out: list[str] = []
    for raw in _WORD_RE.findall(str(text).lower()):
        word = raw.strip(_EDGE_PUNCT)
        if word:
            out.append(word)
    return out


def _contiguous(needle: list[str], haystack: list[str]) -> bool:
    size = len(needle)
    if size == 0 or size > len(haystack):
        return False
    return any(haystack[i : i + size] == needle for i in range(len(haystack) - size + 1))


def quoted_from(claim: str, taught_words: list[str]) -> bool:
    """True when the claim carries a run of QUOTED_SPAN_WORDS words from the taught text.

    A claim shorter than the span must appear whole. This establishes
    provenance (the wording comes from a taught passage); meaning is
    the reader's rubric.
    """
    words = words_of(claim)
    if not words:
        return True
    if len(words) < QUOTED_SPAN_WORDS:
        return _contiguous(words, taught_words)
    return any(
        _contiguous(words[i : i + QUOTED_SPAN_WORDS], taught_words)
        for i in range(len(words) - QUOTED_SPAN_WORDS + 1)
    )


def taught_holds(
    output: dict[str, Any], taught_texts: list[str], block_type: str = ""
) -> list[str]:
    """Hold a check/practice/test block whose key asserts what no taught passage says.

    Every claim of the answer key (answer_key_claims) must be quoted
    from a passage cited by a teach/example block of the same topic
    (module tests: of the module): a run of four consecutive words, or
    the whole claim when shorter. The generator prompts state the same
    rule, so a hold is a broken contract, not a style call.
    """
    taught_words = words_of(" \n ".join(taught_texts))
    holds: list[str] = []
    for claim in answer_key_claims(output, block_type):
        if not quoted_from(claim, taught_words):
            holds.append(
                f"not taught: the key claim {claim[:80]!r} is not quoted from a passage"
                " this topic taught"
            )
    return holds
