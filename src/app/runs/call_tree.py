"""One course's agent-chain tree: runs, blocks and model calls, with what each block depends on.

Reads the `llm_calls` ledger by run (so a learner or tutor call never appears) and
the runs' compiled DAGs, whose `depends_on` lists are the very rule the writers'
"Upstream blocks" and "Previously" inputs are built from. Bodies are not served:
a call's request and reply load through `app.runs.call_log.get_run_call`.
Callers: the course call-tree route via the controller in `app.controllers.t03`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import NotFoundError
from app.llm.registry import display_name_of
from app.runs.call_log import CACHED_REPLY_SQL
from app.services.hold_text import human_hold_reason

BLOCK_AGENTS = ("block_teach", "block_example", "block_exercise")
BLOCK_STEP_AGENTS = (*BLOCK_AGENTS, "saboteur")
FINISHED = ("done", "stopped")


def _node(
    node_id: str, kind: str, label: str, *, status: str | None = None, **facts: Any
) -> dict[str, Any]:
    return {
        "id": node_id,
        "kind": kind,
        "label": label,
        "status": status,
        "facts": facts,
        "children": [],
        "deps": [],
    }


def _totals(node: dict[str, Any]) -> tuple[int, Decimal]:
    if node["kind"] == "call":
        return 1, Decimal(node["facts"]["cost_usd"])
    calls, cost = 0, Decimal(0)
    for child in node["children"]:
        child_calls, child_cost = _totals(child)
        calls += child_calls
        cost += child_cost
    node["facts"]["calls"] = calls
    node["facts"]["cost_usd"] = str(cost)
    return calls, cost


def _split(step_key: str | None) -> tuple[str, str]:
    agent, _, rest = (step_key or "").partition(":")
    return agent, rest


def _call_node(row: Any) -> dict[str, Any]:
    return _node(
        f"call:{row.id}",
        "call",
        row.agent,
        status=row.status,
        call_id=str(row.id),
        run_id=str(row.run_id),
        agent=row.agent,
        step_key=row.step_key,
        model=row.model,
        display_name=display_name_of(str(row.model)),
        variant=int(row.variant),
        cached_reply=bool(row.cached),
        cost_usd=str(row.cost_usd),
        tokens_input=int(row.tokens_input),
        tokens_cached=int(row.tokens_cached),
        tokens_output=int(row.tokens_output),
        tokens_reasoning=int(row.tokens_reasoning),
        duration_ms=int(row.duration_ms),
        created_at=row.created_at.isoformat(),
        query=row.query,
    )


async def _runs(session: AsyncSession, course_id: UUID) -> list[Any]:
    return list(
        (
            await session.execute(
                sa.text(
                    "SELECT id, kind, status, created_at, stop_marker, compiled_dag"
                    " FROM runs WHERE course_id = :course ORDER BY created_at, id"
                ),
                {"course": str(course_id)},
            )
        ).all()
    )


async def _calls(session: AsyncSession, course_id: UUID) -> list[Any]:
    return list(
        (
            await session.execute(
                sa.text(
                    "SELECT c.id, c.run_id, c.agent, c.model, c.status, c.variant, c.created_at,"
                    " c.cost_usd, c.tokens_input, c.tokens_cached, c.tokens_output,"
                    f" c.tokens_reasoning, c.duration_ms, {CACHED_REPLY_SQL} AS cached,"
                    " s.step_key, CASE WHEN c.agent = 'researcher' THEN c.response ->> 'query' END"
                    " AS query,"
                    " CASE WHEN c.agent = 'architect' THEN c.response -> 'modules' END AS modules"
                    " FROM llm_calls c JOIN runs r ON r.id = c.run_id"
                    " LEFT JOIN run_steps s ON s.id = c.step_id"
                    " WHERE r.course_id = :course AND c.subject_user_id IS NULL"
                    " ORDER BY c.created_at, c.id"
                ),
                {"course": str(course_id)},
            )
        ).all()
    )


async def _blocks(session: AsyncSession, course_id: UUID) -> list[Any]:
    return list(
        (
            await session.execute(
                sa.text(
                    "SELECT b.id, b.plan_node_id, b.role, b.block_type, b.status, b.hold_reason,"
                    " t.id AS topic_id, t.plan_node_id AS topic_node, t.title AS topic_title,"
                    " t.kind AS topic_kind, m.id AS module_id, m.title AS module_title"
                    " FROM blocks b JOIN topics t ON t.id = b.topic_id"
                    " JOIN modules m ON m.id = t.module_id WHERE m.course_id = :course"
                    " ORDER BY m.ordinal, t.ordinal, b.ordinal"
                ),
                {"course": str(course_id)},
            )
        ).all()
    )


def _architect_calls(calls: list[Any]) -> tuple[dict[str, str], dict[str, str]]:
    """Latest completed architect call per module node, and each topic node's module node."""
    by_module: dict[str, str] = {}
    module_of_topic: dict[str, str] = {}
    for row in calls:
        if row.agent != "architect" or row.status != "completed":
            continue
        for module in row.modules or []:
            module_node = str(module.get("plan_node_id", ""))
            if not module_node:
                continue
            by_module[module_node] = f"call:{row.id}"
            for topic in module.get("topics", []):
                module_of_topic[str(topic.get("plan_node_id", ""))] = module_node
    return by_module, module_of_topic


