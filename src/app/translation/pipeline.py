"""Translation workflow: glossary-first, per-block steps, code-checked.

One run per translation request: a glossary step per target language,
then one translator step per block. Every model call flows through the
run executor (the only generate_json caller), so unchanged blocks are
skipped by the reply store and a second identical run pays zero.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.translation import checks, memory, segments

STATUSES = ("ok", "held", "failed")


def compile_translation_dag(
    *,
    blocks: list[dict[str, Any]],
    source_language: str,
    target_languages: list[str],
    glossary: dict[str, str],
    course_id: str = "",
) -> list[dict[str, Any]]:
    """DAG entries: one glossary step per language, one step per block."""
    dag: list[dict[str, Any]] = []
    for language in target_languages:
        glossary_key = f"translator_glossary:{language}"
        dag.append(
            {
                "step_key": glossary_key,
                "handler": "translate_glossary",
                "depends_on": [],
                "course_id": course_id,
                "source_language": source_language,
                "target_language": language,
                "terms": dict(glossary),
            }
        )
        for block in blocks:
            dag.append(
                {
                    "step_key": f"translator:{block['plan_node_id']}:{language}",
                    "handler": "translate_block",
                    "depends_on": [glossary_key],
                    "course_id": course_id,
                    "plan_node_id": block["plan_node_id"],
                    "block_type": block["block_type"],
                    "content": block["content"],
                    "source_language": source_language,
                    "target_language": language,
                }
            )
    return dag


def block_input_digest(block: dict[str, Any], target_language: str) -> str:
    """Stable idempotency key: block content plus target language."""
    from app.runs.executor import input_digest_of

    return input_digest_of(
        {"block": block["content"], "type": block["block_type"], "lang": target_language}
    )


async def translate_course(
    session: AsyncSession,
    settings: Any,
    *,
    course_id: UUID,
    version_id: UUID,
    source_language: str,
    target_languages: list[str],
    blocks: list[dict[str, Any]],
    glossary: dict[str, str],
    driver: Callable[[UUID], Any],
) -> UUID:
    """Translate one course version; return the workflow run id."""
    from app.runs import executor

    _ = settings
    dag = compile_translation_dag(
        blocks=blocks,
        source_language=source_language,
        target_languages=target_languages,
        glossary=glossary,
        course_id=str(course_id),
    )
    run_id = await executor.new_run(session, course_id=course_id, kind="translation", dag=dag)
    await session.commit()
    await driver(run_id)
    outputs = await _step_outputs(session, run_id=run_id)
    for language in target_languages:
        term_table = _term_table(outputs.get(f"translator_glossary:{language}"), glossary)
        judged = []
        for block in blocks:
            key = f"translator:{block['plan_node_id']}:{language}"
            judged.append(
                await _judge_block(
                    session,
                    block=block,
                    output=outputs.get(key),
                    source_language=source_language,
                    target_language=language,
                    term_table=term_table,
                )
            )
        await _store_translation(
            session,
            version_id=version_id,
            source_language=source_language,
            language=language,
            term_table=term_table,
            judged=judged,
            run_id=run_id,
        )
    await session.commit()
    return run_id


async def _step_outputs(session: AsyncSession, *, run_id: UUID) -> dict[str, Any]:
    rows = (
        await session.execute(
            sa.text(
                "SELECT step_key, output FROM run_steps WHERE run_id = :run AND status = 'done'"
            ),
            {"run": str(run_id)},
        )
    ).all()
    return {str(key): output for key, output in rows if isinstance(output, dict)}


def _term_table(output: dict[str, Any] | None, glossary: dict[str, str]) -> dict[str, str]:
    if isinstance(output, dict) and isinstance(output.get("terms"), dict):
        table = {str(k): str(v) for k, v in output["terms"].items()}
        if set(table) == set(glossary):
            return table
    return dict(glossary)


async def _judge_block(
    session: AsyncSession,
    *,
    block: dict[str, Any],
    output: dict[str, Any] | None,
    source_language: str,
    target_language: str,
    term_table: dict[str, str],
) -> dict[str, Any]:
    block_type = str(block["block_type"])
    content = dict(block["content"])
    if output is None:
        return _verdict(block, "failed", "translator step produced no output")
    translations = output.get("translations")
    if not isinstance(translations, dict):
        return _verdict(block, "failed", "translator step produced no segments")
    planted_id = segments.planted_segment_id(content)
    fresh = {seg_id: text for seg_id, text in translations.items() if seg_id != planted_id}
    for seg_id, text in fresh.items():
        await memory.tm_store(
            session,
            source_text=_source_text(content, block_type, seg_id),
            translated_text=str(text),
            source_language=source_language,
            target_language=target_language,
        )
    problems: list[str] = []
    try:
        rebuilt = segments.reassemble(block_type, content, translations)
    except Exception as exc:
        return _verdict(block, "failed", f"re-assembly failed: {exc}")
    problems += _validate_against_schema(block_type, rebuilt)
    problems += checks.verify_block_translation(block_type, content, rebuilt)
    for seg in segments.extract_segments(block_type, content):
        problems += [
            f"segment {seg['id']}: {problem}"
            for problem in checks.check_segment_pair(
                seg["text"], str(translations.get(seg["id"], ""))
            )
        ]
    problems += checks.check_glossary_use(
        segments.extract_segments(block_type, content), translations, term_table
    )
    if block_type == "spot_the_error":
        problems += checks.verify_spot_error(content, rebuilt, dict(block.get("answer_key", {})))
    if problems:
        return _verdict(block, "held", "; ".join(problems), rebuilt)
    return _verdict(block, "ok", "", rebuilt)


def _source_text(content: dict[str, Any], block_type: str, segment_id: str) -> str:
    for seg in segments.extract_segments(block_type, content):
        if seg["id"] == segment_id:
            return seg["text"]
    return ""


def _validate_against_schema(block_type: str, content: dict[str, Any]) -> list[str]:
    from importlib import import_module

    try:
        schema_mod = import_module(f"app.blocks.{block_type}.schema")
    except ImportError:
        return [f"unknown block type: {block_type}"]
    return [f"schema: {error}" for error in schema_mod.validate(content)]


def _verdict(
    block: dict[str, Any], status: str, reason: str, content: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "plan_node_id": str(block["plan_node_id"]),
        "block_type": str(block["block_type"]),
        "status": status,
        "reason": reason,
        "content": content if content is not None else dict(block["content"]),
    }


async def _store_translation(
    session: AsyncSession,
    *,
    version_id: UUID,
    source_language: str,
    language: str,
    term_table: dict[str, str],
    judged: list[dict[str, Any]],
    run_id: UUID,
) -> None:
    ok = sum(1 for item in judged if item["status"] == "ok")
    status = "ready" if ok == len(judged) and judged else ("partial" if ok else "failed")
    row = (
        await session.execute(
            sa.text(
                "INSERT INTO course_translations"
                " (id, course_version_id, source_language, language, status,"
                " glossary, run_id)"
                " VALUES (gen_random_uuid(), :version, :source, :lang, :status,"
                " CAST(:glossary AS jsonb), :run)"
                " ON CONFLICT (course_version_id, language) DO UPDATE SET"
                " status = EXCLUDED.status, glossary = EXCLUDED.glossary,"
                " run_id = EXCLUDED.run_id"
                " RETURNING id"
            ),
            {
                "version": str(version_id),
                "source": source_language,
                "lang": language,
                "status": status,
                "glossary": json.dumps(term_table, ensure_ascii=False),
                "run": str(run_id),
            },
        )
    ).one()
    translation_id = str(row[0])
    for item in judged:
        await session.execute(
            sa.text(
                "INSERT INTO translation_blocks"
                " (id, translation_id, plan_node_id, block_type, status, reason, content)"
                " VALUES (gen_random_uuid(), :translation, :node, :type, :status,"
                " :reason, CAST(:content AS jsonb))"
                " ON CONFLICT (translation_id, plan_node_id) DO UPDATE SET"
                " block_type = EXCLUDED.block_type, status = EXCLUDED.status,"
                " reason = EXCLUDED.reason, content = EXCLUDED.content"
            ),
            {
                "translation": translation_id,
                "node": item["plan_node_id"],
                "type": item["block_type"],
                "status": item["status"],
                "reason": item["reason"] or None,
                "content": json.dumps(item["content"], ensure_ascii=False),
            },
        )
