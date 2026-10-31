"""Learner answers: grading by each type's check.py, attempts, counters, module test.

Grading is code only. The grader reads the key of the language the
learner was served (learner_block), submissions are normalised to the
shapes the checks compare (spot sentence index, passage handles), and
every attempt is a new learner_answers row: attempts never lock.
Caller: controllers/learn.py (POST answer, POST module-test submit).
"""

from __future__ import annotations

import json
from importlib import import_module
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ValidationError
from app.rbac.principal import Principal
from app.services import learner

EMPTY_COUNTS = {"hits": 0, "false_alarms": 0, "misses": 0}


def _bounded(payload: dict[str, Any], limit: int) -> None:
    if len(json.dumps(payload, ensure_ascii=False)) > limit:
        raise ValidationError("the answer is too large")


def _spot_submission(
    payload: dict[str, Any], sentences: list[str], passage_handle: dict[str, str]
) -> dict[str, Any]:
    """{sentence_id|none, kind, passage_id, corrected_value} -> the check's shape."""
    sentence_id = payload.get("sentence_id")
    if sentence_id is None or str(sentence_id).strip() == "":
        raise ValidationError("name a sentence number, or answer 'none' when every sentence holds")
    submission: dict[str, Any] = {
        "kind": payload.get("kind") or "",
        "correct_value": payload.get("corrected_value") or "",
        "passage": passage_handle.get(str(payload.get("passage_id") or ""), ""),
    }
    if str(sentence_id).strip().lower() == "none":
        submission["sentence"] = "none"
        return submission
    if isinstance(sentence_id, bool) or not str(sentence_id).isdigit():
        raise ValidationError("sentence_id must be a sentence number or 'none'")
    index = int(str(sentence_id))
    if index >= len(sentences):
        raise ValidationError("sentence_id is outside the exercise")
    submission["sentence_index"] = index
    submission["sentence"] = sentences[index]
    return submission


def _normalise(
    block: dict[str, Any],
    payload: dict[str, Any],
    resolved: dict[str, Any],
    handles: dict[str, str],
) -> dict[str, Any]:
    by_pid = {pid: handle for handle, pid in handles.items()}
    kind = block["block_type"]
    if kind == "spot_the_error":
        return _spot_submission(payload, list(resolved["content"].get("sentences", [])), by_pid)
    if kind == "scenario_practice":
        passages = payload.get("passages")
        if isinstance(passages, dict):
            return {**payload, "passages": {k: by_pid.get(str(v), v) for k, v in passages.items()}}
    return payload


def _feedback(
    block: dict[str, Any],
    raw: dict[str, Any],
    resolved: dict[str, Any],
    chips_by_handle: dict[str, dict[str, str]],
) -> dict[str, Any]:
    fb = dict(raw.get("feedback", {}))
    passage = chips_by_handle.get(str(fb.get("passage", "")))
    if block["block_type"] == "spot_the_error":
        return {
            "wrong_sentence": fb.get("wrong", ""),
            "true_statement": fb.get("true", ""),
            "passage": passage,
            "habit_line": fb.get("habit", ""),
        }
    if block["block_type"] == "choice":
        options = list(resolved["content"].get("options", []))
        correct = resolved["answer_key"].get("correct")
        indices = correct if isinstance(correct, list) else [correct]
        fb["true"] = "; ".join(
            str(options[i]) for i in indices if isinstance(i, int) and i < len(options)
        )
    return {
        "wrong": fb.get("wrong", ""),
        "true": fb.get("true", ""),
        "passage": passage,
        "habit": fb.get("habit", ""),
    }


async def _next_attempt(session: AsyncSession, user_id: UUID, block_id: str) -> int:
    await session.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"{user_id}:{block_id}"}
    )
    row = (
        await session.execute(
            sa.text(
                "SELECT COALESCE(max(attempt_no), 0) + 1 FROM learner_answers"
                " WHERE user_id = :u AND block_id = :b"
            ),
            {"u": str(user_id), "b": block_id},
        )
    ).one()
    return int(row[0])


async def grade_and_store(
    session: AsyncSession,
    principal: Principal,
    course: dict[str, Any],
    block: dict[str, Any],
    payload: dict[str, Any],
    *,
    language: str,
    max_bytes: int,
) -> dict[str, Any]:
    """Grade one submission with the type's check, store the attempt, shape the reply."""
    from app.course.errorspot import classify_spot_attempt
    from app.translation.variants import learner_block

    _bounded(payload, max_bytes)
    resolved = await learner_block(
        session,
        course_id=UUID(course["id"]),
        language=language,
        block={
            "plan_node_id": block["plan_node_id"],
            "content": block["content"],
            "answer_key": block["answer_key"],
        },
        source_language=course["language"],
    )
    rows = await learner.citation_rows(session, block["version_id"])
    handles, _ = learner.handle_maps(block, rows)
    locale = learner.locale_of(language)
    chip_list = learner.chips_of(rows, handles, locale)
    chips_by_handle = {c["handle"]: c for c in chip_list if c["handle"]}
    submission = _normalise(block, payload, resolved, handles)
    check = import_module(f"app.blocks.{block['block_type']}.check").grade
    raw = check(resolved["content"], resolved["answer_key"], submission)
    score = float(raw["score"])
    result: dict[str, Any] = {}
    if block["block_type"] == "spot_the_error":
        from app.blocks.spot_the_error.check import planted_view

        result = classify_spot_attempt(
            planted_view(resolved["content"], resolved["answer_key"]), submission
        )
    attempt_no = await _next_attempt(session, principal.user_id, block["id"])
    await session.execute(
        sa.text(
            "INSERT INTO learner_answers (user_id, block_id, block_version_id, language,"
            " payload, score, attempt_no, result) VALUES (:u, :b, :v, :l,"
            " CAST(:p AS jsonb), :s, :n, CAST(:r AS jsonb))"
        ),
        {
            "u": str(principal.user_id),
            "b": block["id"],
            "v": block["version_id"],
            "l": resolved["language"],
            "p": json.dumps(payload, ensure_ascii=False),
            "s": score,
            "n": attempt_no,
            "r": json.dumps(result),
        },
    )
    hint = None
    if score < 1.0 and block["block_type"] != "spot_the_error" and chip_list:
        hint = chip_list[0]
    reply: dict[str, Any] = {
        "block_id": block["id"],
        "correct": score >= 1.0,
        "partial": 0.0 < score < 1.0,
        "score": score,
        "feedback": _feedback(block, raw, resolved, chips_by_handle),
        "hint_from_source": hint,
        "attempt_no": attempt_no,
        "source_links": chip_list,
    }
    if block["block_type"] == "spot_the_error":
        reply["counters"] = await spot_counters(session, principal.user_id, UUID(course["id"]))
    return reply


