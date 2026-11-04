"""The 2026-11-04 Python-course audit as facts, on texts stored in that course.

Every input below is the stored text of a block or passage of course
13e63d1a (block ids in the names); each test names the rule it proves and
pairs the audited defect with a stored block the audit found correct.
"""

from __future__ import annotations

from typing import Any

from app.course.errorspot import is_content_text
from app.course.exercise_shape import (
    cloze_holds,
    exercise_holds,
    matching_holds,
    ordering_holds,
)
from app.course.grounding import answer_key_claims
from app.course.handles import numbered_passages
from app.course.replay import replay_blocks
from app.course.text_checks import (
    decode_escapes,
    escape_holds,
    narration_holds,
    number_holds,
    python_code_holds,
)

HASH_TAUGHT = [
    "In the following examples, input and output are distinguished by the presence or absence of "
    "prompts (>>> and …): to repeat the example, you must type everything after the prompt, when "
    "the prompt appears; lines that do not begin with a prompt are output from the interpreter. "
    "Note that a secondary prompt on a line by itself in an example means you must type a blank "
    "line; this is used to end a multi-line command.",
    "Many of the examples in this manual, even those entered at the interactive prompt, include "
    "comments. Comments in Python start with the hash character, #, and extend to the end of the "
    "physical line. A comment may appear at the start of a line or following whitespace or code, "
    "but not within a string literal. A hash character within a string literal is just a hash "
    "character. Since comments are to clarify code and are not interpreted by Python, they may be "
    "omitted when typing in examples.",
]
GUESSABLE_8997 = {
    "answer": "to repeat the example, you must type everything after the prompt, when the prompt "
    "appears; lines that do not begin with a prompt are output from the interpreter. "
    "A hash character within a string literal is just a hash character.",
    "options": [
        {
            "text": "to repeat the example, you must type everything after the prompt, when the "
            "prompt appears; lines that do not begin with a prompt are output from the "
            "interpreter.",
            "correct": True,
        },
        {
            "text": "Type the >>> marks and the output lines as input, and delete the hash part "
            "inside quotes as a comment",
            "correct": False,
        },
        {
            "text": "A hash character within a string literal is just a hash character.",
            "correct": True,
        },
        {
            "text": "Comments must always be typed exactly because Python interprets them to run "
            "the tracker",
            "correct": False,
        },
    ],
}
WITH_OPEN = 'with open("myfile.txt") as f:\n    for line in f:\n        print(line, end="")'
CLEAN_74BA = {
    "answer": WITH_OPEN,
    "options": [
        {"text": 'for line in open("myfile.txt"):\n    print(line, end="")', "correct": False},
        {
            "text": 'f = open("myfile.txt")\nfor line in f:\n    print(line, end="")',
            "correct": False,
        },
        {"text": WITH_OPEN, "correct": True},
        {
            "text": 'with open("myfile.txt"):\n    for line in f:\n        print(line, end="")',
            "correct": False,
        },
    ],
}
WITH_TAUGHT = [
    'for line in open("myfile.txt"):\n    print(line, end="")',
    WITH_OPEN,
    "The problem with this code is that it leaves the file open for an indeterminate amount of "
    "time after this part of the code has finished executing.",
]


def test_giveaway_choice_with_only_correct_options_copied_from_the_passage_is_held() -> None:
    """8997ce32: both correct options are passage lines, both wrong ones are the writer's words."""
    holds = exercise_holds(GUESSABLE_8997, "choice", HASH_TAUGHT)
    assert len(holds) == 1 and holds[0].startswith("giveaway: only the correct options")


def test_choice_whose_wrong_options_are_passage_lines_too_is_not_a_giveaway() -> None:
    """74ba08a3, the audit's strongest exercise: every option is code from the passages."""
    assert exercise_holds(CLEAN_74BA, "choice", WITH_TAUGHT) == []


