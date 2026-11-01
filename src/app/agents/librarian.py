"""Librarian: the model writes a DECLARATIVE parser spec; code runs it.

No generated code is ever executed: validate_parser_spec() accepts
only container/field selectors, one address rule, and one pagination
rule, and execute_parser_spec() interprets the spec with the stdlib
HTML parser. The spec is stored with the source so re-crawls reuse it.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from pydantic import BaseModel, Field
from pydantic import ValidationError as PydanticValidationError

_SELECTOR_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9]*(?:[.#][a-zA-Z0-9_-]+)?$")
_MAX_PAGES = 20


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
    """Minimal interpreter for declarative specs over one HTML page."""

    def __init__(self, spec: ParserSpec) -> None:
        super().__init__()
        self._spec = spec
        self._chain: list[tuple[str, dict[str, str]]] = []
        self._records: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._container_depth: int | None = None
        self._field: str | None = None
        self._heading: list[str] = []
        self._heading_text = ""
        self._in_heading = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        self._chain.append((tag, attr))
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._in_heading = True
            self._heading_text = ""
        if self._current is None and _matches(self._chain, self._spec.container):
            self._current = {"title": "", "body": "", "anchor": attr.get("id", "")}
            self._container_depth = len(self._chain)
            return
        if self._current is not None:
            for name, selector in self._spec.fields.items():
                if _matches(self._chain, selector):
                    self._field = name

    def handle_endtag(self, tag: str) -> None:
        if self._in_heading and tag.startswith("h"):
            text = self._heading_text.strip()
            if text:
                self._heading.append(text)
            self._in_heading = False
        if (
            self._current is not None
            and self._container_depth is not None
            and len(self._chain) == self._container_depth
        ):
            record = dict(self._current)
            rule = self._spec.address_rule
            if rule.kind == "heading_path":
                record["address"] = ">".join(self._heading[-3:]) or "body"
            else:
                record["address"] = record.get("anchor") or "body"
            self._records.append(record)
            self._current = None
            self._container_depth = None
        if self._chain:
            self._chain.pop()
        self._field = None

    def handle_data(self, data: str) -> None:
        if self._in_heading:
            self._heading_text += data
        if self._current is not None and self._field:
            self._current[self._field] += data


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
    """Run a validated spec over one page; raise on invalid specs."""
    errors = validate_parser_spec(spec)
    if errors:
        raise ValueError(f"invalid parser spec: {errors[0]}")
    parser = _SpecParser(ParserSpec.model_validate(spec))
    parser.feed(html)
    return [
        {
            "title": record["title"].strip(),
            "body": record["body"].strip(),
            "address": record["address"],
        }
        for record in parser._records
        if record["body"].strip()
    ]


def derive_parser_spec(html: str) -> dict[str, Any] | None:
    """Deterministic first-intake spec from page structure (stored + reused).

    Model-written specs (build_prompt) still win when supplied or
    stored; this covers the first crawl so a spec is always written.
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
