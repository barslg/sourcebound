"""T11 rules as facts, on the shapes the owner's course has (permanent: these guard content).

Each test names the fact the check establishes and shows the pair the
2026-11-01 audit called defective / fine. A wiring mutant that drops a
check in the executor is caught by the smoke in test_jobs_smoke.py.
"""

from __future__ import annotations

from app.agents.architect import check_output
from app.course.grounding import (
    adjacency_holds,
    coverage_holds,
    has_numbers,
    taught_holds,
)
from app.course.handles import numbered_passages, reconcile_handles
from app.course.passages import rejoin_list_passages
from app.services.library import is_navigation_heading

TRUST = (
    "When a stage holds, you choose how much trust to grant:\n"
    "- Allow once: run this stage one time only. Muse Code saves nothing.\n"
    "- Always allow in this workspace: save a rule for this command's prefix.\n"
    "- Reject: deny the whole command."
)


def test_label_list_paragraphs_join_their_colon_intro_as_one_unit() -> None:
    """The audited uploads carry lists as unmarked 'Label: text' paragraphs."""
    rows = [
        {
            "id": "a",
            "address": "h:Trust/para1",
            "text": "When a stage holds, you choose how much trust to grant:",
        },
        {
            "id": "b",
            "address": "h:Trust/para2",
            "text": "Allow once: run this stage one time only. Muse Code saves nothing.",
        },
        {
            "id": "c",
            "address": "h:Trust/para3",
            "text": "Always allow in this workspace: save a rule for this command's prefix.",
        },
        {"id": "d", "address": "h:Trust/para4", "text": "Reject: deny the whole command."},
        {
            "id": "e",
            "address": "h:Trust/para5",
            "text": "Prefix rules apply by specificity. A deny rule always overrides an allow.",
        },
    ]
    merged = rejoin_list_passages(rows)
    assert [p["id"] for p in merged] == ["a", "e"]
    assert merged[0]["text"] == TRUST and merged[0]["merged_ids"] == ["a", "b", "c", "d"]


def test_coverage_holds_a_block_that_stops_at_the_colon_and_passes_one_naming_every_option() -> (
    None
):
    stops = "When a stage holds, you choose how much trust to grant. Think before you click."
    names = (
        "When a stage holds you pick the trust: allow once for this run only, always allow in this "
        "workspace to save a prefix rule, or reject to deny the command."
    )
    assert [h for h in coverage_holds(stops, {"P48": TRUST})] == [
        "coverage: cited passage P48 list item 'Allow once' is not named in the block text",
        "coverage: cited passage P48 list item 'Always allow in this workspace'"
        " is not named in the block text",
        "coverage: cited passage P48 list item 'Reject' is not named in the block text",
    ]
    assert coverage_holds(names, {"P48": TRUST}) == []
    prose_list = (
        "Before you run the loop:\n- Use a base image that already has the toolchain installed.\n"
        "- Know the limits of the sandbox before you rely on it.\n- Allowlist"
    )
    assert coverage_holds("A paraphrase that names an allowlist.", {"P1": prose_list}) == []
    assert coverage_holds("A paraphrase without it.", {"P1": prose_list}) == [
        "coverage: cited passage P1 list item 'Allowlist' is not named in the block text"
    ], "a short named item is checked by code; prose bullets go to the reader"


def test_adjacency_holds_only_where_text_crosses_passages_without_a_marker() -> None:
    cites = [
        {"handle": "P1", "quote": "Approval and sandboxing are on by default."},
        {"handle": "P2", "quote": "Each layer works on its own."},
    ]
    glued = "Approval and sandboxing are on by default. Each layer works on its own. ⟦P1⟧ ⟦P2⟧"
    marked = "⟦P1⟧ Approval and sandboxing are on by default. ⟦P2⟧ Each layer works on its own."
    reversed_order = (
        "⟦P2⟧ Each layer works on its own. ⟦P1⟧ Approval and sandboxing are on by default."
    )
    assert adjacency_holds(glued, cites) == [
        "false adjacency: text from P1 runs into text from P2 without a ⟦P2⟧ marker between them"
    ]
    assert adjacency_holds(marked, cites) == []
    assert adjacency_holds(reversed_order, cites) == [], "citation order is not text order"
    assert adjacency_holds("A paraphrase of both.", cites) == [], (
        "nothing located, the reader judges"
    )


