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


def test_matching_with_two_pairs_is_held_and_four_pairs_pass() -> None:
    """a37a66c1 has two pairs (guessable by elimination); 6ffd6545 has four."""
    two = [
        {"left": "the stop entry that should end entry reading", "right": "breaks out of the loop"},
        {"left": "the blank entry that should be left unprocessed", "right": "continues"},
    ]
    four = [{"left": f"left {i}", "right": f"right {i}"} for i in range(4)]
    assert matching_holds(two)[0].startswith("matching: 2 pairs")
    assert matching_holds(four) == []


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
    held = narration_holds(narrating)
    assert len(held) == 1 and "'not given in'" in held[0] and "'passages'" in held[0]
    assert narration_holds(clean) == []


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
