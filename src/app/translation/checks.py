"""Code checks on translated segments and re-assembled blocks.

Every number, placeholder, list length, and the planted-error invariant
is verified here, never trusted from the translator reply. Numbers are
compared by normalised value as a multiset (nb decimal comma 1,5,
thousands spacing 35 000 000, order changed by word order all pass);
glossary terms match on word boundaries only; [P2] citation/passage
markers are protected explicitly, not by the digit check.
"""

from __future__ import annotations

import re
from typing import Any

from app.course.errorspot import MUTATION_KINDS

_GAP = re.compile(r"_{3,}")
_MARKER = re.compile(r"\[P\d+\]|\[[A-Z][A-Za-z]*[ .]\d+\]")
_NUMBER = re.compile(r"\d{1,3}(?:[ \u00a0,.]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")
_MAGNITUDE = re.compile(
    r"\d[\d .,\u00a0]*\s*(?:million\w*|milliard\w*|billion\w*|thousand|tusen|"
    r"мільйон\w*|мільярд\w*|тисяч\w*)",
    re.IGNORECASE,
)

_SOURCE_RELATED = 0.6
_TRANSLATED_RELATED = 0.5

_NEGATIONS = {
    "not",
    "never",
    "no",
    "n't",
    "cannot",
    "can't",
    "ikke",
    "aldri",
    "ingen",  # nb (ingen doubles as scope; counted in both)
    "не",
    "ні",
    "ніколи",
    "немає",
    "жоден",
}
_SCOPE = {
    "all",
    "every",
    "no",
    "none",
    "only",
    "never",
    "alle",
    "hver",
    "hvert",
    "ingen",
    "kun",
    "bare",
    "aldri",
    "всі",
    "усі",
    "кожен",
    "кожна",
    "жоден",
    "жодна",
    "лише",
    "тільки",
    "ніколи",
}
_CAUSAL = {
    "because",
    "therefore",
    "so",
    "causes",
    "caused",
    "fordi",
    "derfor",
    "fører",
    "оскільки",
    "тому",
    "спричиняє",
}


def _norm_number(raw: str) -> float:
    """Value of one number token: thousands separators and decimal commas."""
    text = raw.replace("\u00a0", " ").replace(" ", "")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif text.count(".") > 1:
        text = text.replace(".", "")
    elif text.count(",") > 1:
        text = text.replace(",", "")
    elif "," in text:
        head, _, tail = text.partition(",")
        # 35,000 reads as thousands; 1,5 reads as a decimal comma.
        if len(tail) == 3 and len(head) <= 3 and tail.isdigit():
            text = head + tail
        else:
            text = text.replace(",", ".")
    return float(text)


def number_values(text: str) -> list[float]:
    """Normalised numeric values of one string, as a sortable multiset."""
    return sorted(_norm_number(m) for m in _NUMBER.findall(text))


def markers_of(text: str) -> list[str]:
    """Bracketed markers ([P2], citation flags) of one string, sorted."""
    return sorted(_MARKER.findall(text))


def check_segment_pair(source: str, translated: str) -> list[str]:
    """Per-segment checks: non-empty, placeholders, markers, numbers."""
    errors: list[str] = []
    if not source.strip():
        errors.append("source segment is empty")
        return errors
    if not translated.strip():
        errors.append("translated segment is empty")
        return errors
    gaps_in, gaps_out = len(_GAP.findall(source)), len(_GAP.findall(translated))
    if gaps_in != gaps_out:
        errors.append(f"gap markers changed: {gaps_in} vs {gaps_out}")
    if markers_of(source) != markers_of(translated):
        errors.append(f"markers changed: {markers_of(source)} vs {markers_of(translated)}")
    if number_values(source) != number_values(translated):
        if _MAGNITUDE.search(source) or _MAGNITUDE.search(translated):
            errors.append(
                "number written with a magnitude word cannot be compared with its digits;"
                " keep the digits exactly as in the source"
            )
        else:
            errors.append(
                f"numbers changed: {number_values(source)} vs {number_values(translated)}"
            )
    return errors


def untranslated_segments(
    segments: list[dict[str, str]],
    translations: dict[str, str],
    kept_terms: set[str],
) -> list[str]:
    """Segments (content AND key) the model echoed instead of translating.

    A translated segment equal to its source and holding letters is not
    a translation unless the whole segment is a glossary-kept term.
    A partial echo (one English option among translated ones) fails
    the same way, because every segment is judged on its own.
    """
    errors: list[str] = []
    for segment in segments:
        source = " ".join(segment["text"].split())
        translated = " ".join(str(translations.get(segment["id"], "")).split())
        if not re.search(r"[^\W\d_]", source):
            continue
        if source.casefold() == translated.casefold() and source.casefold() not in kept_terms:
            errors.append(f"segment {segment['id']}: not translated (equals its source)")
    return errors


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _numbers_in(value: Any) -> list[float]:
    if isinstance(value, str):
        return number_values(value)
    if isinstance(value, dict):
        found: list[float] = []
        for item in value.values():
            found += _numbers_in(item)
        return found
    if isinstance(value, list):
        found = []
        for item in value:
            found += _numbers_in(item)
        return found
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    return []


