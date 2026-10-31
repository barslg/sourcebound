"""Meta strict-subset validator: every agent schema must pass at startup."""

import pytest

from app.llm.schema import SchemaViolation, validate_strict_subset


def _valid() -> dict:  # type: ignore[no-untyped-def]
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "count": {"type": "integer"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "meta": {
                "type": "object",
                "properties": {"note": {"type": "string"}},
                "required": ["note"],
                "additionalProperties": False,
            },
        },
        "required": ["title", "count", "tags", "meta"],
        "additionalProperties": False,
    }


def test_valid_schema_passes() -> None:
    assert validate_strict_subset(_valid()) is None


def test_root_must_be_a_plain_object() -> None:
    with pytest.raises(SchemaViolation, match="root"):
        validate_strict_subset({"type": "array", "items": {"type": "string"}})


def test_root_may_not_use_anyof() -> None:
    schema = _valid()
    schema["anyOf"] = [{"type": "object"}]
    with pytest.raises(SchemaViolation, match="anyOf"):
        validate_strict_subset(schema)


def test_missing_additional_properties_false() -> None:
    schema = _valid()
    del schema["additionalProperties"]
    with pytest.raises(SchemaViolation, match="additionalProperties"):
        validate_strict_subset(schema)


def test_required_must_list_every_property() -> None:
    schema = _valid()
    schema["required"] = ["title"]
    with pytest.raises(SchemaViolation, match="required"):
        validate_strict_subset(schema)


def test_nested_violation_names_its_path() -> None:
    schema = _valid()
    del schema["properties"]["meta"]["required"]
    with pytest.raises(SchemaViolation, match="meta"):
        validate_strict_subset(schema)


def test_allof_oneof_rejected_anywhere() -> None:
    schema = _valid()
    schema["properties"]["title"] = {"allOf": [{"type": "string"}]}
    with pytest.raises(SchemaViolation, match="allOf"):
        validate_strict_subset(schema)


def test_ref_rejected() -> None:
    schema = _valid()
    schema["properties"]["title"] = {"$ref": "#/properties/count"}
    with pytest.raises(SchemaViolation, match="ref"):
        validate_strict_subset(schema)


def test_anyof_allowed_below_root() -> None:
    schema = _valid()
    schema["properties"]["title"] = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    assert validate_strict_subset(schema) is None


def _deep(levels: int) -> dict:  # type: ignore[no-untyped-def]
    node: dict = {"type": "string"}
    for _ in range(levels):
        node = {
            "type": "object",
            "properties": {"child": node},
            "required": ["child"],
            "additionalProperties": False,
        }
    return node


def test_depth_limit() -> None:
    assert validate_strict_subset(_deep(9)) is None
    with pytest.raises(SchemaViolation, match="depth"):
        validate_strict_subset(_deep(10))


def test_null_is_allowed_as_a_scalar_type() -> None:
    schema = _valid()
    schema["properties"]["count"] = {"type": "null"}
    assert validate_strict_subset(schema) is None


def test_nullable_anyof_branch_is_allowed() -> None:
    schema = _valid()
    schema["properties"]["count"] = {"anyOf": [{"type": "integer"}, {"type": "null"}]}
    assert validate_strict_subset(schema) is None


def test_list_valued_type_is_rejected_as_violation() -> None:
    schema = _valid()
    schema["properties"]["title"] = {"type": ["string", "null"]}
    with pytest.raises(SchemaViolation, match="type"):
        validate_strict_subset(schema)


def test_anyof_node_must_not_carry_sibling_keys() -> None:
    schema = _valid()
    schema["properties"]["title"] = {
        "anyOf": [{"type": "string"}, {"type": "integer"}],
        "additionalProperties": True,
    }
    with pytest.raises(SchemaViolation, match="[Ss]ibling"):
        validate_strict_subset(schema)


def test_anyof_node_must_not_carry_a_type_sibling() -> None:
    schema = _valid()
    schema["properties"]["title"] = {
        "type": "object",
        "anyOf": [{"type": "string"}, {"type": "integer"}],
    }
    with pytest.raises(SchemaViolation, match="[Ss]ibling"):
        validate_strict_subset(schema)


def test_required_duplicates_are_rejected() -> None:
    schema = _valid()
    schema["required"] = ["title", "title", "count", "tags", "meta"]
    with pytest.raises(SchemaViolation, match="required"):
        validate_strict_subset(schema)


def test_required_extra_names_are_rejected() -> None:
    schema = _valid()
    schema["required"] = ["title", "count", "tags", "meta", "nope"]
    with pytest.raises(SchemaViolation, match="required"):
        validate_strict_subset(schema)


def test_oneof_rejected_below_root() -> None:
    schema = _valid()
    schema["properties"]["title"] = {"oneOf": [{"type": "string"}]}
    with pytest.raises(SchemaViolation, match="oneOf"):
        validate_strict_subset(schema)


def test_root_ref_is_rejected() -> None:
    schema = _valid()
    schema["$ref"] = "#/properties/title"
    with pytest.raises(SchemaViolation, match="ref"):
        validate_strict_subset(schema)


def test_bad_enums_are_rejected() -> None:
    for bad in ([], "red", [{"name": "red"}], [None]):
        schema = _valid()
        schema["properties"]["title"] = {"type": "string", "enum": bad}
        with pytest.raises(SchemaViolation, match="enum"):
            validate_strict_subset(schema)


def test_good_enum_passes() -> None:
    schema = _valid()
    schema["properties"]["title"] = {"type": "string", "enum": ["red", "green"]}
    assert validate_strict_subset(schema) is None
