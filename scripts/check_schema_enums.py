"""Vocabulary gate: every closed vocabulary the code validates is closed in the schemas too.

The registries own the vocabularies: block types (blocks.registry), roles and
topic kinds (course.tree), mutation / error kinds (course.errorspot), reader
verdicts and rubric items (agents.reader), bloom levels and methodology
prescriptions (course.methodologies), address kinds (agents.librarian). A
model reply is only as closed as its response schema, so this gate walks
every agent schema and every per-type generator schema and fails when:

- a field the table below binds to a vocabulary has no `enum`, or an `enum`
  that is not exactly the vocabulary (drift in either direction);
- an `enum` anywhere in a schema is bound to no vocabulary (a closed list
  that lives only in a schema literal);
- the reader schema's rubric items are not exactly RUBRIC_ITEMS;
- a code-side map (planting habits, block types per topic kind, methodology
  prescriptions) names a value outside its vocabulary or misses one;
- a vocabulary is bound but no schema emits it.

It then runs agents.base.validate_agents(), the same startup check the API
lifespan and the worker run, against the real prompts/ when every agent has
one and against the tracked stubs in tests/fixtures/agent_prompts
otherwise, so a fresh clone checks the same thing.

Usage: python scripts/check_schema_enums.py
Prints "check_schema_enums: N vocabulary bindings checked"; exit 1 per violation.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))


def vocabularies() -> dict[str, tuple[str, ...]]:
    """Vocabulary name -> its values, read from the registries."""
    from app.agents import reader
    from app.agents.librarian import AddressRule
    from app.blocks.registry import BLOCK_TYPES
    from app.course import tree
    from app.course.errorspot import MUTATION_KINDS
    from app.course.methodologies import BLOOM_LEVELS

    pattern = next(
        m.pattern for m in AddressRule.model_fields["kind"].metadata if hasattr(m, "pattern")
    )
    address = tuple(re.fullmatch(r"\^\((.*)\)\$", str(pattern)).group(1).split("|"))  # type: ignore[union-attr]
    return {
        "block_type": tuple(BLOCK_TYPES),
        "role": tuple(tree.ROLES),
        "topic_kind": tuple(tree.TOPIC_KINDS),
        "mutation_kind": tuple(MUTATION_KINDS),
        "verdict": tuple(reader.VERDICTS),
        "address_kind": address,
        "bloom_level": tuple(BLOOM_LEVELS),
    }


# (agent, field) -> vocabulary; agent None matches every schema.
FIELD_VOCAB: dict[tuple[str | None, str], str] = {
    (None, "block_type"): "block_type",
    (None, "role"): "role",
    (None, "verdict"): "verdict",
    ("architect", "kind"): "topic_kind",
    ("saboteur", "kind"): "mutation_kind",
    ("librarian", "kind"): "address_kind",
}


def walk(schema: Any, path: str = "$") -> list[tuple[str, str | None, dict[str, Any]]]:
    """(path, property name, node) for every schema node; the name is None off a property."""
    found: list[tuple[str, str | None, dict[str, Any]]] = []
    if isinstance(schema, dict):
        props = schema.get("properties")
        if isinstance(props, dict):
            for name, sub in props.items():
                if isinstance(sub, dict):
                    found.append((f"{path}.{name}", name, sub))
                found += walk(sub, f"{path}.{name}")
        for key in ("items", "anyOf", "oneOf", "allOf"):
            sub = schema.get(key)
            if isinstance(sub, dict):
                found += walk(sub, f"{path}[]")
            elif isinstance(sub, list):
                for item in sub:
                    found += walk(item, path)
    return found


def check_schemas(
    schemas: dict[str, dict[str, Any]], vocab: dict[str, tuple[str, ...]]
) -> tuple[int, set[str], list[str]]:
    """Return (bindings checked, vocabularies seen, violation lines)."""
    checked = 0
    seen: set[str] = set()
    problems: list[str] = []
    for agent, schema in sorted(schemas.items()):
        base = agent.split(":")[0]
        for path, name, node in walk(schema):
            vocab_name = FIELD_VOCAB.get((base, name or "")) or FIELD_VOCAB.get((None, name or ""))
            enum = node.get("enum")
            if vocab_name is None:
                if enum is not None:
                    problems.append(f"{agent} {path}: enum {enum} is bound to no vocabulary")
                continue
            checked += 1
            seen.add(vocab_name)
            if not isinstance(enum, list):
                problems.append(f"{agent} {path}: {vocab_name} field has no enum")
            elif sorted(enum) != sorted(vocab[vocab_name]) or len(set(enum)) != len(enum):
                problems.append(
                    f"{agent} {path}: enum {sorted(enum)} != {vocab_name}"
                    f" {sorted(vocab[vocab_name])}"
                )
    return checked, seen, problems


def check_rubric(schema: dict[str, Any]) -> list[str]:
    """The reader's rubric items are exactly RUBRIC_ITEMS, all required."""
    from app.agents.reader import RUBRIC_ITEMS

    nodes = [
        n
        for p, name, n in walk(schema)
        if name == "items" and "task_fit" in n.get("properties", {})
    ]
    if len(nodes) != 1:
        return ["reader: the rubric items object was not found in the schema"]
    node = nodes[0]
    problems = []
    if sorted(node["properties"]) != sorted(RUBRIC_ITEMS):
        problems.append(
            f"reader: rubric items {sorted(node['properties'])} != {sorted(RUBRIC_ITEMS)}"
        )
    if sorted(node.get("required", [])) != sorted(RUBRIC_ITEMS):
        problems.append("reader: not every rubric item is required")
    return problems