def verify_block_translation(
    block_type: str, source: dict[str, Any], translated: dict[str, Any]
) -> list[str]:
    """Structure parity between source and re-assembled translated content."""
    errors: list[str] = []
    if sorted(_numbers_in(source)) != sorted(_numbers_in(translated)):
        errors.append("numbers changed in translation")
    if block_type == "choice":
        if len(_as_list(translated.get("options"))) != len(_as_list(source.get("options"))):
            errors.append("option count changed in translation")
    elif block_type == "matching":
        if len(_as_list(translated.get("pairs"))) != len(_as_list(source.get("pairs"))):
            errors.append("pair count changed in translation")
    elif block_type in ("scenario_practice", "ordering"):
        if len(_as_list(translated.get("items"))) != len(_as_list(source.get("items"))):
            errors.append("item count changed in translation")
    elif block_type == "spot_the_error":
        if len(_as_list(translated.get("sentences"))) != len(_as_list(source.get("sentences"))):
            errors.append("sentence count changed in translation")
    elif block_type == "diagram":
        if len(_as_list(translated.get("nodes"))) != len(_as_list(source.get("nodes"))) or len(
            _as_list(translated.get("edges"))
        ) != len(_as_list(source.get("edges"))):
            errors.append("node or edge count changed in translation")
    for key in ("body", "stem", "text", "scenario", "prompt", "caption"):
        if key in source and not str(translated.get(key, "")).strip():
            errors.append(f"{key} translated to empty")
    return errors


def _contains_term(text: str, term: str, *, stem: bool = False) -> bool:
    """Word-boundary term match; inflected suffixes still count.

    `stem` (target-language terms) also accepts a changed ending of a
    long term: Leverandører / leverandørene are the same glossary term.
    """
    if not term.strip():
        return False
    core = term.lower()
    if stem and len(core) >= 7:
        core = core[:-2]
    pattern = r"(?<!\w)" + re.escape(core) + r"\w*"
    return re.search(pattern, text.lower()) is not None


def check_glossary_use(
    segments: list[dict[str, str]],
    translated: dict[str, str],
    glossary: dict[str, str],
) -> list[str]:
    """Every glossary source term present (word-boundary) must surface."""
    errors: list[str] = []
    for segment in segments:
        source_text = segment["text"]
        target_text = translated.get(segment["id"], "")
        for source_term, target_term in glossary.items():
            if not _contains_term(source_text, source_term):
                continue
            if not _contains_term(target_text, target_term, stem=True):
                errors.append(
                    f"segment {segment['id']}: glossary term {target_term!r} missing in translation"
                )
    return errors


def _tokens(text: str) -> list[str]:
    return re.findall(r"[^\W\d_]+(?:'[a-z]+)?", text.lower())


def _count_in(text: str, vocab: set[str]) -> int:
    return sum(1 for tok in _tokens(text) if tok in vocab)