def _hold_lines(reason: str | None, labels: dict[str, str]) -> list[str]:
    text = human_hold_reason(reason, labels)
    return [line for line in text.split("; ") if line]


class _RunTree:
    """The nodes of one run: modules, topics and blocks created on demand, calls hung on them."""

    def __init__(self, run: Any, blocks: list[Any], hold_labels: dict[str, str]) -> None:
        self.run = run
        self.run_id = f"run:{run.id}"
        self.node = _node(
            self.run_id,
            "run",
            run.kind,
            status=run.status,
            run_id=str(run.id),
            run_kind=run.kind,
            created_at=run.created_at.isoformat(),
            stop_marker=run.stop_marker,
        )
        self.blocks = {str(b.plan_node_id): b for b in blocks}
        self.hold_labels = hold_labels
        self.modules: dict[str, dict[str, Any]] = {}
        self.topics: dict[str, dict[str, Any]] = {}
        self.block_nodes: dict[str, dict[str, Any]] = {}
        self.groups: dict[str, dict[str, Any]] = {}

    def group(self, key: str, parent: dict[str, Any] | None = None, **facts: Any) -> dict[str, Any]:
        node_id = f"{self.run_id}:group:{key}"
        if node_id not in self.groups:
            self.groups[node_id] = _node(node_id, "group", key, group=key.split(":")[0], **facts)
            (parent if parent is not None else self.node)["children"].append(self.groups[node_id])
        return self.groups[node_id]

    def block(self, plan_node_id: str) -> dict[str, Any] | None:
        row = self.blocks.get(plan_node_id)
        if row is None:
            return None
        key = str(row.id)
        if key in self.block_nodes:
            return self.block_nodes[key]
        topic = self._topic(row)
        node = _node(
            f"{self.run_id}:block:{row.id}",
            "block",
            row.plan_node_id,
            status=row.status,
            block_id=str(row.id),
            plan_node_id=row.plan_node_id,
            role=row.role,
            block_type=row.block_type,
            hold_lines=_hold_lines(row.hold_reason, self.hold_labels)
            if row.status == "held"
            else [],
        )
        topic["children"].append(node)
        self.block_nodes[key] = node
        return node

    def _topic(self, row: Any) -> dict[str, Any]:
        key = str(row.topic_id)
        if key not in self.topics:
            module_key = str(row.module_id)
            if module_key not in self.modules:
                self.modules[module_key] = _node(
                    f"{self.run_id}:module:{row.module_id}",
                    "module",
                    row.module_title,
                    module_id=module_key,
                )
                self.node["children"].append(self.modules[module_key])
            self.topics[key] = _node(
                f"{self.run_id}:topic:{row.topic_id}",
                "topic",
                row.topic_title,
                topic_id=key,
                plan_node_id=row.topic_node,
                topic_kind=row.topic_kind,
                module_id=module_key,
            )
            self.modules[module_key]["children"].append(self.topics[key])
        return self.topics[key]

    def attach(self, call: dict[str, Any], step_key: str | None) -> None:
        agent, rest = _split(step_key)
        if agent in BLOCK_STEP_AGENTS and (block := self.block(rest)) is not None:
            block["children"].append(call)
        elif agent == "reader" and rest:
            self._reader(rest)["children"].append(call)
        elif agent == "researcher":
            query = self.group(f"research:{step_key}", self.group("research"), step_key=step_key)
            query["facts"]["query"] = query["facts"].get("query") or call["facts"]["query"]
            query["children"].append(call)
        elif agent in ("architect", "blueprint", "lectures", "blockplan"):
            self.group("planning", step_key=step_key)["children"].append(call)
        else:
            self.group(f"other:{step_key or ''}", step_key=step_key)["children"].append(call)

    def _reader(self, topic_node: str) -> dict[str, Any]:
        row = next((b for b in self.blocks.values() if b.topic_node == topic_node), None)
        if row is None:
            return self.group(f"other:reader:{topic_node}", step_key=f"reader:{topic_node}")
        topic = self._topic(row)
        node_id = f"{self.run_id}:group:reader:{topic_node}"
        if node_id not in self.groups:
            self.groups[node_id] = _node(
                node_id, "group", "reader", group="reader", step_key=f"reader:{topic_node}"
            )
            topic["children"].append(self.groups[node_id])
        return self.groups[node_id]

    def wire_dependencies(
        self, architect_of: dict[str, str], module_of_topic: dict[str, str]
    ) -> None:
        dag = self.run.compiled_dag or []
        deps_of = {str(e.get("step_key")): [str(d) for d in e.get("depends_on", [])] for e in dag}
        for entry in dag:
            agent, node_id = _split(str(entry.get("step_key")))
            if agent in BLOCK_AGENTS:
                self.block(node_id)
        for row in self.blocks.values():
            node = self.block_nodes.get(str(row.id))
            if node is None:
                continue
            architect = architect_of.get(module_of_topic.get(str(row.topic_node), ""), "")
            deps = [architect] if architect else []
            step = next(
                (k for k in (f"{a}:{row.plan_node_id}" for a in BLOCK_AGENTS) if k in deps_of), None
            )
            for dep in deps_of.get(step or "", []):
                dep_agent, dep_node = _split(dep)
                dep_block = self.blocks.get(dep_node)
                if (
                    dep_agent in BLOCK_AGENTS
                    and dep_block is not None
                    and dep_node != row.plan_node_id
                ):
                    dep_id = f"{self.run_id}:block:{dep_block.id}"
                    if dep_id not in deps:
                        deps.append(dep_id)
            node["deps"] = deps
        for reader in self.groups.values():
            if reader["facts"].get("group") != "reader":
                continue
            topic_node = reader["facts"]["step_key"].partition(":")[2]
            reader["deps"] = [
                f"{self.run_id}:block:{b.id}"
                for b in self.blocks.values()
                if b.topic_node == topic_node and str(b.id) in self.block_nodes
            ]


async def course_call_tree(
    session: AsyncSession, *, course_id: UUID, hold_labels: dict[str, str]
) -> dict[str, Any]:
    """The course as a tree of runs, modules, topics, blocks and calls, without bodies."""
    course = (
        await session.execute(
            sa.text("SELECT title FROM courses WHERE id = :course"), {"course": str(course_id)}
        )
    ).first()
    if course is None:
        raise NotFoundError("course not found")
    runs = await _runs(session, course_id)
    calls = await _calls(session, course_id)
    blocks = await _blocks(session, course_id)
    architect_of, module_of_topic = _architect_calls(calls)
    trees = {str(run.id): _RunTree(run, blocks, hold_labels) for run in runs}
    for row in calls:
        trees[str(row.run_id)].attach(_call_node(row), row.step_key)
    root = _node(
        f"course:{course_id}",
        "course",
        course[0],
        live=any(run.status not in FINISHED for run in runs),
        blocks_accepted=sum(1 for b in blocks if b.status == "accepted"),
        blocks_held=sum(1 for b in blocks if b.status == "held"),
    )
    for run in runs:
        tree = trees[str(run.id)]
        tree.wire_dependencies(architect_of, module_of_topic)
        root["children"].append(tree.node)
    _totals(root)
    return root
