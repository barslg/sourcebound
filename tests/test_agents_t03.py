"""T03 agents: startup validation, versioned prompts, replay on real input.

Every agent ships prompts/<agent>/system.md (role, rules, output
contract, refusal cases); the version is the content hash logged per
call; the replay test feeds a stored real input through the prompt
assembly and output checks, asserting the schema and tree invariants.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.agents import architect, base, estimator, librarian, researcher


def test_all_agents_validate_at_startup() -> None:
    base.validate_agents()
    from app.llm import prompts

    registered = prompts.registered()
    assert {("researcher", "system"), ("architect", "system"), ("librarian", "system")} <= set(
        registered
    )


def test_prompt_version_is_the_content_hash() -> None:
    first = base.prompt_version("architect")
    assert first == base.prompt_version("architect")
    assert len(first) == 16


def test_prompt_files_carry_role_rules_contract_and_refusals() -> None:
    for agent in ("researcher", "architect", "librarian"):
        text = base.system_prompt(agent).lower()
        assert "role" in text
        assert "refusal" in text
        assert "output contract" in text


def test_researcher_is_code_first() -> None:
    assert researcher.rephrase_queries("  AI   Literacy Duty ") == [
        "AI Literacy Duty",
        "literacy duty",
    ]
    assert researcher.rephrase_queries("AI at work") == ["AI at work", "work"]
    assert researcher.rephrase_queries("   ") == []
    errors = researcher.check_output(
        {
            "query": "duty",
            "rephrased": [],
            "ranked": [{"handle": "P1", "rank": 1, "score": 0.9}],
            "web_candidates": [],
        },
        {"P1"},
    )
    assert errors == []
    bad = researcher.check_output(
        {
            "query": "duty",
            "rephrased": [],
            "ranked": [{"handle": "P9", "rank": 2, "score": 1.5}],
            "web_candidates": [{"url": "ftp://x", "title": "t", "why": "w"}],
        },
        {"P1"},
    )
    assert len(bad) == 4


def test_architect_replay_on_stored_real_input() -> None:
    raw_input = json.loads(Path("tests/fixtures/architect_input.json").read_text())
    output = json.loads(Path("tests/fixtures/architect_output.json").read_text())
    prompt = architect.build_prompt(
        raw_input["profile"],
        raw_input["constraints"],
        raw_input["prescription"],
        raw_input["passages"],
    )
    assert prompt.startswith(base.system_prompt("architect")[:50])
    assert "Article 4" in prompt
    assert architect.check_output(output, raw_input["prescription"]) == []


def test_librarian_spec_is_validated_and_executed() -> None:
    spec = {
        "container": "article.post",
        "fields": {"title": "h1", "body": "div.content"},
        "address_rule": {"kind": "heading_path", "selector": "h1"},
        "pagination": {"next_selector": None, "max_pages": 5},
    }
    assert librarian.validate_parser_spec(spec) == []
    html = (
        "<article class='post'><h1>Duty</h1>"
        "<div class='content'><p>Ensure literacy.</p></div></article>"
    )
    records = librarian.execute_parser_spec(html, spec)
    assert records and records[0]["body"] == "Ensure literacy."
    evil = dict(spec)
    evil["container"] = "div; rm -rf"
    assert librarian.validate_parser_spec(evil) != []


def test_estimator_is_a_forecast_never_a_limit() -> None:
    forecast = estimator.forecast({"blocks": 4, "calls": 8, "tokens": 12000})
    assert forecast["blocks"] == 4
    assert forecast["calls"] == 8
    assert forecast["note"] == "Forecast, not a limit."
    assert "cost_usd" in forecast and "seconds" in forecast


def test_methodology_prescription_is_deterministic() -> None:
    from app.course.methodologies import prescription_for

    assert prescription_for(["direct_instruction"])["lesson"] == ["teach", "example", "check"]
    assert prescription_for(["unknown"]) == {}