def test_the_quote_moved_to_the_answer_is_the_only_grounded_claim_of_a_choice() -> None:
    """The correct options are the writer's words; the key rests on the quoted `answer` clauses."""
    reworded = {
        "answer": "A hash character within a string literal is just a hash character.",
        "options": [
            {"text": "A # inside quotes is plain text", "correct": True},
            {"text": "A # inside quotes starts a comment", "correct": False},
        ],
    }
    assert answer_key_claims(reworded, "choice") == [
        "A hash character within a string literal is just a hash character."
    ]
    assert answer_key_claims({"options": reworded["options"]}, "choice") == [
        "A # inside quotes is plain text"
    ]
    assert exercise_holds(reworded, "choice", HASH_TAUGHT) == []


def test_cloze_blank_longer_than_three_words_or_starting_with_a_prompt_is_held() -> None:
    """d4724a70 and 4f477efe: whole passage sentences and a `>>>` line cannot be typed back."""
    sentences = [
        "The equal sign (=) is used to assign a value to a variable",
        "Division (/) always returns a float",
    ]
    assert len(cloze_holds(sentences)) == 2
    prompt = cloze_holds([">>> f = open('workfile', 'w')"])
    assert len(prompt) == 1 and "prompt" in prompt[0]
    assert cloze_holds(["range()", "end point", "with"]) == []
    assert cloze_holds(["one two three"]) == []
    assert len(cloze_holds(["one two three four"])) == 1


def test_matching_with_two_pairs_is_held_and_three_or_four_pairs_pass() -> None:
    """a37a66c1 has two pairs (guessable by elimination); f21158f3 has three, 6ffd6545 four."""
    two = [
        {"left": "the stop entry that should end entry reading", "right": "breaks out of the loop"},
        {"left": "the blank entry that should be left unprocessed", "right": "continues"},
    ]
    four = [{"left": f"left {i}", "right": f"right {i}"} for i in range(4)]
    assert matching_holds(two)[0].startswith("matching: 2 pairs")
    assert matching_holds(four) == []
    assert matching_holds(four[:3]) == []


def test_ordering_that_mixes_code_lines_and_prose_steps_is_held() -> None:
    """8e42f4e1 mixes code and sentences; 7b67e396 (prose) and afdc6f22 (code) are uniform."""
    mixed = [
        "a, b = 0, 1",
        "while a < 10:",
        "The body of the loop is indented",
        "print(a)",
        "a, b = b, a+b",
        "must be followed by a blank line to indicate completion",
    ]
    prose = [
        "The keyword def introduces a function definition",
        "It must be followed by the function name and the parenthesized list of formal parameters",
        "The return statement returns with a value from a function",
    ]
    code = [
        "try:\n...         result = x / y",
        'except ZeroDivisionError:\n...         print("division by zero!")',
        'finally:\n...         print("executing finally clause")',
    ]
    assert ordering_holds(mixed)[0].startswith("ordering: the items mix code and prose")
    assert ordering_holds(prose) == [] and ordering_holds(code) == []


def test_example_that_narrates_its_sourcing_is_held_and_a_clean_example_is_not() -> None:
    """a7d90d3a says "not given in the passages ... as a stand-in"; e7295a25 just shows dir()."""
    narrating = (
        "The tracker-specific limit number and label text are chosen by the caller for the "
        "tracker and are not given in the passages, so the demonstration uses the passage "
        "operation and its own names and values as a stand-in."
    )
    clean = (
        "**Input**\n\n⟦P420⟧ Without arguments, `dir()` lists the names you have defined "
        "currently: the session has already run `a = [1, 2, 3, 4, 5]` for the expense list."
    )
    held = narration_holds(narrating, [])
    assert len(held) == 1 and "'not given in'" in held[0] and "'passages'" in held[0]
    assert narration_holds(clean, []) == []


