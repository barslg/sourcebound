"""T07b probes: every review probe becomes a test (red before the fix).

1. English echoed as nb is held ("reply not in target language"), never TM-cached.
2. Planted error re-derived by kind on the translated planted/true pair.
3. Translated exercises grade a correct translated answer like the source.
4. TM only for ok blocks, per-course; held blocks re-ask with variant+1.
5. One bad reply holds one block; the run continues and can resume.
6. No false holds: word-boundary glossary, nb numbers as normalised multisets.
7. Mutants: dropped segment check / schema / glossary, held shown as
   translated, TM lookup always missing — each killed by a test below.
"""

from __future__ import annotations

from typing import Any

# --- unit: language detection -------------------------------------------


def test_detect_language_single_nb_word_in_english_is_english() -> None:
    from app.services.library import detect_language

    assert detect_language("The team in Tromsø must follow the rules and report.") == "en"
    assert detect_language("Providers and deployers must ensure staff training.") == "en"
    assert (
        detect_language("Arbeidsgivere må sørge for at ansatte får opplæring og veiledning.")
        == "nb"
    )
    assert (
        detect_language("Роботодавці повинні навчати працівників та забезпечувати безпеку.") == "uk"
    )


# --- unit: segment checks ------------------------------------------------


def test_numbers_normalised_value_multiset() -> None:
    from app.translation.checks import check_segment_pair

    assert check_segment_pair("Fine is 1.5 of 35000000.", "Bot er 1,5 av 35 000 000.") == []
    assert check_segment_pair("Pay 15 and then 35.", "Betal 35 og så 15.") == []
    assert check_segment_pair("Pay 15 and then 35.", "Betal 36 og så 15.") != []
    assert check_segment_pair("See [P2] for the duty.", "Se [P2] for plikten.") == []
    assert check_segment_pair("See [P2] for the duty.", "Se [P3] for plikten.") != []


def test_glossary_word_boundary_not_substring() -> None:
    from app.translation.checks import check_glossary_use

    segs = [{"id": "body", "text": "Maintain the system at work."}]
    assert check_glossary_use(segs, {"body": "Vedlikehold systemet på jobb."}, {"AI": "KI"}) == []
    segs_ai = [{"id": "body", "text": "AI systems at work."}]
    assert check_glossary_use(segs_ai, {"body": "Systemer på jobb."}, {"AI": "KI"}) != []
    assert check_glossary_use(segs_ai, {"body": "KI-systemer på jobb."}, {"AI": "KI"}) == []


# --- unit: planted re-derivation by kind ---------------------------------


def _spot(kind: str, src_planted: str, src_true: str, tr_planted: str, tr_true: str) -> Any:
    from app.translation.checks import verify_spot_error

    source = {"sentences": [src_planted, "Other."], "planted_index": 0, "kind": kind}
    translated = {"sentences": [tr_planted, "Annet."], "planted_index": 0, "kind": kind}
    key = {
        "planted_sentence": src_planted,
        "true_sentence": src_true,
        "translated_true_sentence": tr_true,
        "glossary": {"deployer": "anvender", "provider": "leverandør"},
    }
    return verify_spot_error(source, translated, key)


def test_planted_negation_rederived() -> None:
    assert (
        _spot(
            "negation",
            "Deployers must not train staff.",
            "Deployers must train staff.",
            "Anvendere må ikke lære opp ansatte.",
            "Anvendere må lære opp ansatte.",
        )
        == []
    )
    # negation translated away -> held
    assert (
        _spot(
            "negation",
            "Deployers must not train staff.",
            "Deployers must train staff.",
            "Anvendere må lære opp ansatte.",
            "Anvendere må lære opp ansatte.",
        )
        != []
    )


