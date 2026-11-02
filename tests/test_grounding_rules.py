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
    assert check_output(plan(["P1", "P2"], []), prescription, passages=passages) == []


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
