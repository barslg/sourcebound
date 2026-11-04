"""A hold reason as the author reads it: passages by address, evidence as a quote.

The stored reason stays as the checks wrote it (the writer needs the handles to
repair the block); every screen that shows it goes through `human_hold_reason`.
"""

from __future__ import annotations

import re
from typing import Any

_EVIDENCE = re.compile(r"\(evidence: (.*?)\)(?=; [a-z_]+: |$)", re.DOTALL)
_HANDLE = re.compile(r"\bP[1-9][0-9]*\b")


def handle_labels(tree: dict[str, Any]) -> dict[str, str]:
    """Plan handle -> passage address, from every block's resolved source refs."""
    labels: dict[str, str] = {}
    for module in tree.get("modules", []):
        for topic in module.get("topics", []):
            for block in topic.get("blocks", []):
                for ref in block.get("source_refs", []):
                    if ref.get("address_label"):
                        labels.setdefault(str(ref["handle"]), str(ref["address_label"]))
    return labels


def human_hold_reason(reason: str | None, labels: dict[str, str]) -> str:
    """The reason with handles named by address and the evidence set apart as a quote."""
    if not reason:
        return ""
    out: list[str] = []
    last = 0
    for found in _EVIDENCE.finditer(reason):
        out.append(_plain(reason[last : found.start()], labels))
        out.append(f"Quote: “{found.group(1).strip()}”")
        last = found.end()
    out.append(_plain(reason[last:], labels))
    return re.sub(r"\s+", " ", "".join(out)).strip()


def _plain(text: str, labels: dict[str, str]) -> str:
    text = text.replace("source_handles", "the passages assigned to the block")
    return _HANDLE.sub(lambda m: labels.get(m.group(0), "another passage"), text)