def test_planted_scope_entity_cause_number_citation_rederived() -> None:
    assert (
        _spot(
            "scope",
            "No deployers must train staff.",
            "All deployers must train staff.",
            "Ingen anvendere må lære opp ansatte.",
            "Alle anvendere må lære opp ansatte.",
        )
        == []
    )
    assert (
        _spot(
            "scope",
            "No deployers must train staff.",
            "All deployers must train staff.",
            "Alle anvendere må lære opp ansatte.",
            "Alle anvendere må lære opp ansatte.",
        )
        != []
    )
    assert (
        _spot(
            "entity_swap",
            "The provider must train staff.",
            "The deployer must train staff.",
            "Leverandøren må lære opp ansatte.",
            "Anvenderen må lære opp ansatte.",
        )
        == []
    )
    assert (
        _spot(
            "entity_swap",
            "The provider must train staff.",
            "The deployer must train staff.",
            "Anvenderen må lære opp ansatte.",
            "Anvenderen må lære opp ansatte.",
        )
        != []
    )
    assert (
        _spot(
            "cause_reversal",
            "Records are kept because training fails.",
            "Training fails because records are kept.",
            "Journaler føres fordi opplæring svikter.",
            "Opplæring svikter fordi journaler føres.",
        )
        == []
    )
    assert (
        _spot(
            "cause_reversal",
            "Records are kept because training fails.",
            "Training fails because records are kept.",
            "Opplæring svikter fordi journaler føres.",
            "Opplæring svikter fordi journaler føres.",
        )
        != []
    )


def test_planted_number_and_citation() -> None:
    assert (
        _spot(
            "number_date",
            "The fine is 36 million.",
            "The fine is 35 million.",
            "Boten er 36 millioner.",
            "Boten er 35 millioner.",
        )
        == []
    )
    assert (
        _spot(
            "number_date",
            "The fine is 36 million.",
            "The fine is 35 million.",
            "Boten er 35 millioner.",
            "Boten er 35 millioner.",
        )
        != []
    )
    assert (
        _spot(
            "bad_citation",
            "Deployers must train staff [P9].",
            "Deployers must train staff.",
            "Anvendere må lære opp ansatte [P9].",
            "Anvendere må lære opp ansatte.",
        )
        == []
    )


def test_planted_uncheckable_is_held_never_ok() -> None:
    from app.translation.checks import verify_spot_error

    source = {"sentences": ["Alpha.", "Beta."], "planted_index": 0, "kind": "cause_reversal"}
    translated = {"sentences": ["Alfa.", "Beta."], "planted_index": 0, "kind": "cause_reversal"}
    assert verify_spot_error(source, translated, {}) != []


# --- unit: segments cover every text-bearing key --------------------------


def test_segments_cover_reading_text_and_cloze_key_blanks() -> None:
    from app.translation.segments import extract_segments, key_segments

    content = {"body": "Body text.", "text": "Body text.", "citations": []}
    ids = {s["id"] for s in extract_segments("reading", content)}
    assert {"body", "text"} <= ids
    ks = key_segments("cloze", {"blanks": ["Deployers", "training"]})
    assert [k["text"] for k in ks] == ["Deployers", "training"]
    ks2 = key_segments("spot_the_error", {"true_sentence": "True sentence."})
    assert ks2 and ks2[0]["text"] == "True sentence."


# --- unit: translated answer keys + grading parity for all 7 types --------


def _grade(block_type: str, content: Any, key: Any, submission: Any) -> float:
    from importlib import import_module

    return float(
        import_module(f"app.blocks.{block_type}.check").grade(content, key, submission)["score"]
    )