def _entity_diff(
    planted: str,
    partner: str,
    glossary: dict[Any, Any],
    source_pair: tuple[str, str] | None = None,
) -> bool:
    """Both sentences differ by tokens the other lacks; glossary targets must follow.

    The swapped entity is the token diff of the pair. When the glossary
    covers a token of the SOURCE diff, its target must stand in the
    matching translated sentence; a glossary that does not cover the
    entity decides nothing.
    """
    p_tokens, q_tokens = set(_tokens(planted)), set(_tokens(partner))
    only_p, only_q = p_tokens - q_tokens, q_tokens - p_tokens
    if not only_p or not only_q:
        return False
    if len(only_p) + len(only_q) > max(len(p_tokens | q_tokens) // 2, 2):
        return False
    if source_pair is not None:
        s_planted, s_true = set(_tokens(source_pair[0])), set(_tokens(source_pair[1]))
        covered = {str(term).lower(): str(target) for term, target in glossary.items()}
        for token in s_planted - s_true:
            if token in covered and not _contains_term(planted, covered[token], stem=True):
                return False
        for token in s_true - s_planted:
            if token in covered and not _contains_term(partner, covered[token], stem=True):
                return False
    return True


def _related(a: str, b: str, minimum: float) -> bool:
    """Token overlap of two sentences: a mutation shares most of its words."""
    left, right = set(_tokens(a)), set(_tokens(b))
    if not left or not right:
        return False
    return len(left & right) / len(left | right) >= minimum


def kind_difference(
    kind: str,
    planted: str,
    partner: str,
    glossary: dict[Any, Any] | None = None,
    source_pair: tuple[str, str] | None = None,
) -> bool | None:
    """True when planted/partner differ the way `kind` says, False when not.

    None when the kind cannot be decided on this pair. Language-neutral
    vocabularies cover en/nb/uk; used on the SOURCE pair and again on
    the translated pair. A negation shows when the pair differs by an odd
    number of negations; the translated pair must keep the source's
    difference, so a double negation (which cancels) is not an error.
    """
    glossary = glossary or {}
    if kind == "negation":
        delta = _count_in(planted, _NEGATIONS) - _count_in(partner, _NEGATIONS)
        if source_pair is None:
            return delta % 2 == 1
        return delta == _count_in(source_pair[0], _NEGATIONS) - _count_in(
            source_pair[1], _NEGATIONS
        )
    if kind == "scope":
        p = sorted(t for t in _tokens(planted) if t in _SCOPE)
        q = sorted(t for t in _tokens(partner) if t in _SCOPE)
        return p != q and bool(p or q)
    if kind == "number_date":
        return number_values(planted) != number_values(partner)
    if kind == "bad_citation":
        return bool(markers_of(planted)) and markers_of(planted) != markers_of(partner)
    if kind == "entity_swap":
        return _entity_diff(planted, partner, glossary, source_pair)
    if kind == "cause_reversal":

        def clauses(text: str) -> tuple[set[str], set[str]] | None:
            toks = _tokens(text)
            idx = next((i for i, t in enumerate(toks) if t in _CAUSAL), None)
            if idx is None:
                return None
            return (set(toks[:idx]), set(toks[idx + 1 :]))

        a, b = clauses(planted), clauses(partner)
        if a is None or b is None:
            return None

        def content(words: set[str]) -> set[str]:
            return {tok for tok in words if tok not in _NEGATIONS and len(tok) > 2}

        (pl, pr), (ql, qr) = a, b
        return (
            bool(content(pl) & content(qr))
            and bool(content(pr) & content(ql))
            and not (content(pl) == content(ql) and content(pr) == content(qr))
        )
    return None


def verify_spot_error(
    source: dict[str, Any], translated: dict[str, Any], answer_key: dict[str, Any]
) -> list[str]:
    """Exactly one planted sentence of the same kind must remain.

    The kind check runs on the SOURCE planted/true pair first: a source
    pair that does not show the kind is held (the translation cannot
    prove what the source never had). Then the translated planted
    sentence and the translated true sentence must show the same kind
    difference. A fully-correct block has no planted sentence: sentence
    parity only. When a check cannot be made the block is held.
    """
    source_sentences = _as_list(source.get("sentences"))
    translated_sentences = _as_list(translated.get("sentences"))
    if len(translated_sentences) != len(source_sentences) or len(source_sentences) < 2:
        return ["sentence count changed in translation"]
    if answer_key.get("fully_correct"):
        if source.get("planted_index") not in (None, -1):
            return ["a fully-correct block carries a planted index"]
        if len(set(translated_sentences)) != len(set(source_sentences)):
            return ["two sentences became one in translation"]
        return []
    if translated.get("kind") != source.get("kind"):
        return [f"mutation kind changed: {source.get('kind')} vs {translated.get('kind')}"]
    kind = str(source.get("kind", ""))
    if kind not in MUTATION_KINDS:
        return [f"unknown mutation kind: {kind}"]
    planted_index = source.get("planted_index")
    if (
        not isinstance(planted_index, int)
        or not 0 <= planted_index < len(translated_sentences)
        or translated.get("planted_index") != planted_index
    ):
        return ["planted index moved in translation"]
    _raw_glossary: Any = answer_key.get("glossary")
    glossary: dict[Any, Any] = _raw_glossary if isinstance(_raw_glossary, dict) else {}
    source_true = answer_key.get("true_sentence")
    if not isinstance(source_true, str) or not source_true.strip():
        return [f"planted {kind} check cannot be made: the key has no true sentence"]
    source_planted = str(source_sentences[planted_index])
    if not _related(source_planted, source_true, _SOURCE_RELATED):
        return ["source planted/true pair is not one sentence and its mutation"]
    source_verdict = kind_difference(kind, source_planted, source_true)
    if source_verdict is None:
        return [f"planted {kind} check cannot be made on the source pair"]
    if not source_verdict:
        return [f"source planted/true pair does not show a {kind} difference"]
    true_text = answer_key.get("translated_true_sentence")
    if not isinstance(true_text, str) or not true_text.strip():
        return [f"planted {kind} check cannot be made: no true sentence translation"]
    if not _related(str(translated_sentences[planted_index]), true_text, _TRANSLATED_RELATED):
        return [f"translated planted/true pair is not one sentence and its {kind} mutation"]
    translated_verdict = kind_difference(
        kind,
        str(translated_sentences[planted_index]),
        true_text,
        glossary,
        (source_planted, source_true),
    )
    if translated_verdict is None:
        return [f"planted {kind} check cannot be made on the translated pair"]
    if not translated_verdict:
        return [f"planted {kind} error lost in translation"]
    return []