SYNTAX_LESSON = (
    "**Action**\n\n⟦P75⟧ Syntax errors appear when the learner runs `while True print('Hello "
    "world')`:\n\n```\n>>> while True print('Hello world')\n  File \"<stdin>\", line 1\n    "
    "while True print('Hello world')\n               ^^^^^\nSyntaxError: invalid syntax\n```\n"
)
DIR_EXAMPLE = (
    "```\n>>> dir(fibo)\n['__name__', 'fib', 'fib2']\n>>> dir()\n"
    "['__builtins__', '__name__', 'a', 'fib', 'fibo', 'sys']\n```"
)
TRACEBACK_EXAMPLE = (
    '```\n>>> 10 * (1/0)\nTraceback (most recent call last):\n  File "<stdin>", line 1, in '
    "<module>\n    10 * (1/0)\n          ~^~\nZeroDivisionError: division by zero\n```"
)
PLACEHOLDER_CODE = (
    "```python\nclass UpperCamelCase:\n    def lowercase_with_underscores(self):\n        ....\n"
    "        ....\n```"
)


def test_python_snippet_that_does_not_compile_is_held_with_its_line() -> None:
    """e46313d5 shows `....` as code; the transcripts of 5f58c0dc and e7295a25 parse."""
    held = python_code_holds(PLACEHOLDER_CODE)
    assert len(held) == 1 and "line 3" in held[0] and "'....'" in held[0]
    assert python_code_holds(DIR_EXAMPLE) == []
    assert python_code_holds(TRACEBACK_EXAMPLE) == []


def test_a_snippet_shown_as_a_syntax_error_on_purpose_is_the_lesson_not_a_defect() -> None:
    """db6a3d15 teaches `while True print(...)`; its transcript ends in SyntaxError."""
    assert python_code_holds(SYNTAX_LESSON) == []
    without_lesson = SYNTAX_LESSON.replace("SyntaxError: invalid syntax", "done")
    assert len(python_code_holds(without_lesson)) == 1


def test_literal_unicode_escape_in_prose_is_decoded_and_in_code_it_is_kept() -> None:
    """8f9c588b wrote `file object`\\u2019s; a tutorial's own "\\u00e9" in code stays."""
    stored = "the `file object`\\u2019s current position"
    assert decode_escapes(stored) == "the `file object`’s current position"
    assert escape_holds(stored) != []
    teaching = 'Write `"\\u00e9"` to get an accented e'
    assert decode_escapes(teaching) == teaching and escape_holds(teaching) == []


def test_section_labels_are_not_content_and_real_short_lines_are() -> None:
    """Passages 'Footnotes', 'Warning', 'See also', 'Note' claim nothing; 'Use docstrings.' does."""
    for label in ("Footnotes", "Warning", "See also", "Note"):
        assert is_content_text(label) is False
    for line in ("Use docstrings.", "list.clear()", "Reject"):
        assert is_content_text(line) is True


def test_read_time_numbering_flags_a_stored_label_without_moving_any_handle() -> None:
    """Passages stored before the rule obey it at read time; handles stay as the plan cites them."""
    rows = [
        {"passage_id": "a", "source_id": "s", "address": "p/para1", "text": "Use docstrings."},
        {"passage_id": "b", "source_id": "s", "address": "p/para2", "text": "Footnotes"},
        {"passage_id": "c", "source_id": "s", "address": "p/para3", "text": "list.clear()"},
    ]
    numbered = numbered_passages(rows)
    assert [(p["handle"], p["is_content"]) for p in numbered] == [
        ("P1", True),
        ("P2", False),
        ("P3", True),
    ]


def test_replay_holds_a_stored_giveaway_choice_through_the_same_checks_as_the_executor() -> None:
    """The stored 8997ce32 as the replay reads it: taught passages from the plan."""
    numbered = [
        {"passage_id": f"pid{i}", "handle": f"P{i}", "text": text}
        for i, text in enumerate(HASH_TAUGHT, 1)
    ]
    plan = {
        "source_handles": ["P1", "P2"],
        "source_passages": {"P1": "pid1", "P2": "pid2"},
    }

    def block(node: str, role: str, kind: str, content: dict[str, Any], key: dict[str, Any]) -> Any:
        return {
            "id": node,
            "node": node,
            "role": role,
            "block_type": kind,
            "status": "accepted",
            "hold_reason": "",
            "version_id": f"v-{node}",
            "content": content,
            "answer_key": key,
            "topic": "t1",
            "topic_kind": "lesson",
            "topic_title": "Run examples",
            "objectives": [],
            "plan_content": plan,
            "citations": [("pid1", HASH_TAUGHT[0][:60]), ("pid2", HASH_TAUGHT[1][:60])],
        }

    stored = [
        block("t1-b1", "teach", "reading", {"body": "Prompts mark input."}, {}),
        block(
            "t1-b3",
            "check",
            "choice",
            {
                "stem": "Which actions repeat the example?",
                "options": [o["text"] for o in GUESSABLE_8997["options"]],
            },
            {"answer": GUESSABLE_8997["answer"], "correct": [0, 2]},
        ),
    ]
    rows = {r["node"]: r["holds"] for r in replay_blocks(stored, numbered)}
    assert rows["t1-b1"] == []
    assert len(rows["t1-b3"]) == 1 and rows["t1-b3"][0].startswith("giveaway: ")


