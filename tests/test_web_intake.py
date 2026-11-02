"""Web intake (permanent, data): a page becomes addressed passages, never glue or markup.

The 2026-11 defect: a docs page read as ONE passage with words glued
across element boundaries ("built for Muse SparkMuse Code"), inline
tags cutting paragraphs, lists dropped, and a page without a matching
container chunked as raw HTML. The thin rule is the measured one.
"""

from __future__ import annotations

from typing import Any

from app.clients.web import FetchedPage
from app.services.library import is_thin_page, web_passages

PAGE = (
    '<!DOCTYPE html><html><head><title>T</title><script>var x = "<article>";</script></head>'
    "<body><nav><a>Home</a><a>Docs</a></nav><article><h1>Muse Code</h1>"
    "<p>Install Muse Code with the one-line installer. It puts a native <code>muse</code>"
    " binary on your path.</p>"
    "<h2>Interactive or headless</h2><p>The same binary runs two ways:</p>"
    "<ul><li><b>Interactive:</b> muse opens a terminal UI.</li>"
    "<li>Headless: <code>muse exec</code> runs one prompt.</li></ul>"
    "<p>First unclosed paragraph<p>Second paragraph after an unclosed one.</p>"
    '<figure><pre data-language="bash"><div><span>bash</span><button>Copy code</button></div>'
    "<code><span data-line>cd /path/to/project</span><span data-line>muse</span></code></pre>"
    "</figure><h2>Next</h2><p>Last.</p></article><footer>Copyright</footer></body></html>"
)

GLUED = (
    "Muse Code is Meta's coding agent for the terminal and CI, built for Muse SparkMuse Code and "
    "the Model APIWorkflowsSession messagingInstall Muse Code with the one-line installer. It puts "
    "a native museConfirm it's on your path:Run musetrust the workspacemuse-spark-1.2Reopen the "
    "sign-in options any time from an interactive session with /loginCIMETA_API_KEYauthentication "
    "and billingThe same binary runs two ways:Muse Code runs on macOS, Linux, and Windows from one "
    "codebase. A few capabilities work differently on Windows:Beyond the CLI, you can drive a Muse "
    "Code session from your own program over a stable, versioned session protocol."
)


def _page(html: str, content_type: str = "text/html") -> FetchedPage:
    return FetchedPage(
        url="https://example.test/p", status=200, content_type=content_type, text=html
    )


def test_html_page_becomes_addressed_passages_without_glue_or_chrome() -> None:
    passages, spec = web_passages(_page(PAGE), None)
    texts = [p["text"] for p in passages]
    assert spec is not None and spec["container"] == "article"
    assert texts[0] == (
        "Install Muse Code with the one-line installer. It puts a native muse binary on your path."
    )
    assert (
        "The same binary runs two ways:\n- Interactive: muse opens a terminal UI."
        "\n- Headless: muse exec runs one prompt."
    ) in texts
    assert (
        "First unclosed paragraph" in texts and "Second paragraph after an unclosed one." in texts
    )
    assert "cd /path/to/project\nmuse" in texts, (
        "a code block keeps its lines and drops the language label"
    )
    joined = "\n".join(texts)
    for chrome in ("Home", "Copyright", "Copy code", "var x", "<"):
        assert chrome not in joined, chrome
    keys = [p["address_key"] for p in passages]
    assert len(keys) == len(set(keys)) and keys[0] == "h:Muse Code/para1"
    assert passages[1]["address"] == {
        "kind": "web",
        "heading_path": "Muse Code>Interactive or headless",
        "para": 1,
    }


def test_page_without_article_or_section_still_reads_the_body_never_the_markup() -> None:
    html = (
        "<html><body><div><p>One readable sentence here.</p><p>Another one.</p></div></body></html>"
    )
    passages, spec = web_passages(_page(html), None)
    assert [p["text"] for p in passages] == ["One readable sentence here.", "Another one."]
    assert spec is not None and spec["container"] == "body"


def test_plain_text_answer_is_chunked_as_text() -> None:
    passages, spec = web_passages(_page("Alpha.\n\nBeta.", "text/plain"), None)
    assert [p["text"] for p in passages] == ["Alpha.", "Beta."] and spec is None


def test_thin_rule_matches_the_measured_pages() -> None:
    assert is_thin_page(GLUED), "the stand's glued docs page (8.4 joins per 100 tokens)"
    assert is_thin_page("Home Docs Pricing Sign in Blog Careers " * 8), "a nav-only shell"
    assert is_thin_page("Grant Muse Code only the access you intend."), "too little text"
    readable = (
        "Muse Code reviews a compound shell command one stage at a time, not as a single line. "
        "It parses the command into ordered stages, checks each one against policy, and blocks "
        "on the first stage it cannot approve. On Windows, agent-run commands go through "
        "PowerShell rather than Bash, and the same stage-by-stage review applies. Use --yolo "
        "only where you already trust both the environment and the code."
    )
    assert not is_thin_page(readable)


async def test_a_thin_source_cannot_be_approved(t03_session: Any) -> None:
    """R8 data guard: approving a source that gave no readable text is refused with the message."""
    from uuid import UUID

    import pytest
    import sqlalchemy as sa

    from app.errors import ValidationError
    from app.services.library import THIN_MESSAGE, approve_source

    source = (
        await t03_session.execute(
            sa.text(
                "INSERT INTO sources (content_sha256, kind, media, title, approval, storage_path,"
                " ingest_status, ingest_message) VALUES ('thin-probe', 'url', 'text', 'u',"
                " 'pending', '', 'thin', :m) RETURNING id"
            ),
            {"m": THIN_MESSAGE},
        )
    ).scalar_one()
    with pytest.raises(ValidationError, match="no readable text"):
        await approve_source(t03_session, source_id=UUID(str(source)), approved_by=None)


TABLE_PAGE = (
    "<!DOCTYPE html><html><body><article><h1>Pricing</h1><h2>Muse Spark</h2>"
    "<table><thead><tr><th>Usage</th><th>Standard</th><th>Contributor</th></tr></thead>"
    "<tbody><tr><td>Cached input</td><td>$0.15</td><td>$0.002</td></tr>"
    "<tr><td>Input</td><td>$1.25</td><td>$0.10</td></tr></tbody></table>"
    "<h2>Recipe</h2><table><tr><td>Model</td><td>muse-spark-1.3</td></tr>"
    "<tr><td>Language</td><td>Python</td></tr></table>"
    "<p>After the tables.</p></article></body></html>"
)


def test_a_table_is_one_passage_with_labelled_rows_never_a_passage_per_cell() -> None:
    """The 2026-11-02 Muse API course: 834 of 2,722 passages were lone cells
    ('Usage', '$0.20', 'n'), each assigned, each cited with its own marker."""
    passages, _ = web_passages(_page(TABLE_PAGE), None)
    texts = [p["text"] for p in passages]
    assert texts == [
        "Usage: Cached input | Standard: $0.15 | Contributor: $0.002\n"
        "Usage: Input | Standard: $1.25 | Contributor: $0.10",
        "Model: muse-spark-1.3\nLanguage: Python",
        "After the tables.",
    ]
    assert passages[0]["address_key"] == "h:Pricing>Muse Spark/para1"
