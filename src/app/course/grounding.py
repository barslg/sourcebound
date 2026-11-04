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


def locate_verbatim(passage_text: str, quote: str) -> tuple[int, int] | None:
    """Where a quote stands in its passage, or None when the words are not there.

    Exact containment first; else the same words with any run of
    whitespace between them (a passage wraps mid-sentence, the writer
    quotes it on one line). The span returned is the passage's own
    text, so a stored quote is always verbatim. Every character
    difference beyond whitespace keeps the quote a paraphrase.
    """
    if not quote.strip():
        return None
    at = passage_text.find(quote)
    if at >= 0:
        return at, at + len(quote)
    pattern = r"\s+".join(re.escape(word) for word in quote.split())
    found = re.search(pattern, passage_text)
    if found is None:
        return None
    return found.start(), found.end()


def content_terms(text: str) -> list[str]:
    """Content words of a text: length >= 4, no stopwords, in order."""
    out: list[str] = []
    for token in _TOKEN_RE.findall(str(text).lower()):
        if len(token) >= 4 and token not in _STOPWORDS and token not in out:
            out.append(token)
    return out


_DOC_HEADING_RE = re.compile(r"(?:^|\n)#{1,6} \S")
_CODE_LIKE_RE = re.compile(r"""[=(){}\[\];]|^["'`]|^\w+\.\w+""")


def list_items(passage_text: str) -> list[str]:
    """Numbered or bulleted lines of one passage, without their markers.

    A passage that ends a sentence with a colon and continues as a list
    is one claim unit: these items are the part of the claim a teach or
    example block must carry, not optional decoration. Lines inside a
    code fence and items that read as code (brackets, assignments,
    dotted calls) are not claims a reading must name: the 2026-11-02
    Muse API course held its first two blocks on
    `response = client.chat.completions.create(`.
    """
    if _DOC_HEADING_RE.search(str(passage_text)):
        # a passage that embeds a document (a STATE.md, a rules file) quotes
        # it as an illustration: its bullets are the file's, not the claim's
        # (P1520 of the Muse API course, 15 holds over three rewrites)
        return []
    items: list[str] = []
    fenced = False
    for line in str(passage_text).splitlines():
        if line.strip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = _BULLET_RE.match(line)
        if match and not _CODE_LIKE_RE.search(match.group(1).strip()):
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


_LABEL_ITEM_WORDS = 6


def item_label(item: str) -> tuple[str, str]:
    """(label, rest) of one list item code can check: a colon label, or a short named item.

    A prose bullet ("Use a base image that already has the toolchain")
    carries no name a word can establish — asking for its words held
    paraphrases on the 2026-11-02 big course — so it returns an empty
    label and the reader's coverage item judges it.
    """
    head, colon, rest = item.partition(":")
    if colon and len(head.split()) <= _LABEL_ITEM_WORDS:
        return head.strip(), rest.strip()
    if len(item.split()) <= _LABEL_ITEM_WORDS:
        return item.strip(), ""
    return "", item


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


_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)


def _fence_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _FENCE_RE.finditer(text)]


def _locate_quote(lowered: str, quote: str, marker_positions: list[int]) -> int:
    """Where a citation's quote stands in the block: after its own marker, else by length.

    A quote is searched from each marker of its passage (the nearest
    occurrence after it); elsewhere only a quote of at least
    QUOTED_SPAN_WORDS words is located, so a one-word quote ("Usage")
    never lands on another passage's sentence, while a marker parked at
    the end still covers no boundary.
    """
    for position in marker_positions:
        at = lowered.find(quote, position)
        if at < 0 and len(quote) > 40:
            at = lowered.find(quote[:40], position)
        if at >= 0:
            return at
    if len(words_of(quote)) < QUOTED_SPAN_WORDS:
        return -1
    at = lowered.find(quote)
    if at < 0 and len(quote) > 40:
        at = lowered.find(quote[:40])
    return at


def adjacency_holds(block_text: str, citations: list[dict[str, Any]]) -> list[str]:
    """Hold a teach block whose text moves from one passage to another without a marker.

    Each citation's quote is located in the block text (after its own
    marker when the passage has one); where two located quotes from
    different passages follow each other, a ⟦P<n>⟧ marker naming the
    second passage must stand between them. Citation order is
    irrelevant; a marker parked at the end covers no boundary; a quote
    that is not in the text (a paraphrase) locates nothing and the
    reader judges it; inside one code fence two cited passages may
    meet without a marker (a marker cannot stand inside code).
    """
    lowered = block_text.lower()
    markers = [(m.start(), m.group(1)) for m in HANDLE_MARKER_RE.finditer(block_text)]
    spans: list[tuple[int, str]] = []
    for citation in citations:
        handle = str(citation.get("handle", ""))
        quote = str(citation.get("quote", "")).strip().lower()
        if not handle or not quote:
            continue
        at = _locate_quote(lowered, quote, [pos for pos, h in markers if h == handle])
        if at >= 0:
            spans.append((at, handle))
    spans.sort()
    fences = _fence_spans(block_text)
    holds: list[str] = []
    for (start, first), (end, second) in zip(spans, spans[1:], strict=False):
        if first == second:
            continue
        if "\n\n" in block_text[start:end]:
            continue  # a paragraph break is a boundary the learner sees
        if any(lo <= end < hi for lo, hi in fences):
            continue
        between = [h for at, h in markers if start < at <= end]
        if second not in between:
            holds.append(
                f"false adjacency: text from {first} runs into text from {second} "
                f"without a ⟦{second}⟧ marker between them"
            )
    return list(dict.fromkeys(holds))