def test_a_choice_with_no_wrong_option_has_no_shape_to_give_away() -> None:
    """With every option correct the shape cannot split right from wrong, so nothing is held."""
    every = {
        **GUESSABLE_8997,
        "options": [o for o in GUESSABLE_8997["options"] if o["correct"]],
    }
    assert exercise_holds(every, "choice", HASH_TAUGHT) == []


def test_an_eight_word_step_with_an_equals_sign_is_prose_and_a_shorter_one_is_code() -> None:
    """The prose boundary is eight words: a sentence that mentions `=` is still a sentence."""
    assert ordering_holds(["x = 1", "y = 2", "we set the total = sum of amounts"]) != []
    assert ordering_holds(["x = 1", "y = 2", "set the total = sum of amounts"]) == []


LONG_CODE = "result_value = compute_total(first_amount, second_amount, third_amount)"
LONG_PROSE = "The loop keeps asking for the next entry until the learner types the stop word"


def test_the_ordering_hold_names_the_code_line_and_the_sentence_cut_at_forty_characters() -> None:
    """The Fix list line quotes the first code item and the first prose item, forty characters."""
    holds = ordering_holds([LONG_CODE, LONG_PROSE, "print(result_value)"])
    assert holds == [
        "ordering: the items mix code and prose ('result_value = compute_total(first_amoun' is"
        " code, 'The loop keeps asking for the next entry' is a sentence) - use one procedure"
        " whose steps are all code lines or all sentences"
    ]


def test_a_keyword_line_without_equals_or_call_is_code_and_a_blank_step_is_not() -> None:
    """`try:` is code by its keyword alone; an empty step is nothing, so it mixes with nothing."""
    assert ordering_holds(["try:", "The loop runs first"])[0].startswith("ordering: ")
    assert ordering_holds(["", "The loop runs first", "Then the loop ends"]) == []


def test_a_short_line_with_an_equals_sign_is_code_and_an_eight_word_one_is_prose() -> None:
    """Under eight words and an `=` is code; eight words or more is a sentence."""
    assert ordering_holds(["x = 1", "The loop runs first"])[0].startswith("ordering: ")
    assert ordering_holds(["we set the total = sum of amounts", "The loop runs first"]) == []


def test_a_cloze_hold_quotes_the_blank_cut_at_sixty_characters() -> None:
    """Both cloze holds show at most sixty characters of the blank."""
    prompt_blank = ">>> " + "a" * 70
    long_blank = " ".join(["word"] * 20)
    assert cloze_holds([prompt_blank]) == [
        f"cloze: the blank {('>>> ' + 'a' * 56)!r} starts with a prompt character -"
        " a blank is a term or a code token without the prompt"
    ]
    assert cloze_holds([long_blank]) == [
        f"cloze: the blank {('word ' * 12)!r} is 20 words - a blank is a term or a code"
        " token of at most 3 words, taken verbatim from a taught passage"
    ]


def test_a_choice_whose_correct_options_are_short_and_whose_wrong_one_is_copied_is_a_giveaway() -> (
    None
):
    """A three-word answer is no passage line, so a copied wrong option alone marks the key."""
    split = {
        "answer": "Type after the prompt.",
        "options": [
            {"text": "Type after prompt", "correct": True},
            {"text": HASH_TAUGHT[1][:140], "correct": False},
        ],
    }
    holds = exercise_holds(split, "choice", HASH_TAUGHT)
    assert len(holds) == 1 and holds[0].startswith("giveaway: only the wrong options")