async def spot_counters(session: AsyncSession, user_id: UUID, course_id: UUID) -> dict[str, Any]:
    """Hits, false alarms and misses per error kind: this learner, plus `course` for all."""
    rows = (
        await session.execute(
            sa.text(
                "SELECT la.user_id, la.result FROM learner_answers la"
                " JOIN blocks b ON b.id = la.block_id JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :c AND la.result ? 'hit'"
            ),
            {"c": str(course_id)},
        )
    ).all()
    mine: dict[str, dict[str, int]] = {}
    course: dict[str, dict[str, int]] = {}
    for owner, result in rows:
        for bucket in (course, mine) if str(owner) == str(user_id) else (course,):
            add_counts(bucket, dict(result))
    return {**mine, "course": course}


def add_counts(bucket: dict[str, dict[str, int]], result: dict[str, Any]) -> None:
    """Fold one classified attempt into per-kind counters."""
    kind = result.get("kind")
    if kind and result.get("hit"):
        bucket.setdefault(str(kind), dict(EMPTY_COUNTS))["hits"] += 1
    if kind and result.get("miss"):
        bucket.setdefault(str(kind), dict(EMPTY_COUNTS))["misses"] += 1
    if result.get("false_alarm"):
        bucket.setdefault(str(result.get("false_alarm_kind") or "unspecified"), dict(EMPTY_COUNTS))[
            "false_alarms"
        ] += 1


async def answer_block(
    session: AsyncSession,
    principal: Principal,
    block_id: UUID,
    payload: dict[str, Any],
    *,
    language: str | None,
    max_bytes: int,
    threshold: float,
) -> dict[str, Any]:
    """POST /learn/blocks/{id}/answer: enrolled, published, graded, stored."""
    course_id = await learner.course_of_block(session, block_id)
    course = await learner.enrolled_course(session, principal, course_id, code="learn.answer")
    topics = await learner.topic_entries(session, course)
    topic = next((t for t in topics if any(b["id"] == str(block_id) for b in t["blocks"])), None)
    if topic is None:
        raise learner._not_found()
    block = next(b for b in topic["blocks"] if b["id"] == str(block_id))
    reply = await grade_and_store(
        session,
        principal,
        course,
        block,
        payload,
        language=language or course["language"],
        max_bytes=max_bytes,
    )
    attempts = await learner.attempts_of(
        session, principal.user_id, [b["id"] for b in topic["blocks"]]
    )
    reply["topic_mastery"] = learner.progress_of(topic["blocks"], attempts, threshold)
    return reply


async def submit_module_test(
    session: AsyncSession,
    principal: Principal,
    topic_id: UUID,
    answers: dict[str, dict[str, Any]],
    *,
    language: str | None,
    max_bytes: int,
    threshold: float,
) -> dict[str, Any]:
    """Grade a whole module-test page; retakes add attempts, unanswered scores 0."""
    course, topic, _ = await learner.find_topic(session, principal, topic_id, code="learn.answer")
    if topic["kind"] != "module_test":
        raise ValidationError("only a module test is submitted as a page")
    by_id = {b["id"]: b for b in topic["blocks"]}
    if set(answers) - set(by_id):
        raise ValidationError("answers name a block outside this test")
    results: list[dict[str, Any]] = []
    for block in topic["blocks"]:
        if block["id"] in answers:
            results.append(
                await grade_and_store(
                    session,
                    principal,
                    course,
                    block,
                    answers[block["id"]],
                    language=language or course["language"],
                    max_bytes=max_bytes,
                )
            )
    assessed = [b for b in topic["blocks"] if b["block_type"] in learner.ASSESSED_TYPES]
    scored = {r["block_id"]: r["score"] for r in results}
    total = sum(scored.get(b["id"], 0.0) for b in assessed)
    attempts = await learner.attempts_of(session, principal.user_id, list(by_id))
    return {
        "topic_id": topic["id"],
        "score": round(total / len(assessed), 4) if assessed else 0.0,
        "unanswered": len(topic["blocks"]) - len(results),
        "results": results,
        "mastery": learner.progress_of(topic["blocks"], attempts, threshold),
    }