def test_taught_holds_name_the_unquoted_claim_and_keep_commands_whole() -> None:
    taught = [
        "Type / in the composer to open the command palette. /new starts a fresh session and keeps "
        "the terminal scrollback. /compact summarizes prior context to reclaim room."
    ]
    untaught = {
        "options": [{"text": "/tasks", "correct": True}, {"text": "/new", "correct": False}]
    }
    quoted = {
        "options": [
            {"text": "/new starts a fresh session", "correct": True},
            {"text": "Nope", "correct": False},
        ]
    }
    command = {"blanks": ["/compact"]}
    assert taught_holds(untaught, taught) == [
        "not taught: the key claim '/tasks' is not quoted from a passage this topic taught"
    ]
    assert taught_holds(quoted, taught) == [] and taught_holds(command, taught) == []
    scenario = {"answer": "/new starts a fresh session; /stop stops background work", "items": []}
    assert taught_holds(scenario, taught, "scenario_practice") == [
        "not taught: the key claim '/stop stops background work' is not quoted from a passage"
        " this topic taught"
    ]
    assert (
        taught_holds(
            {"items": [{"text": "State the command.", "tier": "apply"}]},
            taught,
            "scenario_practice",
        )
        == []
    )


def test_figure_gate_wants_a_quantity_not_a_label_digit() -> None:
    assert has_numbers("Muse Code holds for review at stage 3 of 3.") is False
    assert has_numbers("The default model is muse-spark-1.2.") is False
    assert (
        has_numbers("Wash hands for at least 20 seconds; in 2024, 35 % of wards complied.") is True
    )
    assert has_numbers("See Figure 2 for the layers.") is True


def test_architect_check_keeps_the_sixty_percent_rule_and_drops_the_title_word_rejection() -> None:
    passages = [
        {"handle": "P1", "text": "Muse Code holds a stage for review.", "is_content": True},
        {
            "handle": "P2",
            "text": "The sandbox refuses to run without a boundary.",
            "is_content": True,
        },
        {"handle": "P3", "text": "Voice input is unavailable on Windows.", "is_content": True},
    ]

    def plan(cited: list[str], uncovered: list[dict[str, str]]) -> dict[str, object]:
        return {
            "modules": [
                {
                    "plan_node_id": "m1",
                    "title": "Module",
                    "purpose": "p",
                    "entry_knowledge": "e",
                    "exit_knowledge": "x",
                    "topics": [
                        {
                            "plan_node_id": "t1",
                            "kind": "module_test",
                            "title": "Module Test: staged review",
                            "objectives": ["o"],
                            "through_line_note": "n",
                            "blocks": [
                                {
                                    "plan_node_id": "b1",
                                    "role": "test",
                                    "block_type": "choice",
                                    "brief": "Which stage holds?",
                                    "source_refs": [{"handle": h} for h in cited],
                                }
                            ],
                        }
                    ],
                }
            ],
            "estimate_inputs": {"blocks": 1, "calls": 1, "tokens": 1},
            "through_line": {"case": "c", "handoffs": []},
            "uncovered": uncovered,
        }

    prescription = {"module_test": ["test"]}
    low = check_output(plan(["P1"], []), prescription, passages=passages)
    assert low == [
        "plan cites 33% of content passages; uncovered passages must be listed in uncovered:"
        " ['P2', 'P3']"
    ]
    declared = plan(
        ["P1"], [{"handle": "P2", "reason": "later"}, {"handle": "P3", "reason": "off topic"}]
    )
    assert check_output(declared, prescription, passages=passages) == [], (
        "'test' and 'staged' in the title stop nothing"
    )
    one_forgotten = plan(["P1"], [{"handle": "P2", "reason": "later"}])
    assert check_output(one_forgotten, prescription, passages=passages) == [], (
        "one undeclared leftover is declared by the merge, never a rejection"
    )
    assert check_output(plan(["P1", "P2"], []), prescription, passages=passages) == []
    typed = plan(["P1", "P2"], [])
    typed["modules"][0]["topics"][0]["blocks"][0].update(
        {"role": "test", "block_type": "scenario_practice"}
    )
    assert check_output(typed, prescription, passages=passages) == []
    typed["modules"][0]["topics"][0]["blocks"][0].update({"role": "teach", "brief": "A topic"})
    assert check_output(typed, {"module_test": ["teach"]}, passages=passages) == [
        "block b1: a teach block is a reading, not 'scenario_practice'"
    ], "teach and example blocks are readings; the plan typed 24 of 30 examples as exercises"