def test_scenario_items_are_split_by_their_tier_into_right_and_wrong_for_the_giveaway() -> None:
    """Tiers `correct`, `right`, `true` are the key; any other tier is a wrong item."""
    scenario = {
        "answer": "",
        "items": [
            {"text": HASH_TAUGHT[0][:130], "tier": "Correct"},
            {"text": "Retype the output lines too", "tier": "wrong"},
        ],
    }
    holds = exercise_holds(scenario, "scenario_practice", HASH_TAUGHT)
    assert len(holds) == 1 and holds[0].startswith("giveaway: only the correct options")


def test_scenario_practice_key_is_its_answer_or_else_its_correct_items_only() -> None:
    """A quoted answer is the whole key; without one the correct-tier items are, never the wrong."""
    items = [
        {"text": "Type after the prompt", "tier": "correct"},
        {"text": "Retype the output lines", "tier": "wrong"},
    ]
    assert answer_key_claims({"answer": "", "items": items}, "scenario_practice") == [
        "Type after the prompt"
    ]
    assert answer_key_claims(
        {"answer": "Type after the prompt.", "items": items}, "scenario_practice"
    ) == ["Type after the prompt."]
    assert answer_key_claims({"answer": "", "items": items}, "reading") == []


def _stored(
    node: str, role: str, kind: str, content: dict[str, Any], key: dict[str, Any]
) -> dict[str, Any]:
    return {
        "id": node,
        "node": node,
        "role": role,
        "block_type": kind,
        "status": "accepted",
        "hold_reason": "",
        "version_id": f"v-{node}",
        "content": content,
        "answer_key": key,
        "topic": "t1",
        "topic_kind": "lesson",
        "topic_title": "Run examples",
        "objectives": [],
        "plan_content": {
            "source_handles": ["P1", "P2"],
            "source_passages": {"P1": "pid1", "P2": "pid2"},
        },
        "citations": [("pid1", HASH_TAUGHT[0][:60]), ("pid2", HASH_TAUGHT[1][:60])],
    }


def _replayed(blocks: list[dict[str, Any]]) -> dict[str, list[str]]:
    numbered = [
        {"passage_id": f"pid{i}", "handle": f"P{i}", "text": text}
        for i, text in enumerate(HASH_TAUGHT, 1)
    ]
    return {r["node"]: r["holds"] for r in replay_blocks(blocks, numbered)}


def test_replay_runs_the_narration_check_on_teaching_blocks_only() -> None:
    """A reading or example naming its passages is held; a diagram or a question is not."""
    narrating = "The limit is not given in the passages, so a stand-in is used."
    rows = _replayed(
        [
            _stored("t1-b1", "teach", "reading", {"body": narrating}, {}),
            _stored("t1-b2", "example", "reading", {"body": narrating}, {}),
            _stored("t1-b3", "teach", "diagram", {"caption": narrating}, {}),
            _stored("t1-b4", "check", "choice", {"stem": narrating, "options": ["a", "b"]}, {}),
        ]
    )
    narration = {
        node: [h for h in holds if h.startswith("narration: ")] for node, holds in rows.items()
    }
    assert len(narration["t1-b1"]) == 1 and len(narration["t1-b2"]) == 1
    assert narration["t1-b3"] == [] and narration["t1-b4"] == []


def test_replay_compiles_python_in_every_block_but_a_cloze() -> None:
    """A cloze text holds blanks, not a program to type: the compile check skips it."""
    broken = "Type:\n```python\nx = (1\n```"
    rows = _replayed(
        [
            _stored("t1-b1", "teach", "reading", {"body": broken}, {}),
            _stored("t1-b2", "check", "cloze", {"text": broken}, {"blanks": ["x"]}),
        ]
    )
    assert [h for h in rows["t1-b1"] if h.startswith("code: ")] != []
    assert [h for h in rows["t1-b2"] if h.startswith("code: ")] == []


