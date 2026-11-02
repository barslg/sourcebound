"""Replay every code check over a stored course, offline, block by block.

A code check over generated content is not done until it has run over
a real course: this tool reads one course (SELECT only) and prints, per
block, what each check would hold today and why, plus the architect's
plan-level verdict and the handle numbering the generators would use.
Caller: `make replay-holds COURSE=<id>` (and the T11b report).

Usage: python scripts/replay_holds.py --course <uuid> [--database-url URL] [--json PATH]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import sqlalchemy as sa  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

QUESTION_ROLES = ("check", "practice", "test")


async def load_course(url: str, course_id: str) -> dict[str, Any]:
    """Blocks, plan, passages and citations of one course (read-only)."""
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            blocks = (
                await conn.execute(
                    sa.text(
                        "SELECT b.id, b.plan_node_id, b.role, b.block_type, b.status,"
                        " coalesce(b.hold_reason, ''), bv.id, bv.content, bv.answer_key,"
                        " t.plan_node_id, t.kind, t.title, t.objectives,"
                        " (SELECT content FROM block_versions"
                        "  WHERE block_id = b.id AND content -> 'source_handles' IS NOT NULL"
                        "  ORDER BY version_no DESC LIMIT 1)"
                        " FROM blocks b JOIN topics t ON t.id = b.topic_id"
                        " JOIN modules m ON m.id = t.module_id"
                        " LEFT JOIN block_versions bv ON bv.id = b.current_version_id"
                        " WHERE m.course_id = :c ORDER BY m.ordinal, t.ordinal, b.ordinal"
                    ),
                    {"c": course_id},
                )
            ).all()
            passages = (
                await conn.execute(
                    sa.text(
                        "SELECT p.id, p.source_id, p.address_key, p.text, p.is_content, p.ordinal"
                        " FROM passages p"
                        " JOIN course_sources cs ON cs.source_id = p.source_id"
                        " AND cs.course_id = :c AND cs.approval = 'approved'"
                        " JOIN sources s ON s.id = p.source_id AND s.approval = 'approved'"
                    ),
                    {"c": course_id},
                )
            ).all()
            version_ids = [str(r[6]) for r in blocks if r[6] is not None]
            citations = (
                await conn.execute(
                    sa.text(
                        "SELECT block_version_id, passage_id, quote FROM citations"
                        " WHERE block_version_id::text = ANY(:ids) ORDER BY quote_start"
                    ),
                    {"ids": version_ids},
                )
            ).all()
    finally:
        await engine.dispose()
    cited: dict[str, list[tuple[str, str]]] = {}
    for version_id, passage_id, quote in citations:
        cited.setdefault(str(version_id), []).append((str(passage_id), str(quote)))
    return {
        "blocks": [
            {
                "id": str(r[0]),
                "node": str(r[1]),
                "role": str(r[2]),
                "block_type": str(r[3]),
                "status": str(r[4]),
                "hold_reason": str(r[5]),
                "version_id": str(r[6]) if r[6] is not None else "",
                "content": dict(r[7] or {}),
                "answer_key": dict(r[8] or {}),
                "topic": str(r[9]),
                "topic_kind": str(r[10]),
                "topic_title": str(r[11]),
                "objectives": list(r[12] or []),
                "plan_content": dict(r[13] or {}),
                "citations": cited.get(str(r[6]), []) if r[6] is not None else [],
            }
            for r in blocks
        ],
        "passages": [
            {
                "passage_id": str(r[0]),
                "source_id": str(r[1]),
                "address": str(r[2]),
                "text": str(r[3]),
                "is_content": bool(r[4]),
                "ordinal": r[5],
            }
            for r in passages
        ],
    }


def plan_of(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """The frozen plan shape the generators see, from the stored blocks."""
    topics: dict[str, dict[str, Any]] = {}
    for block in blocks:
        topic = topics.setdefault(
            block["topic"],
            {
                "plan_node_id": block["topic"],
                "kind": block["topic_kind"],
                "title": block["topic_title"],
                "objectives": block["objectives"],
                "blocks": [],
            },
        )
        topic["blocks"].append(
            {
                "plan_node_id": block["node"],
                "role": block["role"],
                "block_type": block["block_type"],
                "source_handles": list(block["plan_content"].get("source_handles", [])),
                "source_passages": dict(block["plan_content"].get("source_passages", {})),
            }
        )
    return {"modules": [{"plan_node_id": "m", "topics": list(topics.values())}]}


def generator_output(block: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the generator reply shape from stored content plus key."""
    content, key = block["content"], block["answer_key"]
    out: dict[str, Any] = {"answer": str(key.get("answer", ""))}
    if block["block_type"] == "choice":
        correct = set(key.get("correct", []))
        out["options"] = [
            {"text": str(text), "correct": index in correct}
            for index, text in enumerate(content.get("options", []))
        ]
    elif block["block_type"] == "cloze":
        out["blanks"] = list(key.get("blanks", []))
    elif block["block_type"] == "matching":
        out["pairs"] = list(content.get("pairs", []))
    elif block["block_type"] == "scenario_practice":
        labels = dict(key.get("labels_by_index", {}))
        out["items"] = [
            {"text": str(item.get("text", "")), "tier": str(labels.get(str(i), ""))}
            for i, item in enumerate(content.get("items", []))
            if isinstance(item, dict)
        ]
    return out