def check_code_maps(vocab: dict[str, tuple[str, ...]]) -> tuple[int, list[str]]:
    """Code-side tables keyed or valued by a vocabulary."""
    from app.course import errorspot, tree
    from app.course.methodologies import METHODOLOGIES

    problems: list[str] = []
    checked = 0

    def same(label: str, have: set[str], want: tuple[str, ...]) -> None:
        nonlocal checked
        checked += 1
        if have != set(want):
            problems.append(f"{label}: {sorted(have)} != {sorted(want)}")

    same("errorspot habits keyed by mutation kind", set(errorspot._HABITS), vocab["mutation_kind"])
    same("tree.BLOCK_TYPES keyed by topic kind", set(tree.BLOCK_TYPES), vocab["topic_kind"])
    for kind, allowed in tree.BLOCK_TYPES.items():
        checked += 1
        ghosts = set(allowed) - set(vocab["block_type"])
        if ghosts:
            problems.append(
                f"tree.BLOCK_TYPES[{kind}] names unregistered block types {sorted(ghosts)}"
            )
    for entry in METHODOLOGIES:
        code = entry["code"]
        bloom = set(entry["compatible_with"])
        checked += 1
        if not bloom <= set(vocab["bloom_level"]):
            problems.append(
                f"methodology {code}: unknown bloom levels"
                f" {sorted(bloom - set(vocab['bloom_level']))}"
            )
        for kind, roles in entry["prescription"].items():
            checked += 1
            if kind in ("lesson", "module_test") and isinstance(roles, list):
                bad = set(roles) - set(vocab["role"])
                if bad:
                    problems.append(
                        f"methodology {code}: {kind} prescribes unknown roles {sorted(bad)}"
                    )
            elif kind.endswith("_blocks") and isinstance(roles, dict):
                for stage, pairs in roles.items():
                    for role, block_type in pairs:
                        if role not in vocab["role"] or block_type not in vocab["block_type"]:
                            problems.append(
                                f"methodology {code}: {kind}.{stage} names {role}/{block_type}"
                            )
    checked += 1
    seen = {b for entry in METHODOLOGIES for b in entry["compatible_with"]}
    if seen != set(vocab["bloom_level"]):
        problems.append(
            f"bloom levels {sorted(vocab['bloom_level'])}"
            f" != union of compatible_with {sorted(seen)}"
        )
    return checked, problems


def prompt_root() -> Path | None:
    """The real prompts/ when every agent has a system prompt there, else the tracked stubs."""
    from app.agents.base import AGENT_SCHEMAS

    real = REPO / "prompts"
    if all((real / agent / "system.md").exists() for agent in AGENT_SCHEMAS):
        return None
    return REPO / "tests" / "fixtures" / "agent_prompts"


def main() -> int:
    """CLI entrypoint."""
    from app.agents.base import AGENT_SCHEMAS, validate_agents
    from app.course.tree import BLOCK_GENERATOR_SCHEMAS
    from app.errors import ConfigurationError

    vocab = vocabularies()
    schemas: dict[str, dict[str, Any]] = {a: dict(s) for a, s in AGENT_SCHEMAS.items()}
    schemas.update({f"generator:{t}": dict(s) for t, s in BLOCK_GENERATOR_SCHEMAS.items()})
    checked, seen, problems = check_schemas(schemas, vocab)
    for name in ("block_type", "role", "verdict", "topic_kind", "mutation_kind", "address_kind"):
        if name not in seen:
            problems.append(f"vocabulary {name} is bound but no schema emits it")
    problems += check_rubric(schemas["reader"])
    map_checked, map_problems = check_code_maps(vocab)
    problems += map_problems
    try:
        validate_agents(prompt_root())
    except ConfigurationError as exc:
        problems.append(f"validate_agents: {exc}")
    for line in problems:
        print(line)
    print(
        f"check_schema_enums: {checked + map_checked + len(AGENT_SCHEMAS)}"
        " vocabulary bindings checked"
    )
    return 1 if problems or checked == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
