"""Acceptance checks of the generation live test, over parsed ledger requests.

Every check reads the PARSED JSON of the logged reader/saboteur requests
(never a substring of the raw prompt, where the system prompt's own
words would always match) and the current block rows. Pure functions, no
database: the live test (tests/test_live_generate.py) calls them on the
real run; they are exercised on mock-provider data too, so each check is
known to fail on the defect it exists for. Caller: test_live_generate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

_TASK_MARKER = "## Task input (typed, assembled by code)\n"


@dataclass(frozen=True)
class Block:
    """One current block of the course under test."""

    node: str
    block_type: str
    status: str
    content: dict[str, Any]
    key: dict[str, Any]
    topic: str


def section(prompt: str, name: str) -> Any:
    """Parse one code-assembled prompt section back into JSON."""
    marker = f"## {name}\n"
    start = prompt.index(marker) + len(marker)
    rest = prompt[start:]
    end = rest.find("\n\n## ")
    return json.loads(rest[:end] if end != -1 else rest)


class ReaderRequest:
    """One logged reader request, parsed: topic, blocks by node, handle to passage text."""

    def __init__(self, prompt: str) -> None:
        skeleton = section(prompt, "Block skeleton")
        self.topic = str(skeleton["topic_plan_node_id"])
        self.blocks: dict[str, dict[str, Any]] = {
            str(b["plan_node_id"]): b for b in skeleton["blocks"]
        }
        sources = section(prompt, "Approved sources")["sources"]
        self.passages = {str(s["handle"]): str(s["text"]) for s in sources}


def check_reader_saw_every_block(blocks: list[Block], readers: list[ReaderRequest]) -> None:
    """The LAST reader request naming a block carries that block's full current JSON.

    Content and key must equal the stored current version (so a reworked
    block was read again in its final form), and every option string,
    every sentence and the planted index must sit inside the block's own
    JSON, not merely somewhere in the request.
    """
    for block in blocks:
        seen = [r for r in readers if block.node in r.blocks]
        assert seen, f"no reader request carried {block.node}"
        entry = seen[-1].blocks[block.node]
        assert entry["content"] == block.content, (block.node, "content differs", entry["content"])
        assert entry["answer_key"] == block.key, (block.node, "key differs", entry["answer_key"])
        inside = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        for option in block.content.get("options", []):
            assert json.dumps(option, ensure_ascii=False) in inside, (block.node, option)
        for sentence in block.content.get("sentences", []):
            assert json.dumps(sentence, ensure_ascii=False) in inside, (block.node, sentence)
        if block.block_type == "spot_the_error":
            planted = block.key["planted_index"]
            assert entry["planted_index"] == planted, (block.node, entry.get("planted_index"))
            assert f'"planted_index": {planted}' in inside, (block.node, planted)


def check_held_blocks_were_read_again(blocks: list[Block], readers: list[ReaderRequest]) -> None:
    """A block that ends held was reworked, so its topic was read at least twice."""
    for block in blocks:
        if block.status != "held":
            continue
        topic_reads = [r for r in readers if r.topic == block.topic and block.node in r.blocks]
        assert len(topic_reads) >= 2, (block.node, "held but never read again", len(topic_reads))


def _saboteur_task(prompt: str) -> dict[str, Any]:
    return dict(json.loads(prompt.split(_TASK_MARKER, 1)[1]))


def check_spot_exercises(
    blocks: list[Block], readers: list[ReaderRequest], saboteur_prompts: list[str]
) -> None:
    """The planted sentence, the key and the passage come from ONE pick.

    true_sentence must sit inside the passage the key names (that
    passage resolved through the SAME request's handle table, not any
    passage of the course); the planted sentence differs from it and is
    what the learner sees at planted_index; for model-rewritten kinds
    the saboteur request carried exactly the true sentence and kind.
    """
    tasks = [_saboteur_task(p) for p in saboteur_prompts]
    spots = [b for b in blocks if b.block_type == "spot_the_error"]
    assert spots, "the course holds no spot_the_error block"
    for spot in spots:
        key, content = spot.key, spot.content
        if key.get("fully_correct"):
            assert key["planted_index"] == -1 and key["planted_sentence"] == "", spot.node
            continue
        reader = next(r for r in reversed(readers) if spot.node in r.blocks)
        passage = reader.passages.get(str(key["passage"]))
        assert passage is not None, (spot.node, "key passage is not a handle of the request")
        true_sentence = str(key["true_sentence"])
        assert true_sentence.rstrip(".") in passage, (spot.node, "true sentence not in its passage")
        assert "Reuse notice" not in true_sentence, spot.node
        assert key["planted_sentence"] != true_sentence, spot.node
        assert content["sentences"][int(key["planted_index"])] == key["planted_sentence"], spot.node
        assert true_sentence not in content["sentences"], (spot.node, "true sentence shown")
        if key["kind"] != "number_date":
            sent = [t for t in tasks if t["sentence"] == true_sentence and t["kind"] == key["kind"]]
            assert sent, (spot.node, "no saboteur request carried the key's sentence and kind")
