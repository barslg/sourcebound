"""Course glossary seed: terms the course itself repeats across blocks.

The product path used to translate with an empty glossary, so one term
drifted between blocks (Leverandører vs Tilbydere). The seed takes the
long content words that recur in at least two blocks of the course; the
glossary pass translates them once and code checks every block against
that one table.
"""

from __future__ import annotations

import re
from typing import Any

from app.translation.segments import extract_segments

MIN_TERM_LENGTH = 6
MAX_SEED_TERMS = 12
_FILLER = frozenset(
    {
        "should",
        "would",
        "could",
        "because",
        "however",
        "between",
        "through",
        "within",
        "without",
        "nobody",
        "everyone",
        "another",
        "following",
        "which",
        "where",
        "before",
        "after",
        "their",
        "there",
        "these",
        "those",
        "other",
        "about",
        "today",
        "always",
    }
)


def seed_glossary(
    blocks: list[dict[str, Any]], explicit: dict[str, str] | None = None
) -> dict[str, str]:
    """Explicit terms plus the content words that recur across blocks.

    Deterministic (document frequency, then alphabet), so the second
    identical run builds the same prompt and pays nothing. Seeded terms
    carry an empty target: the glossary step fills it.
    """
    seen_in: dict[str, set[str]] = {}
    for block in blocks:
        node = str(block["plan_node_id"])
        text = " ".join(
            segment["text"]
            for segment in extract_segments(str(block["block_type"]), dict(block["content"]))
        )
        for word in set(re.findall(r"[^\W\d_]+", text.lower())):
            if len(word) >= MIN_TERM_LENGTH and word not in _FILLER:
                seen_in.setdefault(word, set()).add(node)
    recurring = sorted(
        (word for word, nodes in seen_in.items() if len(nodes) >= 2),
        key=lambda word: (-len(seen_in[word]), word),
    )[:MAX_SEED_TERMS]
    terms = {word: "" for word in sorted(recurring)}
    terms.update({str(k): str(v) for k, v in (explicit or {}).items()})
    return terms