def test_one_numbering_keeps_the_plans_handles_through_generation() -> None:
    rows = [
        {"passage_id": "x", "source_id": "s", "address": "h:A/para2", "text": "Second."},
        {"passage_id": "y", "source_id": "s", "address": "h:A/para1", "text": "First."},
        {"passage_id": "z", "source_id": "s", "address": "h:A/para3", "text": "Third."},
    ]
    numbered = numbered_passages(rows)
    assert [(p["handle"], p["passage_id"]) for p in numbered] == [
        ("P1", "y"),
        ("P2", "x"),
        ("P3", "z"),
    ]
    kept = reconcile_handles(numbered, {"P7": "z", "P2": "x"})
    assert [(p["handle"], p["passage_id"]) for p in kept] == [("P2", "x"), ("P7", "z"), ("P8", "y")]
    assert reconcile_handles(numbered, {"P1": "y"}) == numbered


def test_navigation_headings_are_not_content() -> None:
    assert is_navigation_heading("Muse Code>Next steps") and is_navigation_heading(
        "What to read next"
    )
    assert not is_navigation_heading("Muse Code>Install")


def test_coverage_ignores_code_lines_and_fenced_blocks() -> None:
    """A code line is not a claim a reading must name (Muse API course, first two blocks held)."""
    code_list = (
        "The call looks like this:\n- response = client.chat.completions.create(\n"
        '- model="muse-spark-1.3"\n- Allow once: run this stage one time only'
    )
    named = "A reading that names allow once: run this stage one time only."
    assert coverage_holds(named, {"P1": code_list}) == []
    fenced = "Steps:\n```\n- not an item\n```\n- Reject: deny the whole command"
    assert coverage_holds("Nothing named.", {"P1": fenced}) == [
        "coverage: cited passage P1 list item 'Reject' is not named in the block text"
    ]


def test_adjacency_locates_a_quote_after_its_own_marker_and_skips_code_fences() -> None:
    """The 2026-11-02 pricing block: the one-word quote "Usage" of P949 was
    found at its first occurrence (inside P936's sentence) and every row of
    the table became a false adjacency; a code example stitched from two
    cited passages inside one fence carried no marker between them."""
    from app.course.grounding import adjacency_holds

    text = (
        "⟦P936⟧ Usage is priced per 1M tokens for Muse Wonder. "
        "For Muse Spark, ⟦P949⟧ Usage is priced per 1M tokens as well.\n\n"
        '```python\nfirst = client.responses.create(model="muse-spark-1.3")\n'
        "followup = client.responses.create(previous_response_id=first.id)\n```"
    )
    citations = [
        {"handle": "P936", "quote": "Usage"},
        {"handle": "P949", "quote": "Usage"},
        {"handle": "P345", "quote": 'first = client.responses.create(model="muse-spark-1.3")'},
        {
            "handle": "P666",
            "quote": "followup = client.responses.create(previous_response_id=first.id)",
        },
    ]
    assert adjacency_holds(text, citations) == []
    unmarked = text.replace("⟦P949⟧ ", "")
    assert adjacency_holds(unmarked, citations) == [], (
        "a one-word quote with no marker of its own locates nothing"
    )
    prose = (
        "⟦P936⟧ Cached input costs less when part of your prompt matches a cached prefix. "
        "Web search grounding costs $2.50 per 1,000 search queries in addition."
    )
    assert adjacency_holds(
        prose,
        [
            {"handle": "P936", "quote": "Cached input costs less when part of your prompt"},
            {"handle": "P962", "quote": "Web search grounding costs $2.50 per 1,000 search"},
        ],
    ) == [
        "false adjacency: text from P936 runs into text from P962 without a ⟦P962⟧ marker "
        "between them"
    ]


def test_an_assigned_passage_neither_cited_nor_declared_is_a_coverage_hold() -> None:
    """The 2026-11-02 Muse API course: m5-t5-b1 was assigned P639 and the
    writer silently dropped it; only the paid reader noticed."""
    from app.course.grounding import assignment_holds

    assigned = ["P637", "P638", "P639"]
    assert assignment_holds(assigned, cited=["P637", "P638"], leftovers=[]) == [
        "coverage: assigned passage P639 is neither cited nor declared in leftovers"
    ]
    assert (
        assignment_holds(
            assigned,
            cited=["P637"],
            leftovers=[{"handle": "P638", "reason": "duplicate"}, {"handle": "P639"}],
        )
        == []
    )
    assert assignment_holds([], cited=[], leftovers=[]) == [], "no assignment, nothing to hold"


