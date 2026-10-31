"""T07 full multilinguality: segment pipeline, glossary-first, invariant, TM.

Spec (falsifiable):
1. Segments extracted per block type with stable ids; structure, markers,
   numbers, citations, and answer keys never leave code (translator input
   holds plain strings only).
2. Translator agent (Spark low, prompts/translator/system.md) translates
   plain segments per block, so the reply store skips unchanged blocks.
3. Glossary-first pass builds the target-language term table; the
   translator prompt carries it and translated segments use its terms.
4. Code re-assembles the block and validates it against the block schema;
   per-segment checks: placeholders kept, list/option counts equal,
   numbers unchanged.
5. spot_the_error post-check: exactly one planted sentence of the same
   kind remains (code re-derives it); otherwise the block is held with a
   reason. Planted rows never enter translation memory.
6. course_translations rows per (version, language) with per-block
   ok/held/failed; a learner picks the language; untranslated blocks fall
   back to the source language with a visible notice.
7. Backend i18n: error messages en/nb/uk, one citation-address formatter
   ("p. 14, para 3" / "12:04-12:31" / "Figure 2"), sign-in privacy line in
   three languages, equal key sets.
8. Passage language detection by code heuristics (script + stopword lists
   for en/nb/uk) with a stored override; FTS config follows it.
9. Second identical translation run pays zero (no new paid llm_calls,
   no new HTTP).
"""

from __future__ import annotations

from typing import Any


def _all_block_contents() -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    from importlib import import_module

    from app.blocks.registry import BLOCK_TYPES

    out: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for block_type in BLOCK_TYPES:
        fixture_mod = import_module(f"app.blocks.{block_type}.fixture")
        out[block_type] = fixture_mod.fixture()
    return out


def test_segments_cover_every_block_type_with_stable_ids() -> None:
    from app.translation.segments import extract_segments, reassemble

    for block_type, (content, _key) in _all_block_contents().items():
        first = extract_segments(block_type, content)
        second = extract_segments(block_type, content)
        assert first, f"{block_type} extracts no segments"
        assert [seg["id"] for seg in first] == [seg["id"] for seg in second]
        assert all(seg["text"].strip() for seg in first)
        echo = {seg["id"]: seg["text"] for seg in first}
        assert reassemble(block_type, content, echo) == content


def test_structure_markers_numbers_citations_keys_never_leave_code() -> None:
    from app.agents.translator import build_translator_prompt
    from app.translation.segments import extract_segments

    for block_type, (content, _answer_key) in _all_block_contents().items():
        segments = extract_segments(block_type, content)
        prompt = build_translator_prompt(
            segments=segments,
            source_language="en",
            target_language="nb",
            glossary={"deployer": "anvender"},
            course_id="course-1",
        )
        for sentinel in ("SENTINEL", "passage_id", "planted_index", "P1", "35"):
            assert sentinel not in prompt, f"{block_type} leaks {sentinel}"


def test_translator_agent_prompt_schema_and_refusals() -> None:
    from app.agents.base import AGENT_SCHEMAS, system_prompt
    from app.agents.translator import check_translator_output
    from app.course.tree import TRANSLATOR_SCHEMA
    from app.llm.registry import STEPS

    assert STEPS["translator"].model == "muse-spark-1.3"
    assert STEPS["translator"].effort == "low"
    assert AGENT_SCHEMAS["translator"] == TRANSLATOR_SCHEMA
    system = system_prompt("translator")
    for required in ("Role:", "Rules:", "Output contract:", "Refusal cases:", "Language rule:"):
        assert required in system
    good = {"segments": [{"id": "stem", "text": "Hei"}]}
    assert check_translator_output(good, expected_ids={"stem"}) == []
    assert check_translator_output(good, expected_ids={"stem", "extra"}) != []
    assert check_translator_output({"segments": []}, expected_ids={"stem"}) != []
    assert check_translator_output({"nope": 1}, expected_ids={"stem"}) != []


def test_per_segment_checks_catch_placeholders_counts_numbers() -> None:
    from app.translation.checks import check_segment_pair, verify_block_translation

    assert check_segment_pair("Fill ___ today.", "Fyll ___ i dag.") == []
    assert check_segment_pair("Fill ___ and ___.", "Fyll ___ i dag.") != []
    assert check_segment_pair("Fine 35 million.", "Bot 35 millioner.") == []
    assert check_segment_pair("Fine 35 million.", "Bot 36 millioner.") != []
    assert check_segment_pair("", "Noe.") != []
    source = {"stem": "Who?", "options": ["A 15", "B"]}
    translated = {"stem": "Hvem?", "options": ["A 15", "B"]}
    assert verify_block_translation("choice", source, translated) == []
    assert verify_block_translation("choice", source, {"stem": "Hvem?", "options": ["A 15"]}) != []
    changed = {"stem": "Hvem?", "options": ["A 16", "B"]}
    assert verify_block_translation("choice", source, changed) != []


