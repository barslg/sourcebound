"""Gate (permanent): every passage of the NIST fixture is verbatim from the page its heading names.

A paraphrased "source" is a product defect: the tutor, the reader and the
citation check all treat fixture text as the document's own words. The
page text lives in tests/fixtures/nist_ai_100_1_pages.txt (pdftotext of
the public NIST PDF, printed page numbers); the check normalises only
what the extraction itself distorts (whitespace, space before
punctuation). Caller: pytest (the permanent gates).
"""

from __future__ import annotations

import re
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
HEADING_PAGE = re.compile(r"page (\d+)\s*$")


def normalise(text: str) -> str:
    """Collapse whitespace and drop the space pdftotext leaves before punctuation."""
    return re.sub(r"\s+([,.;:])", r"\1", " ".join(text.split()))


def pages() -> dict[int, str]:
    """Normalised text per printed page of the raw extraction."""
    raw = FIXTURES.joinpath("nist_ai_100_1_pages.txt").read_text()
    found = re.split(r"^=== Page (\d+) ===$", raw, flags=re.MULTILINE)
    return {int(n): normalise(body) for n, body in zip(found[1::2], found[2::2], strict=True)}


def passages() -> list[tuple[int, str]]:
    """(page, paragraph) of every sourced paragraph of the fixture."""
    out: list[tuple[int, str]] = []
    page: int | None = None
    for block in re.split(r"\n\s*\n", FIXTURES.joinpath("nist_ai_rmf_excerpt.md").read_text()):
        text = block.strip()
        if text.startswith("#"):
            match = HEADING_PAGE.search(text)
            page = int(match.group(1)) if match else None
        elif text and not text.startswith("Reuse notice:"):
            assert page is not None, ("a paragraph under a heading without a page", text[:60])
            out.append((page, " ".join(text.split())))
    return out


def test_every_fixture_passage_is_verbatim_on_its_named_page() -> None:
    sourced = passages()
    assert len(sourced) >= 10, "the gate must see the fixture's passages"
    by_page = pages()
    for page, paragraph in sourced:
        assert page in by_page, ("page missing from the raw extraction", page)
        assert normalise(paragraph) in by_page[page], ("not verbatim on page", page, paragraph)
