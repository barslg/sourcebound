"""ONE passage numbering for the architect, the generators, the reader and replay.

Handles (P1, P2, …) are assigned once, over the approved passages of a
course in document order after list rejoin. The plan stores the handles
it cites with their passage ids; generation reuses those exact handles
(reconcile_handles) so the frozen plan, the sources list and every
citation name the same passage. Renumbering a filtered subset is what
broke every question block's citations (T11 review H2).
"""

from __future__ import annotations

import re
from typing import Any

from app.course.passages import rejoin_list_passages

_HANDLE_RE = re.compile(r"^P([1-9][0-9]*)$")


def address_sort_key(address_key: str) -> tuple[Any, ...]:
    """Numeric sort for passage addresses: para2 before para10 (never text order)."""
    match = re.fullmatch(r"p(\d+)/para(\d+)", address_key)
    if match:
        return (0, int(match.group(1)), int(match.group(2)))
    match = re.fullmatch(r"h:(.*)/para(\d+)", address_key)
    if match:
        return (1, match.group(1), int(match.group(2)))
    match = re.fullmatch(r"t([0-9:.]+)-([0-9:.]+)", address_key)
    if match:
        return (2, match.group(1), match.group(2))
    return (3, address_key)


def handle_number(handle: str) -> int:
    match = _HANDLE_RE.match(str(handle))
    return int(match.group(1)) if match else 0


def _order_key(row: dict[str, Any]) -> tuple[Any, ...]:
    ordinal = row.get("ordinal")
    return (
        str(row.get("source_id", "")),
        int(ordinal) if ordinal is not None else -1,
        address_sort_key(str(row.get("address", row.get("address_key", "")))),
    )


def numbered_passages(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Document-ordered, list-rejoined passages with handles P1..Pn.

    Rows carry passage_id (or id), source_id, address (or address_key),
    text, optional is_content and ordinal. The same rows always give
    the same handles: the architect and every later step share this.
    """
    normalised = [
        {
            **row,
            "passage_id": str(row.get("passage_id", row.get("id", ""))),
            "address": str(row.get("address", row.get("address_key", ""))),
            "text": str(row.get("text", "")),
        }
        for row in rows
    ]
    merged = rejoin_list_passages(sorted(normalised, key=_order_key))
    return [{**passage, "handle": f"P{index}"} for index, passage in enumerate(merged, 1)]


def reconcile_handles(
    numbered: list[dict[str, Any]], plan_map: dict[str, str]
) -> list[dict[str, Any]]:
    """Keep the plan's handle for every passage it cites; number the rest after them.

    `plan_map` is handle -> passage id as the plan stored it. A passage
    the plan cites keeps that handle even when the approved set changed
    since planning; a passage the plan cited through an absorbed
    paragraph resolves to its merged unit. Every other passage gets a
    fresh handle above the plan's highest number, in document order.
    """
    owner: dict[str, dict[str, Any]] = {}
    for passage in numbered:
        for pid in [passage["passage_id"], *passage.get("merged_ids", [])]:
            owner.setdefault(str(pid), passage)
    wanted: dict[str, str] = {}
    for handle, pid in plan_map.items():
        cited = owner.get(str(pid))
        if cited is None or handle_number(handle) == 0:
            continue
        wanted.setdefault(cited["passage_id"], handle)
    if all(
        passage["handle"] == wanted.get(passage["passage_id"], passage["handle"])
        for passage in numbered
    ):
        return numbered
    used = set(wanted.values())
    next_number = max([handle_number(h) for h in used] + [0]) + 1
    out: list[dict[str, Any]] = []
    for passage in numbered:
        kept = wanted.get(passage["passage_id"])
        if kept is None:
            while f"P{next_number}" in used:
                next_number += 1
            kept = f"P{next_number}"
            used.add(kept)
            next_number += 1
        out.append({**passage, "handle": kept})
    return sorted(out, key=lambda passage: handle_number(passage["handle"]))


def handle_map(sources: list[dict[str, Any]]) -> dict[str, str]:
    """handle -> passage id for a sources list; items without handles are numbered in order."""
    mapping: dict[str, str] = {}
    for index, item in enumerate(sources, 1):
        pid = str(item.get("passage_id", ""))
        if not pid:
            continue
        mapping[str(item.get("handle") or f"P{index}")] = pid
    return mapping
