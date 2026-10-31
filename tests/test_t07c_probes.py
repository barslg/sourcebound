"""T07c probes (unit): every review finding is red here before its fix.

HIGH 1 source pair first (live b4 pair), HIGH 2 key segments echoed or
partially echoed (M12, P1b), HIGH 3 language table ru/uk/nb/da/sv/en,
MEDIUM 7 entity swap by token diff (P11, P11b), 8 fully-correct spot
(P10), 9 numbers (P9), 10 error message detail (P14), plus the LOW rows
that are pure functions. Callers of every helper: these tests; the
production callers are named in the fix commits.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from typing import Any

import pytest
from conftest import requires_private_prompts

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _live_pair() -> tuple[str, str]:
    """The real saboteur pick of the live T05 run: (true sentence, mutated)."""
    ledger = json.loads((FIXTURES / "t05_live_ledger.json").read_text(encoding="utf-8"))
    row = next(r for r in ledger if r["agent"] == "saboteur")
    task = json.loads(
        row["request"]["input"].split("## Task input (typed, assembled by code)\n")[1]
    )
    return str(task["sentence"]), str(row["response"]["mutated"])


NB_TRUE = (
    "Leverandører og idriftsettere av KI-systemer skal treffe tiltak for å sikre at "
    "ansatte har tilstrekkelig KI-kompetanse, og som tar hensyn til kunnskap og erfaring."
)
NB_PLANTED = NB_TRUE.replace("skal treffe", "skal ikke treffe")
EN_FILLER = "This is another sentence that is part of the exercise and it is correct."
NB_FILLER = "Dette er en annen setning av øvelsen som er med og den er riktig."


def _b4_block(*, true_sentence: str | None = None) -> dict[str, Any]:
    true_en, planted_en = _live_pair()
    return {
        "plan_node_id": "b4",
        "block_type": "spot_the_error",
        "content": {"sentences": [EN_FILLER, planted_en]},
        "answer_key": {
            "planted_sentence": planted_en,
            "planted_index": 1,
            "kind": "negation",
            "passage": "P1",
            "true_sentence": true_sentence if true_sentence is not None else true_en,
            "fully_correct": False,
        },
    }


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


# --- HIGH 3: language table ------------------------------------------------

LANGUAGE_SAMPLES = [
    ("Employers must ensure that staff are trained and that the work is safe for everyone.", "en"),
    ("Arbeidsgivere må sørge for at ansatte får opplæring og veiledning.", "nb"),
    (
        "Leverandørene skal sikre at alle ansatte får opplæring og at arbeidet "
        "blir gjort etter loven.",
        "nb",
    ),
    (
        "Arbejdsgivere skal sikre, at medarbejdere ikke bliver udsat for fare efter loven "
        "og af hensyn til sikkerheden.",
        "da",
    ),
    (
        "Arbetsgivare ska se till att de anställda inte utsätts för fara och att arbetet "
        "är säkert för alla.",
        "sv",
    ),
    ("Роботодавці повинні навчати працівників та забезпечувати безпеку на робочому місці.", "uk"),
    ("Работодатели должны обучать сотрудников и обеспечивать безопасность на рабочем месте.", "ru"),
    ("Работодатель должен на рабочем месте при этом не делать это для сотрудников.", "ru"),
]


@pytest.mark.parametrize(("text", "expected"), LANGUAGE_SAMPLES)
def test_detect_language_table(text: str, expected: str) -> None:
    from app.services.library import detect_language

    assert detect_language(text) == expected


def test_russian_without_marker_letters_is_never_ukrainian() -> None:
    from app.services.library import detect_language

    assert (
        detect_language("Работодатель обязан обучать сотрудников и обеспечивать порядок.") != "uk"
    )
    assert detect_language(" the og ") == "unknown"


def test_russian_reply_for_a_uk_target_is_held() -> None:
    block = {
        "plan_node_id": "b1",
        "block_type": "reading",
        "content": {
            "body": "Providers and deployers must ensure staff training and safety at work.",
            "citations": [{"passage_id": "P1", "quote": "q"}],
        },
        "answer_key": {},
    }
    from app.translation.pipeline import _judge_block

    verdict = asyncio.run(
        _judge_block(
            None,  # type: ignore[arg-type]
            block=block,
            output={
                "translations": {
                    "body": "Поставщики и пользователи должны обеспечивать обучение и безопасность "
                    "сотрудников на рабочем месте."
                }
            },
            source_language="en",
            target_language="uk",
            term_table={},
        )
    )
    assert verdict["status"] == "held", verdict
    assert "reply not in target language" in verdict["reason"]


# --- HIGH 1: planted-error check runs the SOURCE pair first -----------------


async def test_live_b4_pair_ok_when_both_pairs_show_the_kind() -> None:
    verdict = await _judge(
        _b4_block(),
        {"sentences[0]": NB_FILLER, "sentences[1]": NB_PLANTED, "key:true_sentence": NB_TRUE},
    )
    assert verdict["status"] == "ok", verdict


async def test_live_b4_planted_replaced_by_translated_true_claim_is_held() -> None:
    """The vacuity probe: the translated planted sentence IS the true claim."""
    verdict = await _judge(
        _b4_block(),
        {"sentences[0]": NB_FILLER, "sentences[1]": NB_TRUE, "key:true_sentence": NB_TRUE},
    )
    assert verdict["status"] == "held", verdict
    assert "negation" in verdict["reason"], verdict


async def test_unrelated_source_true_sentence_holds_even_if_translation_looks_right() -> None:
    verdict = await _judge(
        _b4_block(true_sentence="Fines are set by the national authority in each member state."),
        {
            "sentences[0]": NB_FILLER,
            "sentences[1]": NB_PLANTED,
            "key:true_sentence": "Bøter fastsettes av den nasjonale myndigheten i hver stat.",
        },
    )
    assert verdict["status"] == "held", verdict
    assert "source" in verdict["reason"], verdict


async def test_related_source_pair_without_the_kind_difference_is_held() -> None:
    """The source pair shares its words but shows no negation: the kind was never planted."""
    block = {
        "plan_node_id": "b4",
        "block_type": "spot_the_error",
        "content": {
            "sentences": [EN_FILLER, "Deployers must train their staff at work today."],
        },
        "answer_key": {
            "planted_sentence": "Deployers must train their staff at work today.",
            "planted_index": 1,
            "kind": "negation",
            "passage": "P1",
            "true_sentence": "Deployers must train their staff at work now.",
            "fully_correct": False,
        },
    }
    verdict = await _judge(
        block,
        {
            "sentences[0]": NB_FILLER,
            "sentences[1]": "Anvendere skal ikke lære opp de ansatte på jobb i dag og er med.",
            "key:true_sentence": "Anvendere skal lære opp de ansatte på jobb i dag og er med.",
        },
    )
    assert verdict["status"] == "held", verdict
    assert "source planted/true pair does not show a negation" in verdict["reason"], verdict


# --- HIGH 2: key segments are language-checked (M12) ------------------------

CLOZE = {
    "plan_node_id": "b5",
    "block_type": "cloze",
    "content": {
        "text": "___ must ensure ___ among staff and workers in the office today.",
        "blanks": ["___", "___"],
    },
    "answer_key": {"blanks": ["Deployers", "training"]},
}
CLOZE_NB = {"text": "___ må sikre ___ blant ansatte og arbeidere på kontoret i dag."}


async def test_m12_echoed_cloze_blank_is_held() -> None:
    verdict = await _judge(
        CLOZE, {**CLOZE_NB, "key:blanks[0]": "Deployers", "key:blanks[1]": "opplæring"}
    )
    assert verdict["status"] == "held", verdict
    assert "key:blanks[0]" in verdict["reason"] and "not translated" in verdict["reason"]


async def test_m12_translated_cloze_blanks_are_ok() -> None:
    verdict = await _judge(
        CLOZE, {**CLOZE_NB, "key:blanks[0]": "Anvendere", "key:blanks[1]": "opplæring"}
    )
    assert verdict["status"] == "ok", verdict
    assert verdict["translated_answer_key"]["blanks"] == ["Anvendere", "opplæring"]


async def test_p1b_partial_echo_one_english_option_is_held() -> None:
    block = {
        "plan_node_id": "b2",
        "block_type": "choice",
        "content": {
            "stem": "Who must ensure staff training at work and in the office?",
            "options": ["Providers and deployers who are here", "Nobody who is here"],
        },
        "answer_key": {"correct": 0},
    }
    verdict = await _judge(
        block,
        {
            "stem": "Hvem må sørge for opplæring av ansatte på jobb og på kontoret?",
            "options[0]": "Leverandører og anvendere som er her",
            "options[1]": "Nobody who is here",
        },
    )
    assert verdict["status"] == "held", verdict
    assert "options[1]" in verdict["reason"] and "not translated" in verdict["reason"]


async def test_glossary_kept_term_may_equal_its_source() -> None:
    block = {
        "plan_node_id": "b6",
        "block_type": "cloze",
        "content": {
            "text": "The ___ governs the use of data at work in the office today.",
            "blanks": ["___"],
        },
        "answer_key": {"blanks": ["GDPR"]},
    }
    text = {"text": "___ regulerer bruken av data på jobb og på kontoret i dag."}
    held = await _judge(block, {**text, "key:blanks[0]": "GDPR"})
    kept = await _judge(block, {**text, "key:blanks[0]": "GDPR"}, glossary={"GDPR": "GDPR"})
    assert held["status"] == "held" and "not translated" in held["reason"], held
    assert kept["status"] == "ok", kept


# --- MEDIUM 8: fully-correct spot (P10) --------------------------------------


async def test_p10_fully_correct_spot_ok_on_sentence_parity() -> None:
    block = {
        "plan_node_id": "b7",
        "block_type": "spot_the_error",
        "content": {"sentences": [EN_FILLER, "Another correct sentence is part of it today."]},
        "answer_key": {
            "planted_sentence": "",
            "planted_index": -1,
            "kind": "negation",
            "passage": "P1",
            "true_sentence": "",
            "fully_correct": True,
        },
    }
    good = {
        "sentences[0]": NB_FILLER,
        "sentences[1]": "En annen riktig setning som er med i den i dag og som er fin.",
    }
    verdict = await _judge(block, good)
    assert verdict["status"] == "ok", verdict
    assert verdict["translated_answer_key"]["fully_correct"] is True


# --- MEDIUM 7: entity swap by token diff (P11, P11b) ----------------------------


def _entity(planted: str, true: str, tr_planted: str, tr_true: str, glossary: Any = None) -> Any:
    from app.translation.checks import verify_spot_error

    source = {"sentences": [planted, "Other."], "planted_index": 0, "kind": "entity_swap"}
    translated = {"sentences": [tr_planted, "Annet."], "planted_index": 0, "kind": "entity_swap"}
    key = {
        "planted_sentence": planted,
        "true_sentence": true,
        "translated_true_sentence": tr_true,
        "glossary": glossary or {},
    }
    return verify_spot_error(source, translated, key)


def test_p11_lowercase_entities_no_glossary_ok() -> None:
    assert (
        _entity(
            "the mayor approves the budget.",
            "the council approves the budget.",
            "ordføreren godkjenner budsjettet.",
            "rådet godkjenner budsjettet.",
        )
        == []
    )


def test_p11b_glossary_that_does_not_cover_the_entity_is_not_a_hold() -> None:
    assert (
        _entity(
            "the mayor approves the budget.",
            "the council approves the budget.",
            "ordføreren godkjenner budsjettet.",
            "rådet godkjenner budsjettet.",
            {"deployer": "anvender"},
        )
        == []
    )


def test_p11_translation_without_the_swap_is_held() -> None:
    assert (
        _entity(
            "the mayor approves the budget.",
            "the council approves the budget.",
            "rådet godkjenner budsjettet.",
            "rådet godkjenner budsjettet.",
        )
        != []
    )


def test_p11_source_pair_without_a_swap_is_held() -> None:
    assert (
        _entity(
            "the mayor approves the budget.",
            "the mayor approves the budget.",
            "ordføreren godkjenner budsjettet.",
            "rådet godkjenner budsjettet.",
        )
        != []
    )


# --- MEDIUM 9: numbers (P9) -----------------------------------------------------


def test_p9_thousands_separators_are_one_number() -> None:
    from app.translation.checks import check_segment_pair

    assert check_segment_pair("The fine is 35,000,000 euro.", "Bøten er 35 000 000 euro.") == []
    assert check_segment_pair("The fine is 35,000,000 euro.", "Bøten er 35.000.000 euro.") == []
    assert check_segment_pair("The fine is 35,000,000 euro.", "Bøten er 36 000 000 euro.") != []
    assert check_segment_pair("Pay 1.5 of 35,000,000.", "Betal 35 000 000 av 1,5.") == []


def test_p9_magnitude_word_is_a_hold_with_its_own_reason() -> None:
    from app.translation.checks import check_segment_pair

    errors = check_segment_pair("The fine is 15,000,000 euro.", "Bøten er 15 millioner euro.")
    assert errors and "magnitude" in errors[0], errors
    assert "numbers changed" not in errors[0], errors


# --- MEDIUM 10: error detail kept (P14) -----------------------------------------


def test_p14_accept_language_keeps_the_specific_message() -> None:
    from app.errors import ValidationError, _render

    class Req:
        headers = {"accept-language": "nb"}
        query_params: dict[str, str] = {}

    resp = asyncio.run(
        _render(Req(), ValidationError("payload.message must be a non-empty string"))
    )  # type: ignore[arg-type]
    body = json.loads(bytes(resp.body))  # type: ignore[attr-defined]
    assert body["message"] == "payload.message must be a non-empty string"
    assert body["localized_message"] == "Forespørselen er ugyldig."
    assert body["error"] == "bad_request"


# --- LOW: text translated once, notice names, prompt ------------------------------


def test_reading_text_equal_to_body_is_one_segment_and_copied() -> None:
    from app.translation.segments import extract_segments, reassemble

    content = {"body": "Same text.", "text": "Same text.", "citations": []}
    ids = [s["id"] for s in extract_segments("reading", content)]
    assert ids == ["body"], ids
    rebuilt = reassemble("reading", content, {"body": "Samme tekst."})
    assert rebuilt["body"] == rebuilt["text"] == "Samme tekst."
    other = {"body": "A.", "text": "B differs.", "citations": []}
    assert [s["id"] for s in extract_segments("reading", other)] == ["body", "text"]


def test_fallback_notice_names_languages_not_codes() -> None:
    from app.i18n.messages import fallback_notice

    notice = fallback_notice(target_language="nb", source_language="en")
    assert notice is not None
    assert "engelsk" in notice and "norsk" in notice, notice
    assert " nb" not in notice and " en." not in notice, notice
    assert fallback_notice(target_language="nb", source_language="nb") is None


@requires_private_prompts
def test_translator_prompt_translates_words_around_numbers() -> None:
    from app.agents.base import system_prompt

    text = system_prompt("translator")
    assert "Article" in text and "Section" in text and "translate" in text.lower()
