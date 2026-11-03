"""Diagram block: nodes and edges from one passage's flow; code draws, the model names."""

from __future__ import annotations

from typing import Any

MIN_NODES = 2


def validate(content: dict[str, Any]) -> list[str]:
    """Strict shape: caption, at least two labelled nodes, edges between known nodes."""
    errors: list[str] = []
    if not isinstance(content.get("caption"), str) or not content["caption"].strip():
        errors.append("caption must be a non-empty string")
    nodes = content.get("nodes")
    if not isinstance(nodes, list) or len(nodes) < MIN_NODES:
        errors.append(f"nodes must hold at least {MIN_NODES} labelled nodes")
        return errors
    ids: list[str] = []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict) or not str(node.get("label", "")).strip():
            errors.append(f"nodes[{index}]: label is required")
        ids.append(str(node.get("id", "")) if isinstance(node, dict) else "")
    if len(set(ids)) != len(ids) or any(not i for i in ids):
        errors.append("nodes must carry distinct ids")
    edges = content.get("edges")
    if not isinstance(edges, list) or not edges:
        errors.append("edges must be a non-empty list")
        return errors
    for index, edge in enumerate(edges):
        if (
            not isinstance(edge, dict)
            or str(edge.get("from", "")) not in ids
            or str(edge.get("to", "")) not in ids
        ):
            errors.append(f"edges[{index}]: from and to must name nodes of this diagram")
    return errors
