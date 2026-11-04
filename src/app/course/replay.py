"""Replay the code checks over stored blocks, offline and without the model.

One form for two callers: `scripts/replay_holds.py` prints what each check
would hold today; `services.recheck` lifts a hold that no current check
finds. A line this module can recompute is a code hold (`is_code_hold`);
reader lines, assignment lines (leftovers are not stored), non-verbatim
quotes (the executor drops them from stored content) and language or shape
lines are kept as they were.
"""

from __future__ import annotations

from typing import Any

QUESTION_ROLES = ("check", "practice", "test")

CODE_HOLD_PREFIXES = (
    "coverage: cited passage ",
    "false adjacency: ",
    "not taught: the key claim ",
    "figure: cited passage has no numbers",
    "cloze: ",
    "matching: ",
    "ordering: ",
    "giveaway: ",
    "narration: ",
    "code: ",
    "escape: ",
)


# Lines judged on the answer key. A held draft is stored without its key, so the replay
# cannot recompute them from it: recheck keeps such a line as stored (unknown is not passed).
KEY_HOLD_PREFIXES = ("giveaway: ", "cloze: ", "not taught: ")


def join_hold_lines(lines: list[str]) -> str:
    """The stored `hold_reason`: lines joined by "; ", none of which may carry "; " itself.

    `recheck_held` splits the reason back on "; ", so a quoted run that holds one
    (code with a semicolon, a key clause) would leave a tail that no code check owns.
    """
    return "; ".join(line.replace("; ", ", ") for line in lines)


def is_code_hold(line: str) -> bool:
    """True for a hold line that `replay_blocks` recomputes from stored content."""
    return line.startswith(CODE_HOLD_PREFIXES)


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
    elif block["block_type"] == "ordering":
        out["steps"] = [str(s) for s in content.get("items", [])]
    elif block["block_type"] == "diagram":
        out["nodes"] = list(content.get("nodes", []))
        out["edges"] = list(content.get("edges", []))
    elif block["block_type"] == "scenario_practice":
        labels = dict(key.get("labels_by_index", {}))
        out["items"] = [
            {"text": str(item.get("text", "")), "tier": str(labels.get(str(i), ""))}
            for i, item in enumerate(content.get("items", []))
            if isinstance(item, dict)
        ]
    return out


def block_text(block: dict[str, Any]) -> str:
    """The learner-facing text of a stored block, whichever field carries it."""
    content = block["content"]
    for key in ("body", "stem", "text", "scenario", "prompt", "caption"):
        if isinstance(content.get(key), str) and content[key]:
            return str(content[key])
    return ""


def replay_blocks(
    blocks: list[dict[str, Any]], numbered: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Run every code check over the stored blocks; one row per generated block."""
    from app.course import grounding
    from app.course.exercise_shape import exercise_holds
    from app.course.text_checks import escape_holds, narration_holds, python_code_holds

    by_id: dict[str, dict[str, Any]] = {}
    handle_of: dict[str, str] = {}
    for passage in numbered:
        for pid in [passage["passage_id"], *passage.get("merged_ids", [])]:
            by_id.setdefault(str(pid), passage)
            handle_of.setdefault(str(pid), passage["handle"])
    by_handle = {p["handle"]: p["passage_id"] for p in numbered}
    plan = plan_of(blocks)
    rows: list[dict[str, Any]] = []
    for block in blocks:
        if not block["version_id"] or block["block_type"] == "spot_the_error":
            continue
        if not block_text(block) and not block["content"].get("pairs"):
            continue  # a plan skeleton, nothing generated yet
        text = block_text(block)
        citations = list(block["citations"])
        if not citations:
            # a held block has no citation rows: read the handles the generator wrote
            raw = block["content"].get("citations")
            if isinstance(block["content"].get("citation"), dict):
                raw = [block["content"]["citation"]]
            citations = [
                (by_handle.get(str(c.get("handle", "")), ""), str(c.get("quote", "")))
                for c in (raw if isinstance(raw, list) else [])
                if isinstance(c, dict) and by_handle.get(str(c.get("handle", "")))
            ]
        cited_pids = list(dict.fromkeys(pid for pid, _ in citations))
        cited_handles = [handle_of.get(pid, "?") for pid in cited_pids]
        cited_texts = {
            handle_of.get(pid, pid): by_id.get(pid, {}).get("text", "") for pid in cited_pids
        }
        holds: list[str] = []
        if block["role"] in ("teach", "example") and block["block_type"] != "figure":
            holds += grounding.coverage_holds(text, cited_texts)
            holds += grounding.adjacency_holds(
                text,
                [{"handle": handle_of.get(pid, "?"), "quote": quote} for pid, quote in citations],
            )
        if block["role"] in ("teach", "example") and block["block_type"] not in (
            "figure",
            "diagram",
        ):
            holds += narration_holds(text, list(cited_texts.values()))
        if block["block_type"] != "cloze":
            holds += python_code_holds(text)
        holds += escape_holds(text)
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
                stored: dict[str, Any] = {}
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
            holds += exercise_holds(generator_output(block), block["block_type"], taught_texts)
        for pid, quote in citations:
            if (
                quote
                and grounding.locate_verbatim(by_id.get(pid, {}).get("text", ""), quote) is None
            ):
                holds.append(f"quote is not verbatim in its passage ({handle_of.get(pid, '?')})")
        rows.append(
            {
                "id": block["id"],
                "node": block["node"],
                "topic": block["topic_title"],
                "topic_node": block["topic"],
                "version_id": block["version_id"],
                "role": block["role"],
                "type": block["block_type"],
                "stored_status": block["status"],
                "stored_reason": block["hold_reason"],
                "cited": cited_handles,
                "holds": holds,
                "author_accepted": bool(block.get("author_accepted")),
                "keyed": bool(block["answer_key"]),
            }
        )
    return rows


def plan_coverage_errors(blocks: list[dict[str, Any]], numbered: list[dict[str, Any]]) -> list[str]:
    """The architect's plan-level coverage verdict over the stored plan."""
    from app.agents import architect
    from app.course.tree import ArchitectOutput

    plan = plan_of(blocks)
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
        return architect._coverage_errors(
            ArchitectOutput.model_validate(arch_plan),
            [
                {"handle": p["handle"], "text": p["text"], "is_content": p["is_content"]}
                for p in numbered
            ],
        )
    except Exception as exc:  # the plan shape itself may not validate
        return [f"plan contract: {exc}"[:200]]
