from app.services.hold_text import handle_labels, human_hold_reason

TREE = {
    "modules": [
        {
            "topics": [
                {
                    "blocks": [
                        {
                            "source_refs": [
                                {"handle": "P586", "address_label": "Data Structures, para 12"},
                                {"handle": "P66", "address_label": None},
                            ]
                        }
                    ]
                }
            ]
        }
    ]
}

# stored as the reader and the coverage check wrote it on the Python course
STORED = (
    "coverage: Carry the P586 single-item tuple passage or name it in leftovers with a reason. "
    "(evidence: a tuple with one item is constructed by following a value with a comma "
    "(it is not sufficient to enclose a single value in parentheses).); "
    "grounding: Drop the range claim or cite P147 for it. (evidence: see P5 in the text)"
)


def test_a_hold_reason_names_passages_by_address_and_sets_the_evidence_apart_as_a_quote() -> None:
    shown = human_hold_reason(STORED, handle_labels(TREE))
    assert shown == (
        "coverage: Carry the Data Structures, para 12 single-item tuple passage or name it in "
        "leftovers with a reason. Quote: “a tuple with one item is constructed by following a "
        "value with a comma (it is not sufficient to enclose a single value in parentheses).”; "
        "grounding: Drop the range claim or cite another passage for it. "
        "Quote: “see P5 in the text”"
    )


def test_the_plan_field_name_is_not_shown_and_an_empty_reason_stays_empty() -> None:
    assert (
        human_hold_reason("coverage: source_handles is empty", {})
        == "coverage: the passages assigned to the block is empty"
    )
    assert human_hold_reason(None, {}) == ""


def test_only_refs_with_an_address_become_labels() -> None:
    assert handle_labels(TREE) == {"P586": "Data Structures, para 12"}