def test_grading_parity_all_types() -> None:
    from app.translation.keys import build_translated_answer_key

    cases: dict[str, Any] = {
        "reading": (
            {
                "body": "Deployers must train staff.",
                "citations": [{"passage_id": "P1", "quote": "q"}],
            },
            {"note": "k"},
            {
                "body": "Anvendere må lære opp ansatte.",
                "citations": [{"passage_id": "P1", "quote": "q"}],
            },
            {"body": "Anvendere må lære opp ansatte."},
            {"acknowledged": True},
        ),
        "figure": (
            {
                "caption": "Fine ceilings.",
                "numbers": [15, 35],
                "citation": {"passage_id": "P1", "quote": "q"},
            },
            {"source_numbers": [15, 35]},
            {
                "caption": "Botterskala.",
                "numbers": [15, 35],
                "citation": {"passage_id": "P1", "quote": "q"},
            },
            {"caption": "Botterskala."},
            {"acknowledged": True},
        ),
        "choice": (
            {"stem": "Who must train?", "options": ["Deployers", "Nobody"]},
            {"correct": 0, "passage": "P1"},
            {"stem": "Hvem må lære opp?", "options": ["Anvendere", "Ingen"]},
            {"stem": "Hvem må lære opp?", "options[0]": "Anvendere", "options[1]": "Ingen"},
            {"picked": 0},
        ),
        "matching": (
            {
                "pairs": [
                    {"left": "Provider", "right": "train staff"},
                    {"left": "Deployer", "right": "oversee use"},
                ]
            },
            {"mapping": {"Provider": "train staff", "Deployer": "oversee use"}, "passage": "P1"},
            {
                "pairs": [
                    {"left": "Leverandør", "right": "lære opp ansatte"},
                    {"left": "Anvender", "right": "følge med på bruk"},
                ]
            },
            {
                "pairs[0].left": "Leverandør",
                "pairs[0].right": "lære opp ansatte",
                "pairs[1].left": "Anvender",
                "pairs[1].right": "følge med på bruk",
            },
            {"mapping": {"Leverandør": "lære opp ansatte", "Anvender": "følge med på bruk"}},
        ),
        "cloze": (
            {"text": "___ must ensure ___ among staff.", "blanks": ["___", "___"]},
            {"blanks": ["Deployers", "training"], "passage": "P1"},
            {"text": "___ må sikre ___ blant ansatte.", "blanks": ["___", "___"]},
            {
                "text": "___ må sikre ___ blant ansatte.",
                "key:blanks[0]": "Anvendere",
                "key:blanks[1]": "opplæring",
            },
            {"blanks": ["Anvendere", "opplæring"]},
        ),
        "scenario_practice": (
            {
                "scenario": "Sort uses.",
                "items": [
                    {"text": "CV screening", "passage_id": "P1"},
                    {"text": "Spell check", "passage_id": "P2"},
                ],
            },
            {
                "labels": {"CV screening": "high-risk", "Spell check": "minimal"},
                "passages": {"CV screening": "P1", "Spell check": "P2"},
                "passage": "P1",
            },
            {
                "scenario": "Sorter bruk.",
                "items": [
                    {"text": "CV-screening", "passage_id": "P1"},
                    {"text": "Stavekontroll", "passage_id": "P2"},
                ],
            },
            {
                "scenario": "Sorter bruk.",
                "items[0].text": "CV-screening",
                "items[1].text": "Stavekontroll",
            },
            {
                "labels": {"CV-screening": "high-risk", "Stavekontroll": "minimal"},
                "passages": {"CV-screening": "P1", "Stavekontroll": "P2"},
            },
        ),
        "spot_the_error": (
            {"sentences": ["Deployers must train staff.", "The GDPR never applies."]},
            {
                "planted_sentence": "The GDPR never applies.",
                "planted_index": 1,
                "kind": "scope",
                "passage": "P1",
                "true_sentence": "The GDPR may apply.",
                "fully_correct": False,
            },
            {"sentences": ["Anvendere må lære opp ansatte.", "GDPR gjelder aldri."]},
            {
                "sentences[0]": "Anvendere må lære opp ansatte.",
                "sentences[1]": "GDPR gjelder aldri.",
                "key:true_sentence": "GDPR kan gjelde.",
            },
            {
                "sentence": "GDPR gjelder aldri.",
                "kind": "scope",
                "passage": "P1",
                "correct_value": "GDPR kan gjelde.",
            },
        ),
    }
    for block_type, (content, key, tr_content, translations, submission) in cases.items():
        tr_key = build_translated_answer_key(block_type, content, key, translations, tr_content)
        en_score = _grade(block_type, content, key, _english_submission(block_type))
        nb_score = _grade(block_type, tr_content, tr_key, submission)
        assert nb_score == en_score == 1.0, (block_type, tr_key, nb_score)


def _english_submission(block_type: str) -> Any:
    return {
        "reading": {"acknowledged": True},
        "figure": {"acknowledged": True},
        "choice": {"picked": 0},
        "matching": {"mapping": {"Provider": "train staff", "Deployer": "oversee use"}},
        "cloze": {"blanks": ["Deployers", "training"]},
        "scenario_practice": {
            "labels": {"CV screening": "high-risk", "Spell check": "minimal"},
            "passages": {"CV screening": "P1", "Spell check": "P2"},
        },
        "spot_the_error": {
            "sentence": "The GDPR never applies.",
            "kind": "scope",
            "passage": "P1",
            "correct_value": "The GDPR may apply.",
        },
    }[block_type]


# --- unit: i18n wiring ----------------------------------------------------