def block_text(block: dict[str, Any]) -> str:
    content = block["content"]
    for key in ("body", "stem", "text", "scenario", "caption"):
        if isinstance(content.get(key), str) and content[key]:
            return str(content[key])
    return ""


def replay(course: dict[str, Any]) -> dict[str, Any]:
    """Run every check and return the per-block verdicts."""
    from app.agents import architect
    from app.course import grounding
    from app.course.handles import numbered_passages

    numbered = numbered_passages(course["passages"])
    # absorbed paragraphs (list items joined into their intro) resolve to their unit
    by_id: dict[str, dict[str, Any]] = {}
    handle_of: dict[str, str] = {}
    for passage in numbered:
        for pid in [passage["passage_id"], *passage.get("merged_ids", [])]:
            by_id.setdefault(str(pid), passage)
            handle_of.setdefault(str(pid), passage["handle"])
    plan = plan_of(course["blocks"])
    rows: list[dict[str, Any]] = []
    for block in course["blocks"]:
        if not block["version_id"] or block["block_type"] == "spot_the_error":
            continue
        if not block_text(block) and not block["content"].get("pairs"):
            continue  # a plan skeleton, nothing generated yet
        text = block_text(block)
        if not block["citations"]:
            # a held block has no citation rows: read the handles the generator wrote
            raw = block["content"].get("citations")
            if isinstance(block["content"].get("citation"), dict):
                raw = [block["content"]["citation"]]
            by_handle = {p["handle"]: p["passage_id"] for p in numbered}
            block["citations"] = [
                (by_handle.get(str(c.get("handle", "")), ""), str(c.get("quote", "")))
                for c in (raw if isinstance(raw, list) else [])
                if isinstance(c, dict) and by_handle.get(str(c.get("handle", "")))
            ]
        cited_pids = list(dict.fromkeys(pid for pid, _ in block["citations"]))
        cited_handles = [handle_of.get(pid, "?") for pid in cited_pids]
        cited_texts = {
            handle_of.get(pid, pid): by_id.get(pid, {}).get("text", "") for pid in cited_pids
        }
        holds: list[str] = []
        if block["role"] in ("teach", "example") and block["block_type"] != "figure":
            holds += grounding.coverage_holds(text, cited_texts)
            holds += grounding.adjacency_holds(
                text,
                [
                    {"handle": handle_of.get(pid, "?"), "quote": quote}
                    for pid, quote in block["citations"]
                ],
            )
        if block["block_type"] == "figure":
            kinds = {
                grounding.passage_kind(by_id.get(pid, {}).get("text", "")) for pid in cited_pids
            }
            if kinds and kinds <= {"prose"}:
                holds.append("figure: cited passage has no numbers or structure to draw")
        if block["role"] in QUESTION_ROLES:
            taught = grounding.taught_handles_for(plan, block["node"])
            taught_pids: list[str] = []
            if taught is not None:
                stored = {}
                for topic in plan["modules"][0]["topics"]:
                    for item in topic["blocks"]:
                        stored.update(item.get("source_passages", {}))
                taught_pids = [str(stored[h]) for h in taught[0] if h in stored]
            taught_texts = [
                by_id.get(pid, {}).get("text", "") for pid in (taught_pids or cited_pids)
            ]
            holds += grounding.taught_holds(
                generator_output(block), taught_texts, block["block_type"]
            )
        for pid, quote in block["citations"]:
            if quote and quote not in by_id.get(pid, {}).get("text", ""):
                holds.append(f"quote is not verbatim in its passage ({handle_of.get(pid, '?')})")
        rows.append(
            {
                "node": block["node"],
                "topic": block["topic_title"],
                "role": block["role"],
                "type": block["block_type"],
                "stored_status": block["status"],
                "stored_reason": block["hold_reason"][:80],
                "cited": cited_handles,
                "holds": holds,
            }
        )
    plan_errors: list[str] = []
    try:
        arch_plan = {
            "modules": [
                {
                    "plan_node_id": "m",
                    "title": "m",
                    "purpose": "p",
                    "entry_knowledge": "e",
                    "exit_knowledge": "x",
                    "topics": [
                        {
                            **topic,
                            "objectives": topic["objectives"] or ["o"],
                            "through_line_note": "n",
                            "blocks": [
                                {
                                    "plan_node_id": b["plan_node_id"],
                                    "role": b["role"],
                                    "block_type": b["block_type"],
                                    "brief": "b?" if b["role"] in QUESTION_ROLES else "b",
                                    "source_refs": [{"handle": h} for h in b["source_handles"]],
                                }
                                for b in topic["blocks"]
                            ],
                        }
                        for topic in plan["modules"][0]["topics"]
                    ],
                }
            ],
            "estimate_inputs": {"blocks": 1, "calls": 1, "tokens": 1},
            "through_line": {"case": "", "handoffs": []},
            "uncovered": [],
        }
        from app.course.tree import ArchitectOutput

        plan_errors = architect._coverage_errors(
            ArchitectOutput.model_validate(arch_plan),
            [
                {"handle": p["handle"], "text": p["text"], "is_content": p["is_content"]}
                for p in numbered
            ],
        )
    except Exception as exc:  # the plan shape itself may not validate
        plan_errors = [f"plan contract: {exc}"[:200]]
    return {"blocks": rows, "plan_errors": plan_errors, "passages": len(numbered)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--course", required=True)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument("--json", default="")
    args = parser.parse_args()
    if not args.database_url:
        print("replay_holds: set --database-url or DATABASE_URL", file=sys.stderr)
        return 2
    course = asyncio.run(load_course(args.database_url, args.course))
    result = replay(course)
    held = 0
    for row in result["blocks"]:
        mark = "HOLD" if row["holds"] else "pass"
        held += bool(row["holds"])
        print(
            f"{mark:<4} {row['role']:<8} {row['type']:<17} {row['node']:<10}"
            f" {row['topic'][:28]:<28} cited={','.join(row['cited'])}"
        )
        for hold in row["holds"]:
            print(f"       - {hold[:160]}")
    print(
        f"replay_holds: {len(result['blocks'])} blocks, {held} would hold,"
        f" {result['passages']} passages, plan errors: {len(result['plan_errors'])}"
    )
    for error in result["plan_errors"]:
        print(f"  plan: {error[:200]}")
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
