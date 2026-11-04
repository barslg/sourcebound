"""Code checks over the learner-facing text of a generated block.

Called by the runs executor (`generate_block_step.judge`) on every writer
draft and by `course.replay` over stored blocks. A returned line is a Fix
list line the writer can act on; the prefixes are listed in
`course.replay.CODE_HOLD_PREFIXES` so a recheck recomputes them.
"""

from __future__ import annotations

import re
from typing import Any

NARRATION_RE = re.compile(
    r"\bpassages?\b|\bthe brief\b|example_spec|\bcase input\b|\bstand[- ]in\b"
    r"|\bnot given in\b|\bnot invented\b"
    r"|\binvented (?:value|price|number|amount|name|data|figure)s?\b",
    re.IGNORECASE,
)
_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")
_FENCE_RE = re.compile(r"```[ \t]*([\w+-]*)[^\n]*\n(.*?)```", re.DOTALL)
_PY_FENCE_LANGS = frozenset({"python", "py", "python3", "pycon"})
_ERROR_SHOWN_RE = re.compile(r"SyntaxError|IndentationError|TabError|invalid syntax")
_AFTER_SNIPPET = 200


def narration_holds(text: str) -> list[str]:
    """Hold a reading that talks about its own sourcing instead of teaching.

    A learner never saw "the passages", "the brief" or "example_spec": a
    sentence that names them narrates the pipeline (22 of 29 examples of
    the 2026-11-04 Python course). The check establishes that none of the
    pipeline's words reaches the learner; whether the teaching is right is
    the reader's call.
    """
    found = list(dict.fromkeys(m.group(0).lower() for m in NARRATION_RE.finditer(text)))
    if not found:
        return []
    return [
        "narration: the text mentions its own sourcing ("
        + ", ".join(repr(w) for w in found[:4])
        + ") - say what the value is, never where the writer got it"
    ]


_CODE_SPAN_RE = re.compile(r"```.*?```|`[^`\n]*`|^>>> .*$", re.DOTALL | re.MULTILINE)


def _map_prose(text: str, change: Any) -> str:
    """`change` applied to the text between code spans; code spans stay as written."""
    out: list[str] = []
    at = 0
    for span in _CODE_SPAN_RE.finditer(text):
        out.append(change(text[at : span.start()]))
        out.append(span.group(0))
        at = span.end()
    out.append(change(text[at:]))
    return "".join(out)


def decode_escapes(text: str) -> str:
    """Literal `\\uXXXX` in prose replaced by its character; code spans keep their escapes.

    A tutorial that teaches `"\\u00e9"` must keep it: only the prose between
    code spans is decoded.
    """
    return _map_prose(text, lambda part: _ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), part))


def decode_escapes_in(value: Any) -> Any:
    """`decode_escapes` over every string of a nested reply, other values untouched."""
    if isinstance(value, str):
        return decode_escapes(value)
    if isinstance(value, list):
        return [decode_escapes_in(item) for item in value]
    if isinstance(value, dict):
        return {key: decode_escapes_in(item) for key, item in value.items()}
    return value


def escape_holds(text: str) -> list[str]:
    """Hold a stored text whose prose carries a literal `\\uXXXX` (new drafts are repaired)."""
    if decode_escapes(text) != text:
        return ["escape: the text carries a literal \\uXXXX sequence - write the character itself"]
    return []


def _transcript_source(lines: list[str]) -> tuple[str, str]:
    """(source, output) of an interactive session: prompts stripped, output lines apart."""
    source: list[str] = []
    output: list[str] = []
    for line in lines:
        if line.startswith(">>> ") or line == ">>>":
            source.append(line[4:])
        elif line.startswith("... ") or line == "...":
            source.append(line[4:])
        else:
            output.append(line)
    return "\n".join(source), "\n".join(output)


def _snippets(text: str) -> list[tuple[str, str, int]]:
    """(source, own output, end offset) of every Python fence and every `>>>` transcript."""
    found: list[tuple[str, str, int]] = []
    covered: list[tuple[int, int]] = []
    for match in _FENCE_RE.finditer(text):
        covered.append(match.span())
        lang = match.group(1).lower()
        lines = match.group(2).splitlines()
        has_prompts = any(line.startswith(">>>") for line in lines)
        if lang not in _PY_FENCE_LANGS and not (lang == "" and has_prompts):
            continue
        if has_prompts:
            source, out = _transcript_source(lines)
            found.append((source, out, match.end()))
        else:
            found.append((match.group(2), "", match.end()))
    offset = 0
    session: list[str] = []
    session_end = 0
    for raw in text.splitlines(keepends=True):
        start, offset = offset, offset + len(raw)
        line = raw.rstrip("\n")
        if any(lo <= start < hi for lo, hi in covered):
            continue
        if line.startswith(">>>") or (session and line.startswith("...")):
            session.append(line)
            session_end = offset
        elif session and line.strip():
            session.append(line)
            session_end = offset
        elif session:
            source, out = _transcript_source(session)
            found.append((source, out, session_end))
            session = []
    if session:
        source, out = _transcript_source(session)
        found.append((source, out, session_end))
    return found


def python_code_holds(text: str) -> list[str]:
    """Hold a block whose Python snippet is not valid Python (compile only, never run).

    Every ```python fence and every `>>>` transcript is compiled after the
    prompts and the output lines are stripped. A snippet that is shown as
    an error on purpose (its output, or the 200 characters after it, name
    a SyntaxError) is the lesson, not a defect. The check establishes that
    the code a learner is told to type parses; what it prints is not
    checked.
    """
    holds: list[str] = []
    for source, out, end in _snippets(text):
        if not source.strip():
            continue
        if _ERROR_SHOWN_RE.search(out) or _ERROR_SHOWN_RE.search(
            source + text[end : end + _AFTER_SNIPPET]
        ):
            continue
        try:
            compile(source, "<block>", "exec")
        except (MemoryError, RecursionError):
            continue  # the compiler gave up on the size of the snippet: no verdict, no hold
        except SyntaxError as error:
            lines = source.splitlines()
            at = error.lineno or 1
            shown = lines[at - 1].strip() if 0 < at <= len(lines) else ""
            holds.append(
                f"code: a Python snippet does not compile ({error.msg}) at line {at}: "
                f"{shown[:80]!r} - show code a learner can type as written"
            )
    return list(dict.fromkeys(holds))