def test_a_bare_prompt_transcript_is_compiled_and_the_error_window_is_two_hundred_characters() -> (
    None
):
    """An unfenced `>>>` session is a snippet; a SyntaxError named within 200 characters excuses."""
    bare = "Try this:\n\n>>> for i in range(3)\n... print(i)\n"
    assert len(python_code_holds(bare)) == 1
    fenced = "```python\nx = (1\n```"
    inside = fenced + " " * 189 + "SyntaxError"
    outside = fenced + " " * 190 + "SyntaxError"
    assert python_code_holds(inside) == []
    assert len(python_code_holds(outside)) == 1


def test_a_valid_prompt_transcript_keeps_its_continuation_lines_and_is_not_held() -> None:
    """`...` lines belong to the statement above them: without them `for` has no body."""
    text = "Loop:\n\n>>> for i in range(3):\n...     print(i)\n...\n0\n1\n2\n"
    assert python_code_holds(text) == []
    fenced = "```pycon\n>>> if True:\n...     pass\n...\n```"
    assert python_code_holds(fenced) == []


def test_a_prose_line_that_starts_with_an_ellipsis_is_not_a_session() -> None:
    """Only `>>>` opens a session; a sentence continued as `... and so on` is prose."""
    assert python_code_holds("The list goes on\n... and so on until the end\nDone.") == []


def test_a_transcript_after_a_fence_is_compiled_at_its_own_offset() -> None:
    """Fences are skipped by character offset; the bare session after one is still checked."""
    text = "Type:\n```python\nx = 1\n```\n\n>>> y = (1\n"
    holds = python_code_holds(text)
    assert len(holds) == 1 and "'y = (1'" in holds[0]


def test_a_fenced_block_in_another_language_that_shows_a_prompt_is_not_python() -> None:
    """A text or ruby fence is covered by its offsets and skipped by its language, not read bare."""
    assert python_code_holds("```text\n>>> x = (1\n```") == []
    assert python_code_holds("```ruby\n>>> puts(\n```") == []


def test_a_transcript_continuation_line_keeps_its_own_first_character() -> None:
    """`... else:` is the statement `else:`: the four-character prompt is all that is dropped."""
    text = "```pycon\n>>> if x:\n...     pass\n... else:\n...     pass\n...\n```"
    assert python_code_holds(text.replace("if x:", "if True:")) == []


def test_no_hold_line_of_a_code_check_carries_the_line_separator() -> None:
    """Hold lines are re-split on "; ": a line holding one leaves a fragment recheck keeps."""
    lines = [
        *narration_holds("The values are not given in the passages.", []),
        *cloze_holds(["one two three four", ">>> x = 1"]),
        *matching_holds([{"left": "a", "right": "b"}]),
        *ordering_holds(["a, b = 0, 1", "The body of the loop is indented"]),
        *exercise_holds(GUESSABLE_8997, "choice", HASH_TAUGHT),
        *exercise_holds(
            {
                "answer": "Type after the prompt.",
                "options": [
                    {"text": "Type after prompt", "correct": True},
                    {"text": HASH_TAUGHT[1][:140], "correct": False},
                ],
            },
            "choice",
            HASH_TAUGHT,
        ),
        *python_code_holds(PLACEHOLDER_CODE),
        *escape_holds("The character \\u00e9 is written out"),
        *number_holds("A total of 85 and 7731 held", [LIMIT_PASSAGE]),
    ]
    assert len(lines) == 10
    assert [line for line in lines if "; " in line] == []


def test_a_stored_reason_built_from_every_code_hold_prefix_splits_back_into_its_lines() -> None:
    """Every CODE_HOLD_PREFIXES prefix, with a quoted run holding "; ", survives the join."""
    from app.course.replay import CODE_HOLD_PREFIXES, is_code_hold, join_hold_lines

    quoted = "x = 1; y = 2"
    lines = [f"{prefix}{quoted!r} and more" for prefix in CODE_HOLD_PREFIXES]
    reader = "b2: the note (evidence: a; b)"
    stored = join_hold_lines([*lines, reader])
    pieces = stored.split("; ")
    assert [p for p in pieces if is_code_hold(p)] == [line.replace("; ", ", ") for line in lines]
    assert [p for p in pieces if not is_code_hold(p)] == ["b2: the note (evidence: a, b)"]


