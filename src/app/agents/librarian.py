"""Librarian: the model writes a DECLARATIVE parser spec; code runs it.

No generated code is ever executed: validate_parser_spec() accepts
only container/field selectors, one address rule, and one pagination
rule, and execute_parser_spec() interprets the spec with the stdlib
HTML parser. The spec is stored with the source so re-crawls reuse it.

A page is read block by block: every paragraph, list, code block or
cell inside the container becomes one record with the heading path it
sits under, so two neighbouring elements never glue into one string
and a list keeps its items on their own lines (the intro sentence and
its items stay one claim for the generators).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from pydantic import BaseModel, Field
from pydantic import ValidationError as PydanticValidationError

_SELECTOR_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9]*(?:[.#][a-zA-Z0-9_-]+)?$")
_MAX_PAGES = 20
_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_SKIP_TAGS = frozenset(
    {"script", "style", "noscript", "svg", "template", "nav", "header", "footer", "aside", "button"}
)
_BLOCK_TAGS = frozenset(
    {"p", "li", "pre", "blockquote", "dd", "dt", "td", "th", "figcaption", "summary"}
)
_LIST_TAGS = frozenset({"ul", "ol"})
_VOID_TAGS = frozenset({"br", "hr", "img", "input", "meta", "link", "wbr", "source", "area"})


class AddressRule(BaseModel):
    kind: str = Field(pattern="^(heading_path|anchor_id)$")
    selector: str = Field(min_length=1)


class PaginationRule(BaseModel):
    next_selector: str | None = None
    max_pages: int = Field(ge=1, le=_MAX_PAGES)


class ParserSpec(BaseModel):
    container: str = Field(min_length=1)
    fields: dict[str, str]
    address_rule: AddressRule
    pagination: PaginationRule


def validate_parser_spec(spec: dict[str, Any]) -> list[str]:
    """Check a parser spec in code; return error strings (empty = valid)."""
    try:
        parsed = ParserSpec.model_validate(spec)
    except PydanticValidationError as exc:
        return [f"schema: {error['loc']}: {error['msg']}" for error in exc.errors()]
    errors: list[str] = []
    selectors = [parsed.container, *parsed.fields.values(), parsed.address_rule.selector]
    if parsed.pagination.next_selector:
        selectors.append(parsed.pagination.next_selector)
    for selector in selectors:
        for part in selector.split():
            if not _SELECTOR_RE.match(part):
                errors.append(f"selector {selector!r}: only tag, tag.class, tag#id chains")
    if set(parsed.fields) != {"title", "body"}:
        errors.append("fields must be exactly {title, body}")
    return errors


class _SpecParser(HTMLParser):
    """Block-by-block interpreter for declarative specs over one HTML page.

    Inside the container every block element (the body selector plus
    p/li/pre/blockquote/dd/dt/td/th/figcaption/summary) collects its own
    text, inline tags included; a list (ul/ol) is one record whose items
    are bullet lines; script/style/nav/header/footer/aside subtrees are
    never read. Each record carries the heading path (or the nearest
    ancestor id) current when its block closes.
    """

    def __init__(self, spec: ParserSpec) -> None:
        super().__init__(convert_charrefs=True)
        self._spec = spec
        self._chain: list[tuple[str, dict[str, str]]] = []
        self._records: list[dict[str, str]] = []
        self._container_depth: int | None = None
        self._skip_depth: int | None = None
        self._blocks: list[tuple[int, str, list[str]]] = []
        self._lists: list[tuple[int, str, list[str]]] = []
        self._heading: list[tuple[int, str]] = []
        self._heading_text: list[str] = []
        self._heading_depth: int | None = None
        self._title = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID_TAGS:
            if tag == "br" and self._blocks:
                self._blocks[-1][2].append("\n")
            return
        attr = {key: value or "" for key, value in attrs}
        self._chain.append((tag, attr))
        depth = len(self._chain)
        if self._skip_depth is not None:
            return
        if tag in _SKIP_TAGS:
            self._skip_depth = depth
            return
        if tag in _HEADINGS:
            self._heading_text = []
            self._heading_depth = depth
            return
        if self._container_depth is None:
            if _matches_here(self._chain, self._spec.container):
                self._container_depth = depth
            return
        if tag == "p" and self._blocks and self._blocks[-1][1] == "p":
            # <p> never closed before the next <p>: the first one ends here
            self._chain.pop()
            self._flush_deeper(self._blocks[-1][0] - 1)
            self._chain.append((tag, attr))
            depth = len(self._chain)
        if tag in _LIST_TAGS:
            self._lists.append((depth, tag, []))
            return
        if tag in _BLOCK_TAGS or _matches_here(self._chain, self._spec.fields["body"]):
            self._blocks.append((depth, tag, []))

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS or not self._chain:
            return
        depth = len(self._chain)
        if self._skip_depth is not None:
            if depth == self._skip_depth and self._chain[-1][0] == tag:
                self._skip_depth = None
                self._chain.pop()
            elif depth > self._skip_depth:
                self._chain.pop()
            return
        if self._heading_depth is not None and depth == self._heading_depth:
            self._close_heading(tag)
        else:
            self._flush_deeper(depth)
            if self._blocks and self._blocks[-1][1] == "pre" and self._line_break_in_pre(tag):
                # a code block renders its lines as child elements: keep them lines
                self._blocks[-1][2].append("\n")
        if self._container_depth is not None and depth == self._container_depth:
            self._container_depth = None
        self._chain.pop()

    def _line_break_in_pre(self, tag: str) -> bool:
        attrs = self._chain[-1][1] if self._chain else {}
        return tag in ("div", "code") or (tag == "span" and "data-line" in attrs)

    def _flush_deeper(self, depth: int) -> None:
        """Close every block and list opened at `depth` or deeper (unclosed tags included)."""
        while (self._blocks and self._blocks[-1][0] >= depth) or (
            self._lists and self._lists[-1][0] >= depth
        ):
            block_depth = self._blocks[-1][0] if self._blocks else -1
            list_depth = self._lists[-1][0] if self._lists else -1
            if block_depth >= list_depth:
                self._close_block()
            else:
                self._close_list()

    def handle_data(self, data: str) -> None:
        if self._skip_depth is not None:
            return
        if self._heading_depth is not None:
            self._heading_text.append(data)
            return
        if self._container_depth is not None and self._blocks:
            self._blocks[-1][2].append(data)

    def _close_heading(self, tag: str) -> None:
        text = " ".join("".join(self._heading_text).split())
        level = int(tag[1]) if len(tag) == 2 and tag[1].isdigit() else 6
        self._heading_depth = None
        if not text:
            return
        if not self._title:
            self._title = text
        self._heading = [(lvl, name) for lvl, name in self._heading if lvl < level]
        self._heading.append((level, text))

    def _close_block(self) -> None:
        _, tag, parts = self._blocks.pop()
        text = "".join(parts)
        if tag == "li" and self._lists:
            self._lists[-1][2].append(" ".join(text.split()))
            return
        self._emit(text)

    def _close_list(self) -> None:
        _, kind, items = self._lists.pop()
        lines = [item for item in items if item]
        if not lines:
            return
        text = "\n".join(
            f"{index}. {item}" if kind == "ol" else f"- {item}"
            for index, item in enumerate(lines, start=1)
        )
        if self._blocks:
            # a list inside a paragraph-like block: the block carries it as its own lines
            self._blocks[-1][2].append("\n" + text + "\n")
            return
        self._emit(text)

    def _emit(self, text: str) -> None:
        if not text.strip():
            return
        rule = self._spec.address_rule
        if rule.kind == "heading_path":
            address = ">".join(name for _, name in self._heading[-3:]) or "body"
        else:
            address = (
                next((attr["id"] for _, attr in reversed(self._chain) if attr.get("id")), "")
                or "body"
            )
        self._records.append(
            {
                "title": self._heading[-1][1] if self._heading else self._title,
                "body": text,
                "address": address,
            }
        )


def _matches_here(chain: list[tuple[str, dict[str, str]]], selector: str) -> bool:
    """True when the innermost element is the selector's last part and the rest matches above it.

    A descendant match alone would turn every inline tag inside a
    paragraph into its own block (the 2026-11 glue defect: `<code>`
    inside `<p>` cut the paragraph in two).
    """
    parts = selector.split()
    if not chain or not parts or not _part_matches(chain[-1], parts[-1]):
        return False
    return _matches(chain[:-1], " ".join(parts[:-1])) if len(parts) > 1 else True


def _matches(chain: list[tuple[str, dict[str, str]]], selector: str) -> bool:
    """True when the chain tail matches a descendant selector chain."""
    parts = selector.split()
    depth = len(chain) - 1
    for part in reversed(parts):
        while depth >= 0 and not _part_matches(chain[depth], part):
            depth -= 1
        if depth < 0:
            return False
        depth -= 1
    return True


def _part_matches(node: tuple[str, dict[str, str]], part: str) -> bool:
    tag, attrs = node
    if "#" in part:
        wanted_tag, wanted_id = part.split("#", 1)
        return tag == wanted_tag and attrs.get("id") == wanted_id
    if "." in part:
        wanted_tag, wanted_class = part.split(".", 1)
        classes = attrs.get("class", "").split()
        return tag == wanted_tag and wanted_class in classes
    return tag == part


def execute_parser_spec(html: str, spec: dict[str, Any]) -> list[dict[str, str]]:
    """Run a validated spec over one page; one record per block; raise on invalid specs."""
    errors = validate_parser_spec(spec)
    if errors:
        raise ValueError(f"invalid parser spec: {errors[0]}")
    parser = _SpecParser(ParserSpec.model_validate(spec))
    parser.feed(html)
    parser.close()
    return [
        {
            "title": record["title"].strip(),
            "body": record["body"].strip(),
            "address": record["address"],
        }
        for record in parser._records
        if record["body"].strip()
    ]


def body_spec() -> dict[str, Any]:
    """The spec of last resort: the whole document body, paragraph by paragraph."""
    return {
        "container": "body",
        "fields": {"title": "h1", "body": "p"},
        "address_rule": {"kind": "heading_path", "selector": "body"},
        "pagination": {"next_selector": None, "max_pages": 1},
    }


def derive_parser_spec(html: str) -> dict[str, Any] | None:
    """Deterministic first-intake spec from page structure (stored + reused).

    A stored spec still wins when supplied; this covers the first crawl
    so a spec is always written. `<article` or `<section` in the markup
    picks the container; the caller falls back to body_spec() when the
    derived container yields no block.
    """
    container = "article" if "<article" in html else "section" if "<section" in html else "body"
    heading = next((tag for tag in ("h1", "h2", "h3") if f"<{tag}" in html), "h2")
    spec: dict[str, Any] = {
        "container": container,
        "fields": {"title": heading, "body": "p"},
        "address_rule": {"kind": "heading_path", "selector": container},
        "pagination": {"next_selector": None, "max_pages": 1},
    }
    return spec if validate_parser_spec(spec) == [] else None