def test_spot_error_postcheck_holds_fixed_or_reshaped_blocks() -> None:
    from app.course.errorspot import plant_error
    from app.translation.checks import verify_spot_error

    planted = plant_error(
        verified=[
            {"sentence": "Deployers must train staff.", "passage": "P1"},
            {"sentence": "Providers shall keep records.", "passage": "P1"},
        ],
        reader_holds=[],
        seed=3,
        kind="negation",
    )
    kind = str(planted["kind"])
    source = {
        "sentences": list(planted["sentences"]),
        "planted_index": int(planted["planted_index"]),
        "kind": kind,
    }
    translated_true = "Anvendere må lære opp ansatte."
    key = {
        "true_sentence": str(planted["true_sentence"]),
        "translated_true_sentence": translated_true,
    }
    translated_planted = "Anvendere må lære opp ansatte under GDPR, ikke AI-forordningen."
    translated = {
        "sentences": [
            translated_planted if i == int(planted["planted_index"]) else translated_true
            for i in range(len(planted["sentences"]))
        ],
        "planted_index": int(planted["planted_index"]),
        "kind": kind,
    }
    assert verify_spot_error(source, translated, key) == []
    fixed = dict(translated, sentences=[translated_true, translated_true])
    assert verify_spot_error(source, fixed, key) != []
    reshaped = dict(translated, sentences=[translated_planted])
    assert verify_spot_error(source, reshaped, key) != []
    rekinded = dict(translated, kind="scope" if kind != "scope" else "negation")
    assert verify_spot_error(source, rekinded, key) != []


def test_backend_i18n_key_parity_citation_format_privacy_line() -> None:
    from app.i18n import (
        LOCALES,
        error_message,
        format_citation_address,
        signin_privacy_line,
    )

    assert set(LOCALES) == {"en", "nb", "uk"}
    assert error_message("not_found", "nb") != error_message("not_found", "en")
    assert error_message("not_found", "uk") != error_message("not_found", "en")
    assert error_message("no_such_code", "nb") == error_message("no_such_code", "en")
    assert format_citation_address({"kind": "pdf", "page": 14, "para": 3}, "en") == "p. 14, para 3"
    assert "14" in format_citation_address({"kind": "pdf", "page": 14, "para": 3}, "nb")
    assert "14" in format_citation_address({"kind": "pdf", "page": 14, "para": 3}, "uk")
    assert format_citation_address({"kind": "pdf", "page": 14, "para": 3}, "nb") != "p. 14, para 3"
    av = {"kind": "av", "t_start": "12:04", "t_end": "12:31"}
    assert format_citation_address(av, "en") == "12:04–12:31"
    assert format_citation_address(av, "uk") == "12:04–12:31"
    assert format_citation_address({"kind": "figure", "figure": 2}, "en") == "Figure 2"
    assert format_citation_address(
        {"kind": "figure", "figure": 2}, "en"
    ) != format_citation_address({"kind": "figure", "figure": 2}, "nb")
    lines = {locale: signin_privacy_line(locale) for locale in LOCALES}
    assert all(line.strip() for line in lines.values())
    assert len(set(lines.values())) == 3


def test_language_detection_by_script_and_stopwords() -> None:
    from app.services.library import detect_language, ts_config_for_passage

    assert detect_language("Providers and deployers must ensure staff training.") == "en"
    assert detect_language("Arbeidsgivere må sørge for at ansatte får opplæring.") == "nb"
    assert detect_language("unix and the shell") == "en"
    assert detect_language("Роботодавці повинні навчати працівників.") == "uk"
    assert detect_language("Співробітники повинні знати правила для роботи.") == "uk"
    assert ts_config_for_passage("Deployers must train staff.") == "english"
    assert ts_config_for_passage("Ansatte må få opplæring.", None) == "norwegian"
    assert ts_config_for_passage("Працівники проходять навчання.") == "simple"
    assert ts_config_for_passage("Deployers must train staff.", "norwegian") == "norwegian"


async def test_passage_language_override_is_stored(t03_session: Any) -> None:
    import sqlalchemy as sa

    from app.services import library
    from app.services.library import set_passage_language_override

    rows = (await t03_session.execute(sa.text("SELECT id FROM passages LIMIT 1"))).all()
    if not rows:
        from uuid import uuid4

        import demo_t05

        from app.config import Settings
        from app.services import courses

        EXCERPT = demo_t05.EXCERPT
        settings = Settings(
            _env_file=None,
            model_api_key="synthetic-test-key",
            model_api_base_url="https://api.meta.ai/v1",
            llm_spend_ceiling_usd=50.0,
            source_store_path="/tmp/muse-t07-lang",
        )

        user = (
            await t03_session.execute(
                sa.text("INSERT INTO users (email, display_name) VALUES (:e, 'N') RETURNING id"),
                {"e": f"lang-{uuid4().hex[:8]}@example.test"},
            )
        ).one()[0]
        course_id = await courses.create_course(
            t03_session, owner_id=user, title="lang", language="en"
        )
        source_id = await library.store_upload(
            t03_session,
            settings=settings,
            created_by=user,
            filename="x.md",
            media="text",
            content=EXCERPT.encode(),
            licence="test",
            course_id=course_id,
        )
        await library.ingest_passages(
            t03_session, source_id=source_id, passages=library.chunk_markdown(EXCERPT)
        )
        await t03_session.commit()
        rows = (await t03_session.execute(sa.text("SELECT id FROM passages LIMIT 1"))).all()
    passage_id = str(rows[0][0])
    await set_passage_language_override(t03_session, passage_id=passage_id, language="nb")
    await t03_session.commit()
    cfg = (
        await t03_session.execute(
            sa.text("SELECT ts_config FROM passages WHERE id = :pid"), {"pid": passage_id}
        )
    ).scalar()
    assert cfg == "norwegian"
