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

_GAP = "___"
_MARKER = re.compile(r"\[P\d+\]|\[[A-Z][A-Za-z]*[ .]\d+\]")
_NUMBER = re.compile(r"\d{1,3}(?:[ \u00a0]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")

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
    text = raw.replace("\u00a0", " ").replace(" ", "")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        head, _, tail = text.partition(",")
        # 35,000 reads as thousands; 1,5 reads as a decimal comma.
        if len(tail) == 3 and "," not in tail and len(head) <= 3 and tail.isdigit():
            text = head + tail
        else:
            text = text.replace(",", ".")
    return float(text)


def numbers_of(text: str) -> list[str]:
    """Number tokens of one string (legacy string form)."""
    return _NUMBER.findall(text)


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
    if source.count(_GAP) != translated.count(_GAP):
        errors.append(f"gap markers changed: {source.count(_GAP)} vs {translated.count(_GAP)}")
    if markers_of(source) != markers_of(translated):
        errors.append(f"markers changed: {markers_of(source)} vs {markers_of(translated)}")
    if number_values(source) != number_values(translated):
        errors.append(f"numbers changed: {number_values(source)} vs {number_values(translated)}")
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
    elif block_type == "scenario_practice":
        if len(_as_list(translated.get("items"))) != len(_as_list(source.get("items"))):
            errors.append("item count changed in translation")
    elif block_type == "spot_the_error":
        if len(_as_list(translated.get("sentences"))) != len(_as_list(source.get("sentences"))):
            errors.append("sentence count changed in translation")
    for key in ("body", "stem", "text", "scenario", "caption"):
        if key in source and not str(translated.get(key, "")).strip():
            errors.append(f"{key} translated to empty")
    return errors


def _contains_term(text: str, term: str) -> bool:
    """Word-boundary term match; inflected suffixes still count."""
    if not term.strip():
        return False
    pattern = r"(?<!\w)" + re.escape(term.lower()) + r"\w*"
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
            if not _contains_term(target_text, target_term):
                errors.append(
                    f"segment {segment['id']}: glossary term {target_term!r} missing in translation"
                )
    return errors


def _tokens(text: str) -> list[str]:
    return re.findall(r"[^\W\d_]+(?:'[a-z]+)?", text.lower())


def _count_in(text: str, vocab: set[str]) -> int:
    return sum(1 for tok in _tokens(text) if tok in vocab)


def verify_spot_error(
    source: dict[str, Any], translated: dict[str, Any], answer_key: dict[str, Any]
) -> list[str]:
    """Exactly one planted sentence of the same kind must remain.

    Code re-derives the error by kind on the translated pair: the
    planted sentence and the true sentence, both translated as separate
    checked segments (the true translation arrives in the answer key as
    `translated_true_sentence`, or as a companion sentence). When the
    kind-specific check cannot be made the block is held, never ok.
    """
    errors: list[str] = []
    source_sentences = _as_list(source.get("sentences"))
    translated_sentences = _as_list(translated.get("sentences"))
    if len(translated_sentences) != len(source_sentences) or len(source_sentences) < 2:
        return ["sentence count changed in translation"]
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
    planted_text = str(translated_sentences[planted_index])
    companions = [
        str(sentence)
        for index, sentence in enumerate(translated_sentences)
        if index != planted_index
    ]
    if planted_text in companions:
        return ["planted error lost in translation: no sentence differs"]
    true_text = (
        answer_key.get("translated_true_sentence")
        or answer_key.get("true_sentence_translated")
        or translated.get("translated_true_sentence")
        or translated.get("true_sentence")
    )
    # A companion that is the translated true sentence may serve as the
    # pair partner only when no explicit true translation was supplied
    # and the key carries no source true sentence to translate-compare.
    candidates: list[str] = []
    if isinstance(true_text, str) and true_text.strip():
        candidates.append(true_text)
    if not candidates:
        candidates = companions
    if not candidates:
        return [f"planted {kind} check cannot be made: no true sentence translation"]
    _raw_glossary: Any = answer_key.get("glossary")
    glossary: dict[Any, Any] = _raw_glossary if isinstance(_raw_glossary, dict) else {}

    def pair_ok(partner: str) -> bool | None:
        """True/False when the kind check is decidable on this pair, else None."""
        if kind == "negation":
            return _count_in(planted_text, _NEGATIONS) != _count_in(partner, _NEGATIONS)
        if kind == "scope":
            p = sorted(t for t in _tokens(planted_text) if t in _SCOPE)
            q = sorted(t for t in _tokens(partner) if t in _SCOPE)
            return p != q and bool(p or q)
        if kind == "number_date":
            return number_values(planted_text) != number_values(partner)
        if kind == "bad_citation":
            return bool(markers_of(planted_text)) and markers_of(planted_text) != markers_of(
                partner
            )
        if kind == "entity_swap":
            # Swapped entity: a glossary target term present in one, absent
            # in the other; without a glossary, capitalised entity tokens.
            for target in glossary.values():
                if _contains_term(planted_text, str(target)) != _contains_term(
                    partner, str(target)
                ):
                    return True
            if glossary:
                return False
            cap_p = {t for t in re.findall(r"\b[A-ZÆØÅ][\wæøåÆØÅ]*", planted_text)}
            cap_q = {t for t in re.findall(r"\b[A-ZÆØÅ][\wæøåÆØÅ]*", partner)}
            # sentence-initial words are capitalised in both; compare sets
            return cap_p != cap_q
        if kind == "cause_reversal":
            # Clauses around a causal connector must be swapped.
            def clauses(text: str) -> tuple[set[str], set[str]] | None:
                toks = _tokens(text)
                idx = next((i for i, t in enumerate(toks) if t in _CAUSAL), None)
                if idx is None:
                    return None
                return (set(toks[:idx]), set(toks[idx + 1 :]))

            a, b = clauses(planted_text), clauses(partner)
            if a is None or b is None:
                return None
            (pl, pr), (ql, qr) = a, b

            def content_words(s: set[str]) -> set[str]:
                return {tok for tok in s if tok not in _NEGATIONS and len(tok) > 2}

            content = content_words
            return (
                bool(content(pl) & content(qr))
                and bool(content(pr) & content(ql))
                and not (
                    content(pl) & content(ql)
                    and content(pr) & content(qr)
                    and content(pl) == content(ql)
                )
            )
        return None

    verdicts = [pair_ok(c) for c in candidates]
    if any(v is True for v in verdicts):
        return errors
    if all(v is None for v in verdicts):
        return [f"planted {kind} check cannot be made on the translated pair"]
    return [f"planted {kind} error lost in translation"]