def test_error_render_uses_request_language() -> None:
    import asyncio

    from app.errors import NotFoundError, _render

    class Req:
        headers = {"accept-language": "nb"}
        query_params: dict[str, str] = {}

    resp = (
        asyncio.get_event_loop_policy()
        .new_event_loop()
        .run_until_complete(
            _render(Req(), NotFoundError("x"))  # type: ignore[arg-type]
        )
    )
    import json as _json

    body = _json.loads(bytes(resp.body))  # type: ignore[attr-defined]
    assert body["message"] == "Her ble det ikke funnet noe."


def test_translator_prompt_has_no_course_facts() -> None:
    import pathlib

    text = pathlib.Path("prompts/translator/system.md").read_text(encoding="utf-8")
    for fact in ("AI Act", "GDPR", "municipal"):
        assert fact not in text


# --- DB: judge-block mutants ----------------------------------------------


async def _judge(block: Any, translations: Any, *, glossary: Any = None) -> Any:
    from app.translation.pipeline import _judge_block

    return await _judge_block(
        None,  # type: ignore[arg-type]
        block=block,
        output={"translations": translations},
        source_language="en",
        target_language="nb",
        term_table=glossary or {},
    )


async def test_mutant_segment_check_killed_numbers_change_holds() -> None:
    block = {
        "plan_node_id": "b1",
        "block_type": "choice",
        "content": {"stem": "Pay 15 now?", "options": ["Pay 15", "Pay 35"]},
        "answer_key": {"correct": 0},
    }
    verdict = await _judge(
        block,
        {
            "stem": "Betal 16 nå og dette er riktig?",
            "options[0]": "Betal 16",
            "options[1]": "Betal 35",
        },
    )
    assert verdict["status"] == "held", verdict


async def test_mutant_schema_and_glossary_checks_killed() -> None:
    block = {
        "plan_node_id": "b1",
        "block_type": "choice",
        "content": {"stem": "Who must train staff?", "options": ["Deployers", "Nobody"]},
        "answer_key": {"correct": 0},
    }
    bad_schema = await _judge(
        block,
        {"stem": "Hvem må lære opp ansatte?", "options[0]": "", "options[1]": "Ingen som er her"},
    )
    assert bad_schema["status"] == "held", bad_schema
    bad_gloss = await _judge(
        block,
        {
            "stem": "Hvem må lære opp ansatte i dag?",
            "options[0]": "Arbeidsgivere som er her",
            "options[1]": "Ingen som er her",
        },
        glossary={"deployer": "anvender"},
    )
    # stem has no deployer; options[0] should carry anvender -> held
    assert bad_gloss["status"] == "held", bad_gloss


async def test_mutant_segment_check_only_gap_loss_holds() -> None:
    """Only the per-segment check sees a lost ___ gap (schema still passes)."""
    block = {
        "plan_node_id": "b1",
        "block_type": "cloze",
        "content": {
            "text": "___ must ensure ___ among staff and workers in the office today.",
            "blanks": ["___", "___"],
        },
        "answer_key": {"blanks": ["Deployers", "training"]},
    }
    verdict = await _judge(
        block,
        {
            "text": "___ må sikre opplæring blant ansatte og arbeidere på kontoret i dag.",
            "key:blanks[0]": "Anvendere som er her i dag",
            "key:blanks[1]": "opplæring og trening for alle",
        },
    )
    assert verdict["status"] == "held", verdict
    assert "gap" in verdict["reason"], verdict


async def test_mutant_schema_only_missing_citations_holds() -> None:
    """Only schema validation sees a reading block without citations."""
    block = {
        "plan_node_id": "b1",
        "block_type": "reading",
        "content": {"body": "Providers and deployers must ensure staff training at work."},
        "answer_key": {},
    }
    verdict = await _judge(
        block,
        {"body": "Leverandører og anvendere må sikre opplæring av ansatte på jobb og i arbeid."},
    )
    assert verdict["status"] == "held", verdict
    assert "schema" in verdict["reason"], verdict


async def test_echoed_english_held_with_exact_reason() -> None:
    block = {
        "plan_node_id": "b1",
        "block_type": "reading",
        "content": {
            "body": "Providers and deployers must ensure staff training and safety at work.",
            "citations": [{"passage_id": "P1", "quote": "q"}],
        },
        "answer_key": {},
    }
    verdict = await _judge(
        block,
        {"body": "Providers and deployers must ensure staff training and safety at work."},
    )
    assert verdict["status"] == "held"
    assert "reply not in target language" in verdict["reason"]
