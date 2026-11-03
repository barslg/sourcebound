"""Probes: what a reader sees on a citation chip and in a reading block.

Owner finding 2026-11-03: chips carried the full URL and the whole heading
path; reading blocks collapsed paragraphs and printed the writer's quotes.
"""

from __future__ import annotations

from app.blocks.reading.render import render_for_learner
from app.i18n.citations import display_title, format_citation_address, format_citation_short
from app.providers.web import FetchedPage
from app.services.library import page_title

_WEB = {
    "kind": "web",
    "heading_path": "Chat completions: your first call>Statelessness is by design",
    "para": 1,
}


def test_short_address_is_the_last_heading_only() -> None:
    assert format_citation_short(_WEB, "en") == "Statelessness is by design, para 1"
    assert format_citation_address(_WEB, "en") == (
        "Chat completions: your first call>Statelessness is by design, para 1"
    )
    assert format_citation_short({"kind": "pdf", "page": 3, "para": 2}, "nb") == "s. 3, avsnitt 2"
    only = {"kind": "md", "heading_path": "Only", "para": None}
    assert format_citation_short(only, "uk") == "Only"


def test_url_title_becomes_the_documents_first_heading() -> None:
    assert display_title("https://dev.meta.ai/docs/x", _WEB) == "Chat completions: your first call"
    assert display_title("Hygiene protocol", _WEB) == "Hygiene protocol"
    assert (
        display_title("https://dev.meta.ai/docs/x", {"kind": "web"}) == "https://dev.meta.ai/docs/x"
    )


def _page(text: str, content_type: str = "text/html; charset=utf-8") -> FetchedPage:
    return FetchedPage(
        url="https://example.test/p", status=200, content_type=content_type, text=text
    )


def test_page_title_reads_title_then_h1_then_markdown_heading() -> None:
    html = (
        "<html><head><title> Chat &amp; state\n </title></head><body><h1>Other</h1></body></html>"
    )
    assert page_title(_page(html)) == "Chat & state"
    assert page_title(_page("<html><body><h1><span>Only</span> h1</h1></body></html>")) == "Only h1"
    assert page_title(_page("<html><body><p>no heading</p></body></html>")) == ""
    assert page_title(_page("# Top heading\n\nbody", "text/markdown")) == "Top heading"
    assert page_title(_page("plain text", "text/plain")) == ""


def test_learner_render_carries_no_quotes() -> None:
    view = render_for_learner(
        {"body": "One ⟦P1⟧ two.\n\nThree.", "citations": [{"handle": "P1", "quote": "evidence"}]}
    )
    assert "citations" not in view
    assert "evidence" not in str(view)
    assert view["segments"] == [
        {"text": "One "},
        {"text": "", "handle": "P1"},
        {"text": " two.\n\nThree."},
    ]
