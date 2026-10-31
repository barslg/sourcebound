"""Meta strict-subset validator for structured-output JSON schemas.

Every agent declares a JSON schema; at app start each one must satisfy
Meta's strict subset or the service refuses to boot:
root plain object, no allOf/oneOf anywhere, anyOf only below the root,
additionalProperties false on every object, required listing every
property, no $ref (hence no recursion), nesting depth at most 10.
`null` is a scalar (the only way to express an optional field, since
required must list every property).
"""

from __future__ import annotations

from typing import Any

MAX_DEPTH = 10
_SCALAR_TYPES = frozenset({"string", "integer", "number", "boolean", "null"})
_ANYOF_SIBLING_ALLOWLIST = frozenset({"anyOf", "description"})


class SchemaViolation(ValueError):
    """A schema breaks the Meta strict subset; message names the path."""


def validate_strict_subset(schema: object, name: str = "") -> None:
    """Validate one response schema; raise SchemaViolation on any breach."""
    label = name or "schema"
    if not isinstance(schema, dict):
        raise SchemaViolation(f"{label}: root must be a plain object schema")
    if schema.get("type") != "object":
        raise SchemaViolation(f"{label}: root must be type object, no wrappers")
    for banned in ("allOf", "oneOf", "anyOf", "$ref"):
        if banned in schema:
            raise SchemaViolation(f"{label}: root must not use {banned}")
    _check_object(schema, label, depth=0)


def _check_object(node: dict[str, Any], path: str, depth: int) -> None:
    if depth >= MAX_DEPTH:
        raise SchemaViolation(f"{path}: exceeds max nesting depth {MAX_DEPTH}")
    if node.get("type") != "object":
        raise SchemaViolation(f"{path}: object node must declare type object")
    if node.get("additionalProperties") is not False:
        raise SchemaViolation(f"{path}: additionalProperties must be false")
    properties = node.get("properties")
    if not isinstance(properties, dict) or not properties:
        raise SchemaViolation(f"{path}: object must declare non-empty properties")
    required = node.get("required")
    if (
        not isinstance(required, list)
        or any(not isinstance(item, str) for item in required)
        or sorted(required) != sorted(properties)
    ):
        raise SchemaViolation(f"{path}: required must list every property exactly once")
    for key, child in properties.items():
        _check_node(child, f"{path}.{key}", depth + 1)


def _check_node(node: Any, path: str, depth: int, allow_anyof: bool = True) -> None:
    if depth >= MAX_DEPTH:
        raise SchemaViolation(f"{path}: exceeds max nesting depth {MAX_DEPTH}")
    if not isinstance(node, dict):
        raise SchemaViolation(f"{path}: schema node must be an object")
    if "$ref" in node:
        raise SchemaViolation(f"{path}: $ref is not allowed (no recursion)")
    if "allOf" in node or "oneOf" in node:
        raise SchemaViolation(f"{path}: allOf/oneOf are not allowed")
    if "anyOf" in node:
        if not allow_anyof:
            raise SchemaViolation(f"{path}: nested anyOf is not allowed")
        siblings = set(node) - _ANYOF_SIBLING_ALLOWLIST
        if siblings:
            raise SchemaViolation(f"{path}: anyOf sibling keys are not allowed: {sorted(siblings)}")
        branches = node["anyOf"]
        if not isinstance(branches, list) or not branches:
            raise SchemaViolation(f"{path}: anyOf must list at least one branch")
        for index, branch in enumerate(branches):
            _check_node(branch, f"{path}.anyOf[{index}]", depth + 1, allow_anyof=False)
        return
    kind = node.get("type")
    if not isinstance(kind, str):
        raise SchemaViolation(f"{path}: type must be a single type name, got {kind!r}")
    if kind == "object":
        _check_object(node, path, depth)
    elif kind == "array":
        items = node.get("items")
        if not isinstance(items, dict):
            raise SchemaViolation(f"{path}: array must declare items")
        _check_node(items, f"{path}.items", depth + 1)
    elif kind in _SCALAR_TYPES:
        enum = node.get("enum")
        if enum is not None and (
            not isinstance(enum, list)
            or not enum
            or any(value is None or isinstance(value, dict | list) for value in enum)
        ):
            raise SchemaViolation(f"{path}: enum must list scalar values")
    else:
        raise SchemaViolation(f"{path}: unsupported type {kind!r}")