def test_a_short_option_that_is_a_passage_line_counts_as_copied_so_it_splits_nothing() -> None:
    """m2-test-b1: the right option `break` and the wrong ones are all lines of the passages."""
    taught = ["Use break to leave a loop early.\n\nfor n in range(2, 10):\n\nwhile n > 1: print(n)"]
    options = {
        "answer": "break leaves the loop",
        "options": [
            {"text": "break", "correct": True},
            {"text": "for n in range(2, 10):", "correct": False},
            {"text": "while n > 1: print(n)", "correct": False},
        ],
    }
    assert exercise_holds(options, "choice", taught) == []


def test_python_code_that_overflows_the_compiler_is_not_a_hold_and_never_crashes() -> None:
    """A deep expression raises RecursionError or MemoryError from compile(), not SyntaxError."""
    recursion = "```python\nx = " + "+".join(["1"] * 100000) + "\n```"
    memory = "```python\nx = " + "-" * 100000 + "1\n```"
    assert python_code_holds(recursion) == []
    assert python_code_holds(memory) == []


META_DOC_PASSAGE = (
    "The request carries the brief you want answered. The model reads each passage you attach "
    "and returns one answer per passage; a prompt can stand in for a system message."
)


def test_a_narration_word_the_cited_passages_use_is_course_vocabulary_not_narration() -> None:
    """The Meta API docs say "the brief" 34 times, "passage" 20: teaching them is not narrating."""
    teaching = (
        "Send the brief with the request; the model answers once for each passage you attach."
    )
    assert narration_holds(teaching, [META_DOC_PASSAGE]) == []
    assert len(narration_holds(teaching, ["A different page about error codes."])) == 1


def test_only_the_narration_words_missing_from_the_cited_passages_are_reported() -> None:
    """`the brief` is in the passage, `not given in` is not: the line names the second alone."""
    text = "Use the brief as shown; the limit is not given in the material."
    held = narration_holds(text, [META_DOC_PASSAGE])
    assert len(held) == 1 and "'not given in'" in held[0] and "'the brief'" not in held[0]


def test_replay_reads_the_narration_words_of_the_blocks_own_cited_passage_as_its_vocabulary() -> (
    None
):
    """A stored example saying "the brief" is held unless the passage it cites says it too."""
    numbered = [
        {"passage_id": "pid1", "handle": "P1", "text": META_DOC_PASSAGE},
        {"passage_id": "pid2", "handle": "P2", "text": "Errors carry a code and a message."},
    ]

    def example(node: str, cited: str) -> Any:
        return {
            "id": node,
            "node": node,
            "role": "example",
            "block_type": "reading",
            "status": "accepted",
            "hold_reason": "",
            "version_id": f"v-{node}",
            "content": {"body": "Send the brief with each request."},
            "answer_key": {},
            "topic": "t1",
            "topic_kind": "lesson",
            "topic_title": "Requests",
            "objectives": [],
            "plan_content": {"source_handles": ["P1", "P2"]},
            "citations": [(cited, "")],
        }

    rows = {
        r["node"]: r["holds"]
        for r in replay_blocks([example("own", "pid1"), example("other", "pid2")], numbered)
    }
    assert rows["own"] == []
    assert len(rows["other"]) == 1 and rows["other"][0].startswith("narration: ")


SIGN_PASSAGE = (
    "A branch tests a value:\n\n```python\nif x < 0:\n    print('Negative')\n"
    "elif x == 0:\n    print('Zero')\nelif x == 1:\n    print('Single')\n"
    "else:\n    print('More')\n```"
)
LIMIT_PASSAGE = "The loop stops after 3 tries, and 2.5 seconds pass between tries."


