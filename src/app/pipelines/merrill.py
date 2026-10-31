"""Generation pipelines: one compiler per methodology.

The Merrill compiler expands a profile plus an approved plan tree into
the stored DAG: research, plan, per-topic wave A teach/example, wave B
check/practice/test reading wave A text, reader per topic, continuity
reader per module, then rework for named blocks only. Backward Design
and Kolb reuse the same wave shape with their own role mix so the
compiler can expand them; the demo runs Merrill.
"""

from __future__ import annotations

from typing import Any

from app.runs.executor import input_digest_of

_WAVE_A_ROLES = ("teach", "example")
_ROLE_TO_AGENT = {
    "teach": "block_teach",
    "example": "block_example",
    "check": "block_check",
    "practice": "block_practice",
    "test": "block_test",
}


def _role_agent(role: str) -> str:
    return _ROLE_TO_AGENT.get(role, "block_practice")


def compile_generation_dag(
    *,
    profile: dict[str, Any],
    tree: dict[str, Any],
    course_id: str,
    methodology: str = "merrill_first_principles",
) -> list[dict[str, Any]]:
    """Expand profile plus plan into generation steps with dependencies."""
    dag: list[dict[str, Any]] = []
    dag.append(
        {
            "step_key": "research:plan",
            "handler": "generation_research",
            "depends_on": [],
            "input_digest": input_digest_of(
                {"kind": "research", "course": course_id, "methodology": methodology}
            ),
        }
    )
    dag.append(
        {
            "step_key": "plan:plan",
            "handler": "generation_plan",
            "depends_on": ["research:plan"],
            "input_digest": input_digest_of(
                {"kind": "plan", "course": course_id, "methodology": methodology}
            ),
        }
    )
    methodology = str(
        profile.get("methodology") or profile.get("methodologies", [""])[0]
        if isinstance(profile.get("methodologies"), list)
        else methodology
    )
    method = methodology
    for module in tree.get("modules", []):
        module_id = str(module.get("plan_node_id", "module"))
        for topic in module.get("topics", []):
            topic_id = str(topic.get("plan_node_id", "topic"))
            wave_a: list[str] = []
            wave_b: list[str] = []
            for block in topic.get("blocks", []):
                node_id = str(block.get("plan_node_id", "block"))
                role = str(block.get("role", "practice"))
                agent = _role_agent(role)
                key = f"{agent}:{node_id}"
                if role in _WAVE_A_ROLES:
                    wave_a.append(key)
                    dag.append(
                        {
                            "step_key": key,
                            "handler": "generate_block",
                            "depends_on": ["plan:plan"],
                            "input_digest": input_digest_of(
                                {
                                    "kind": "block",
                                    "node": node_id,
                                    "course": course_id,
                                    "methodology": method,
                                    "block": block,
                                }
                            ),
                        }
                    )
                else:
                    wave_b.append(key)
            for block in topic.get("blocks", []):
                node_id = str(block.get("plan_node_id", "block"))
                role = str(block.get("role", "practice"))
                if role in _WAVE_A_ROLES:
                    continue
                agent = _role_agent(role)
                key = f"{agent}:{node_id}"
                deps = ["plan:plan"] if method == "backward_design" else ["plan:plan", *wave_a]
                dag.append(
                    {
                        "step_key": key,
                        "handler": "generate_block",
                        "depends_on": deps,
                        "input_digest": input_digest_of(
                            {
                                "kind": "block",
                                "node": node_id,
                                "course": course_id,
                                "methodology": method,
                                "profile": profile,
                                "block": block,
                            }
                        ),
                    }
                )
            if method == "kolb_experiential":
                dag.append(
                    {
                        "step_key": f"reflection:{topic_id}",
                        "handler": "read_continuity",
                        "depends_on": [*wave_a, *wave_b],
                        "input_digest": input_digest_of(
                            {"kind": "reflection", "node": topic_id, "course": course_id}
                        ),
                    }
                )
            for block in topic.get("blocks", []):
                if str(block.get("block_type", "")) != "spot_the_error":
                    continue
                node_id = str(block.get("plan_node_id", "block"))
                sab_key = f"saboteur:{node_id}"
                sab_entry = {
                    "step_key": sab_key,
                    "handler": "saboteur",
                    "depends_on": ["plan:plan"],
                    "kind": "entity_swap",
                    "input_digest": input_digest_of(
                        {"kind": "saboteur", "node": node_id, "course": course_id}
                    ),
                }
                for index, step in enumerate(dag):
                    is_gen = step["handler"] == "generate_block"
                    if is_gen and step["step_key"].endswith(f":{node_id}"):
                        step["depends_on"] = [*step["depends_on"], sab_key]
                        dag.insert(index, sab_entry)
                        break
                else:
                    dag.append(sab_entry)
            reader_key = f"reader:{topic_id}"
            dag.append(
                {
                    "step_key": reader_key,
                    "handler": "read_topic",
                    "depends_on": [*wave_a, *wave_b],
                    "input_digest": input_digest_of(
                        {"kind": "reader", "node": topic_id, "course": course_id}
                    ),
                }
            )
        continuity_key = f"continuity:{module_id}"
        dag.append(
            {
                "step_key": continuity_key,
                "handler": "read_continuity",
                "depends_on": [
                    step["step_key"] for step in dag if step["step_key"].startswith("reader:")
                ],
                "input_digest": input_digest_of(
                    {"kind": "continuity", "node": module_id, "course": course_id}
                ),
            }
        )
    dag.append(
        {
            "step_key": "rework:named-blocks",
            "handler": "rework_blocks",
            "depends_on": [
                step["step_key"]
                for step in dag
                if step["step_key"].startswith(("reader:", "continuity:"))
            ],
            "input_digest": input_digest_of({"kind": "rework", "course": course_id}),
        }
    )
    return dag