WRAPPED = (
    "You are the engineer on a new HTTP reverse proxy + load balancer. Build it in\n"
    "TypeScript using only Node.js built-in modules plus the existing vitest tooling."
)


def test_a_quote_that_differs_from_its_passage_only_in_whitespace_is_verbatim() -> None:
    """P1515 of the Muse API course wraps mid-sentence; the writer quoted it with a space.

    The exact-containment check held m6-t3-b1 for that (2026-11-03, 15 of
    the course's 3,904 citations): whitespace is not the writer's claim.
    A transposed bracket or a missing word still is.
    """
    from app.course.grounding import locate_verbatim

    start = WRAPPED.index("Build it in")
    assert locate_verbatim(WRAPPED, "Build it in TypeScript using only") == (
        start,
        start + len("Build it in\nTypeScript using only"),
    )
    assert locate_verbatim(WRAPPED, "Build it in\nTypeScript") == (start, start + 22)
    assert locate_verbatim(WRAPPED, "Build it in  TypeScript") == (start, start + 22)
    assert locate_verbatim(WRAPPED, "Build it TypeScript") is None
    assert (
        locate_verbatim('tools=[{"type": "web_search"}]:', 'tools=[{"type": "web_search"]}:')
        is None
    )
    assert locate_verbatim(WRAPPED, "") is None
    assert locate_verbatim(WRAPPED, "   ") is None


def test_replay_accepts_a_stored_quote_that_wraps_differently_from_its_passage() -> None:
    """The offline replay judges a quote the way the executor does: by words, not line breaks."""
    from app.course.replay import replay_blocks

    numbered = [{"passage_id": "p1", "handle": "P1", "text": WRAPPED, "merged_ids": []}]
    block = {
        "id": "b",
        "node": "m1-t1-b1",
        "role": "teach",
        "block_type": "reading",
        "status": "held",
        "hold_reason": "",
        "version_id": "v1",
        "content": {"body": "⟦P1⟧ Build it in TypeScript using only Node.js built-in modules."},
        "answer_key": {},
        "topic": "m1-t1",
        "topic_kind": "core",
        "topic_title": "Proxy",
        "objectives": [],
        "plan_content": {"source_handles": ["P1"]},
        "citations": [("p1", "Build it in TypeScript using only")],
    }
    rows = replay_blocks([block], numbered)
    assert [h for h in rows[0]["holds"] if h.startswith("quote is not verbatim")] == []
    block["citations"] = [("p1", "Build it TypeScript")]
    rows = replay_blocks([block], numbered)
    assert rows[0]["holds"] == ["quote is not verbatim in its passage (P1)"]


QUOTED_STATE = (
    "Prompt the model to keep an explicit running state. Verbatim from the run, trimmed:\n"
    "# HTTP Reverse Proxy + Load Balancer — Working Memory\n"
    "## Goal\n"
    "Build a proxy in TypeScript using only Node.js built-ins.\n"
    "## Key Decisions\n"
    "- Node built-in `http`/`https` only, no Express.\n"
    "- CircuitBreaker: CLOSED/OPEN/HALF_OPEN, failure threshold, cooldown.\n"
    "- Router: host + path prefix matching, longest-prefix wins.\n"
)


def test_a_quoted_document_inside_a_passage_is_an_illustration_not_a_claim_list() -> None:
    """P1520 of the Muse API course quotes a STATE.md file; its bullets are the file, not claims.

    The coverage check held m6-t3-b1 fifteen times over three rewrites for
    not naming CircuitBreaker and Router — requirements of the sample
    project, not what the passage teaches. A passage that embeds a
    document (a markdown heading inside its text) carries no list items
    for code; the reader's coverage item still judges it.
    """
    from app.course.grounding import list_items

    assert list_items(QUOTED_STATE) == []
    assert coverage_holds("We keep a STATE.md running state.", {"P1520": QUOTED_STATE}) == []
    plain = "The top level carries:\n- sessions: one summary per stream\n- events: one per line"
    assert list_items(plain) == ["sessions: one summary per stream", "events: one per line"]
