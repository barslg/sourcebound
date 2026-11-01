"""Code checks that bind generated blocks to their passages.

Every function here is called by the runs executor (the production
caller) on a generator reply before the block is persisted as draft;
a returned hold string becomes the block's hold_reason. The checks
compare the block text and answer key against the *stored* passage
text, never against model-supplied text.
"""

from __future__ import annotations

import re

HANDLE_MARKER_RE = re.compile(r"⟦(P[1-9][0-9]*)⟧")
_BULLET_RE = re.compile(r"^\s*(?:[-•*]|\d+[.)])\s+(.*\S)\s*$")
_TOKEN_RE = re.compile(r"[^\W\d_]+(?:[-'][^\W\d_]+)*|\d+(?:[.,]\d+)?", re.UNICODE)

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


def has_numbers(passage_text: str) -> bool:
    """True when the passage carries a numeric value worth drawing."""
    return bool(re.search(r"\d", str(passage_text)))


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


def _item_carried(item: str, block_norm: str) -> bool:
    if normalize(item) in block_norm:
        return True
    terms = content_terms(item)
    return bool(terms) and all(term in block_norm for term in terms)


def coverage_holds(block_text: str, cited: dict[str, str]) -> list[str]:
    """Hold a teach/example block that drops a cited passage's list.

    `cited` maps handle to stored passage text. Every numbered or
    bulleted item of a cited passage must be carried by the block text
    (verbatim item or all its content terms). No word quotas: a prose
    passage imposes no requirement beyond its citations.
    """
    holds: list[str] = []
    block_norm = normalize(block_text)
    for handle, passage_text in cited.items():
        for item in list_items(passage_text):
            if not _item_carried(item, block_norm):
                holds.append(
                    f"coverage: cited passage {handle} list item {item!r} "
                    "is missing from the block text"
                )
    return holds


def adjacency_holds(block_text: str, cited_handles: list[str]) -> list[str]:
    """Hold a teach block that joins passages without handle markers.

    With two or more cited passages the text must carry one
    ⟦P<n>⟧ marker per cited passage, in citation order, never
    interleaved: sentences from two passages may not sit in one
    paragraph without the marker between them.
    """
    handles = list(dict.fromkeys(cited_handles))
    if len(handles) < 2:
        return []
    found = HANDLE_MARKER_RE.findall(block_text)
    holds: list[str] = []
    for first, second in zip(handles, handles[1:], strict=False):
        if first not in found or second not in found:
            holds.append(
                f"false adjacency: passages {first} and {second} are joined "
                "without a handle marker between them"
            )
    if found and [h for h in found if h in handles] != handles:
        # a repeated or out-of-order marker means passages interleave
        if not holds:
            holds.append(
                "false adjacency: cited passages appear interleaved "
                "without markers in citation order"
            )
    return holds


def taught_handles_for(plan: dict, node_id: str) -> tuple[list[str], str] | None:
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


def answer_key_terms(output: dict) -> list[str]:
    """Content terms an exercise asks with: options, blanks, pairs, key.

    Every option (not only the correct one), every blank, every pair
    side, every scenario item and tier, and the free `answer` key.
    """
    texts: list[str] = [str(output.get("answer", ""))]
    for option in output.get("options", []) if isinstance(output.get("options"), list) else []:
        texts.append(str(option.get("text", "")) if isinstance(option, dict) else str(option))
    for blank in output.get("blanks", []) if isinstance(output.get("blanks"), list) else []:
        texts.append(str(blank))
    for pair in output.get("pairs", []) if isinstance(output.get("pairs"), list) else []:
        if isinstance(pair, dict):
            texts.append(str(pair.get("left", "")))
            texts.append(str(pair.get("right", "")))
    for item in output.get("items", []) if isinstance(output.get("items"), list) else []:
        if isinstance(item, dict):
            texts.append(str(item.get("text", "")))
            texts.append(str(item.get("tier", "")))
    terms: list[str] = []
    for text in texts:
        for term in content_terms(text):
            if term not in terms:
                terms.append(term)
    return terms


def taught_holds(output: dict, taught_texts: list[str]) -> list[str]:
    """Hold a check/practice/test block that asks untaught material.

    No claim may sit in an answer key that is not in a passage cited
    by a teach/example block of the same topic (module tests: of the
    module). A term with a trailing plural or hyphen variant in the
    taught text counts as taught.
    """
    taught = normalize(" \n ".join(taught_texts))
    holds: list[str] = []
    for term in answer_key_terms(output):
        variants = {term, term.rstrip("s"), f"{term}s"}
        if not any(variant and variant in taught for variant in variants):
            holds.append(
                f"not taught: answer-key term {term!r} is not in a passage taught by this topic"
            )
    return holds


def marker(handle: str) -> str:
    """The handle marker shown as a chip at a passage boundary."""
    return f"⟦{handle}⟧"