def assignment_holds(
    assigned: list[str], *, cited: list[str], leftovers: list[dict[str, Any]]
) -> list[str]:
    """Hold a teach/example block that drops an assigned passage without a word.

    The plan assigns passages to the block (the skeleton's
    source_handles); the writer either cites each one or names it in
    `leftovers` with a reason the author sees. A passage that is
    neither is a silent gap: on the 2026-11-02 course only the paid
    reader noticed, one topic at a time.
    """
    declared = {str(item.get("handle", "")) for item in leftovers if isinstance(item, dict)}
    named = set(cited) | declared
    return [
        f"coverage: assigned passage {handle} is neither cited nor declared in leftovers"
        for handle in assigned
        if handle and handle not in named
    ]


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


def number_pool_pids(
    plan: dict[str, Any], node_id: str, passage_of: dict[str, str], cited_pids: list[str]
) -> list[str]:
    """Passage ids a block's numbers are read against: its topic's taught passages and its own."""
    found = taught_handles_for(plan, node_id)
    taught = [passage_of[h] for h in found[0] if h in passage_of] if found else []
    return list(dict.fromkeys([*taught, *cited_pids]))


_CORRECT_TIERS = frozenset({"correct", "right", "true"})
_CLAUSE_SPLIT_RE = re.compile(r"\s*(?:;|—|–| - |\.\s+|\n)\s*")


def answer_key_claims(output: dict[str, Any], block_type: str = "") -> list[str]:
    """The claims an exercise's key asserts.

    Choice and scenario practice: the clauses of the free `answer` (the
    quote the key rests on); their options and items are the writer's own
    words, so a learner cannot tell the key by its shape. Only an exercise
    with no `answer` falls back to its correct options or items. Cloze:
    the blanks. Matching: the right sides. Ordering: every step. Never a
    distractor (the exercise's own invention), a question item or a
    label: those assert nothing about the sources.
    """
    claims: list[str] = []
    answer_clauses = [c for c in _CLAUSE_SPLIT_RE.split(str(output.get("answer", ""))) if c.strip()]
    quoted_answer = block_type in ("choice", "scenario_practice") and bool(answer_clauses)
    if not quoted_answer:
        for option in output.get("options", []) if isinstance(output.get("options"), list) else []:
            if isinstance(option, dict) and option.get("correct"):
                claims.append(str(option.get("text", "")))
    for blank in output.get("blanks", []) if isinstance(output.get("blanks"), list) else []:
        claims.append(str(blank))
    for pair in output.get("pairs", []) if isinstance(output.get("pairs"), list) else []:
        if isinstance(pair, dict):
            claims.append(str(pair.get("right", "")))
    for step in output.get("steps", []) if isinstance(output.get("steps"), list) else []:
        claims.append(str(step))
    if block_type == "scenario_practice" and not quoted_answer:
        for item in output.get("items", []) if isinstance(output.get("items"), list) else []:
            if isinstance(item, dict) and str(item.get("tier", "")).lower() in _CORRECT_TIERS:
                claims.append(str(item.get("text", "")))
    if quoted_answer:
        claims += answer_clauses
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


def diagram_holds(output: dict[str, Any], cited_texts: list[str]) -> list[str]:
    """Hold a diagram whose node or edge label is not quoted from a cited passage.

    The browser draws from labels only, so a label is the whole claim:
    each one is a run of QUOTED_SPAN_WORDS words from a passage the
    block cites, or the whole label when shorter. The writer prompts
    state the same rule.
    """
    cited_words = words_of(" \n ".join(cited_texts))
    labels: list[str] = []
    for node in output.get("nodes", []) if isinstance(output.get("nodes"), list) else []:
        if isinstance(node, dict):
            labels.append(str(node.get("label", "")))
    for edge in output.get("edges", []) if isinstance(output.get("edges"), list) else []:
        if isinstance(edge, dict):
            labels.append(str(edge.get("label", "")))
    return [
        f"diagram: the label {label[:80]!r} is not quoted from a cited passage"
        for label in labels
        if label.strip() and not quoted_from(label, cited_words)
    ]


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