def test_a_number_no_cited_passage_shows_is_held_and_named() -> None:
    """The story's total 85 and limit 50 are in none of the passages; 0 and 1 are in the code."""
    held = number_holds(
        "With a total of 85 and a limit of 50 held in x, the check x == 0 is False.\n"
        "```python\nif x == 1:\n    print('Single')\n```",
        [SIGN_PASSAGE],
    )
    assert len(held) == 1 and held[0].startswith("number: ")
    assert "'85'" in held[0] and "'50'" in held[0]
    assert "'0'" not in held[0] and "'1'" not in held[0]
    assert "; " not in held[0]


def test_numbers_the_passages_show_are_not_held_and_digits_inside_words_are_not_numbers() -> None:
    """3 and 2.5 are the passage's; `x2`, `v3.1` and `2nd` are names and ordinals, not values."""
    text = "Retry 3 times, wait 2.5 seconds; the x2 variable, version v3.1 and the 2nd try."
    assert number_holds(text, [LIMIT_PASSAGE]) == []
    assert len(number_holds("Wait 25 seconds.", [LIMIT_PASSAGE])) == 1


def _topic_block(node: str, role: str, body: str, cited: str) -> dict[str, Any]:
    block = _stored(node, role, "reading", {"body": body}, {})
    block["plan_content"] = {"source_handles": [cited]}
    block["citations"] = [({"P1": "pid1", "P2": "pid2"}[cited], "")]
    return block


def _number_lines(blocks: list[dict[str, Any]]) -> dict[str, list[str]]:
    numbered = [
        {"passage_id": "pid1", "handle": "P1", "text": SIGN_PASSAGE},
        {"passage_id": "pid2", "handle": "P2", "text": LIMIT_PASSAGE},
    ]
    return {
        r["node"]: [h for h in r["holds"] if h.startswith("number: ")]
        for r in replay_blocks(blocks, numbered)
    }


def test_replay_reads_a_number_against_every_passage_the_topic_cites_not_only_the_blocks_own() -> (
    None
):
    """An example citing P1 may use 3 from the sibling reading's P2, but not 85 from nowhere."""
    lines = _number_lines(
        [
            _topic_block("t1-b1", "teach", "Retry 3 times.", "P2"),
            _topic_block("t1-b2", "example", "Here x == 1 and the retry count 3 is shown.", "P1"),
            _topic_block("t1-b3", "example", "A total of 85 sits in x.", "P1"),
        ]
    )
    assert lines["t1-b1"] == [] and lines["t1-b2"] == []
    assert len(lines["t1-b3"]) == 1 and "'85'" in lines["t1-b3"][0]


def test_a_module_test_is_judged_against_its_own_module_not_the_whole_course() -> None:
    """65536 is module 1's number: a module-2 test using it is held, while 3 (module 2's) is not."""
    numbered = [
        {"passage_id": "pid1", "handle": "P1", "text": "The buffer holds 65536 bytes."},
        {"passage_id": "pid2", "handle": "P2", "text": "The loop stops after 3 tries."},
    ]

    def block(node: str, module: str, topic: str, kind: str, role: str, body: str, cited: str):
        item = _topic_block(node, role, body, cited)
        item.update({"module": module, "topic": topic, "topic_kind": kind})
        return item

    blocks = [
        block("m1-t1-b1", "m1", "m1-t1", "lesson", "teach", "The buffer holds 65536.", "P1"),
        block("m2-t1-b1", "m2", "m2-t1", "lesson", "teach", "The loop stops after 3.", "P2"),
        block("m2-test-b1", "m2", "m2-test", "module_test", "test", "The limit is 65536?", "P2"),
        block("m2-test-b2", "m2", "m2-test", "module_test", "test", "It stops after 3?", "P2"),
    ]
    lines = {
        r["node"]: [h for h in r["holds"] if h.startswith("number: ")]
        for r in replay_blocks(blocks, numbered)
    }
    assert len(lines["m2-test-b1"]) == 1 and "'65536'" in lines["m2-test-b1"][0]
    assert lines["m2-test-b2"] == [] and lines["m1-t1-b1"] == []
