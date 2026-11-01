"""Translation workflow: glossary-first, per-block steps, code-checked.

One run per translation request: a glossary step per target language,
then one translator step per block. Every model call flows through the
run executor (the only generate_json caller), so unchanged blocks are
skipped by the reply store and a second identical run pays zero.

Judgement lives here, after the drive: language detection per block,
per-segment checks, schema, glossary, and the planted-error invariant.
Only blocks judged ok enter translation memory; a bad reply holds its
block and never fails the run.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.translation import checks, memory, segments
from app.translation.keys import build_translated_answer_key
from app.translation.variants import block_digest

STATUSES = ("ok", "held", "failed")


def compile_translation_dag(
    *,
    blocks: list[dict[str, Any]],
    source_language: str,
    target_languages: list[str],
    glossary: dict[str, str],
    course_id: str = "",
    version_id: str = "",
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
                "version_id": version_id,
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
                    "version_id": version_id,
                    "plan_node_id": block["plan_node_id"],
                    "block_type": block["block_type"],
                    "content": block["content"],
                    "answer_key": dict(block.get("answer_key", {})),
                    "source_language": source_language,
                    "target_language": language,
                }
            )
    return dag


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
        version_id=str(version_id),
    )
    run_id = await executor.new_run(session, course_id=course_id, kind="translation", dag=dag)
    await session.commit()
    await driver(run_id)
    await finalize_translation_run(session, run_id=run_id)
    return run_id


async def finalize_translation_run(session: AsyncSession, *, run_id: UUID) -> None:
    """Judge every translated block of a driven run and store the rows.

    Shared by translate_course and by the worker/resume path: the DAG
    stored on the run carries blocks, languages, and the version, so a
    resumed translation run finalises exactly like a fresh one.
    """
    row = (
        await session.execute(
            sa.text("SELECT compiled_dag, course_id FROM runs WHERE id = :run"),
            {"run": str(run_id)},
        )
    ).first()
    if row is None:
        return
    dag = row[0] or []
    course_id = str(row[1]) if row[1] else ""
    outputs = await _step_outputs(session, run_id=run_id)
    languages: list[str] = []
    source_language = "en"
    version_id = ""
    blocks_by_lang: dict[str, list[dict[str, Any]]] = {}
    glossaries: dict[str, dict[str, str]] = {}
    for entry in dag:
        if not isinstance(entry, dict):
            continue
        language = str(entry.get("target_language", ""))
        if not language:
            continue
        source_language = str(entry.get("source_language", source_language))
        version_id = str(entry.get("version_id", version_id))
        if entry.get("handler") == "translate_glossary":
            if language not in languages:
                languages.append(language)
            glossaries[language] = {str(k): str(v) for k, v in dict(entry.get("terms", {})).items()}
        elif entry.get("handler") == "translate_block":
            if language not in languages:
                languages.append(language)
            blocks_by_lang.setdefault(language, []).append(
                {
                    "plan_node_id": entry.get("plan_node_id"),
                    "block_type": entry.get("block_type"),
                    "content": entry.get("content", {}),
                    "answer_key": entry.get("answer_key", {}),
                }
            )
    if not version_id:
        return
    for language in languages:
        blocks = blocks_by_lang.get(language, [])
        if not blocks:
            continue
        glossary = glossaries.get(language, {})
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
                    course_id=course_id,
                )
            )
        await _store_translation(
            session,
            version_id=UUID(version_id),
            source_language=source_language,
            language=language,
            term_table=term_table,
            judged=judged,
            run_id=run_id,
        )
    await session.commit()


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
            return {k: v for k, v in table.items() if v.strip()}
    return {k: v for k, v in glossary.items() if v.strip()}


async def _judge_block(
    session: AsyncSession | None,
    *,
    block: dict[str, Any],
    output: dict[str, Any] | None,
    source_language: str,
    target_language: str,
    term_table: dict[str, str],
    course_id: str = "",
) -> dict[str, Any]:
    block_type = str(block["block_type"])
    content = dict(block["content"])
    answer_key = dict(block.get("answer_key", {}))
    if output is None:
        return _verdict(block, "held", "translator step produced no output")
    if output.get("error"):
        return _verdict(block, "held", f"translator reply rejected: {output['error']}")
    translations = output.get("translations")
    if not isinstance(translations, dict):
        return _verdict(block, "held", "translator step produced no segments")
    translations = {str(k): str(v) for k, v in translations.items()}
    problems: list[str] = []
    try:
        rebuilt = segments.reassemble(block_type, content, translations)
    except Exception as exc:
        return _verdict(block, "held", f"re-assembly failed: {exc}")
    problems += _validate_against_schema(block_type, rebuilt)
    problems += checks.verify_block_translation(block_type, content, rebuilt)
    source_segments = segments.extract_segments(block_type, content)
    for seg in source_segments:
        problems += [
            f"segment {seg['id']}: {problem}"
            for problem in checks.check_segment_pair(
                seg["text"], str(translations.get(seg["id"], ""))
            )
        ]
    for seg in segments.key_segments(block_type, answer_key):
        problems += [
            f"segment {seg['id']}: {problem}"
            for problem in checks.check_segment_pair(
                seg["text"], str(translations.get(seg["id"], ""))
            )
        ]
    kept = {k.casefold() for k, v in term_table.items() if k.casefold() == v.casefold()}
    key_segments = segments.key_segments(block_type, answer_key)
    problems += checks.untranslated_segments(source_segments + key_segments, translations, kept)
    problems += checks.check_glossary_use(source_segments, translations, term_table)
    # Language of the reply itself: echoed source text is not a translation.
    if target_language != source_language:
        from app.services.library import detect_language

        joined = " ".join(str(translations.get(seg["id"], "")) for seg in source_segments).strip()
        if joined:
            detected = detect_language(joined)
            if detected != target_language:
                problems.append(
                    f"reply not in target language: detected {detected}, wanted {target_language}"
                )
    if block_type == "spot_the_error":
        spot_source = {
            **content,
            "planted_index": answer_key.get("planted_index"),
            "kind": answer_key.get("kind"),
        }
        spot_rebuilt = {
            **rebuilt,
            "planted_index": answer_key.get("planted_index"),
            "kind": answer_key.get("kind"),
        }
        spot_key = {
            **answer_key,
            "translated_true_sentence": translations.get("key:true_sentence", ""),
            "glossary": dict(term_table),
        }
        problems += checks.verify_spot_error(spot_source, spot_rebuilt, spot_key)
    translated_key = build_translated_answer_key(
        block_type, content, answer_key, translations, rebuilt
    )
    variant = int(output.get("variant", 0) or 0)
    if problems:
        return _verdict(block, "held", "; ".join(problems), rebuilt, translated_key, variant)
    # Only ok blocks enter translation memory — never before the checks,
    # never the planted sentence, never key segments.
    if session is not None:
        planted_id = segments.planted_segment_id(content, answer_key)
        for seg in source_segments:
            if seg["id"] == planted_id:
                continue
            await memory.tm_store(
                session,
                source_text=seg["text"],
                translated_text=str(translations.get(seg["id"], "")),
                source_language=source_language,
                target_language=target_language,
                course_id=course_id,
            )
    return _verdict(block, "ok", "", rebuilt, translated_key, variant)


def _validate_against_schema(block_type: str, content: dict[str, Any]) -> list[str]:
    from importlib import import_module

    try:
        schema_mod = import_module(f"app.blocks.{block_type}.schema")
    except ImportError:
        return [f"unknown block type: {block_type}"]
    return [f"schema: {error}" for error in schema_mod.validate(content)]


def _verdict(
    block: dict[str, Any],
    status: str,
    reason: str,
    content: dict[str, Any] | None = None,
    translated_answer_key: dict[str, Any] | None = None,
    variant: int = 0,
) -> dict[str, Any]:
    return {
        "plan_node_id": str(block["plan_node_id"]),
        "block_type": str(block["block_type"]),
        "status": status,
        "reason": reason,
        "content": content if content is not None else dict(block["content"]),
        "translated_answer_key": translated_answer_key or {},
        "source_digest": block_digest(dict(block["content"]), dict(block.get("answer_key", {}))),
        "variant": variant,
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
                " (id, translation_id, plan_node_id, block_type, status, reason, content,"
                " translated_answer_key, source_digest, variant)"
                " VALUES (gen_random_uuid(), :translation, :node, :type, :status,"
                " :reason, CAST(:content AS jsonb), CAST(:tkey AS jsonb), :digest, :variant)"
                " ON CONFLICT (translation_id, plan_node_id) DO UPDATE SET"
                " block_type = EXCLUDED.block_type, status = EXCLUDED.status,"
                " reason = EXCLUDED.reason, content = EXCLUDED.content,"
                " translated_answer_key = EXCLUDED.translated_answer_key,"
                " source_digest = EXCLUDED.source_digest, variant = EXCLUDED.variant"
            ),
            {
                "translation": translation_id,
                "node": item["plan_node_id"],
                "type": item["block_type"],
                "status": item["status"],
                "reason": item["reason"] or None,
                "content": json.dumps(item["content"], ensure_ascii=False),
                "tkey": json.dumps(item.get("translated_answer_key", {}), ensure_ascii=False),
                "digest": item["source_digest"],
                "variant": item["variant"],
            },
        )
