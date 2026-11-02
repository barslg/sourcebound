"""Generation pipelines: one compiler per methodology.

The Merrill compiler expands a profile plus an approved plan tree into
the stored DAG: research, plan, per-topic wave A (teach, then example
reading the teach text), wave B exercises reading wave A text, reader
per topic, then rework for named blocks only. Teach blocks are chained
topic after topic inside a module, so each one reads the handoffs of
the topics before it ("previously"); modules run side by side.
Backward Design and Kolb reuse the same wave shape with their own role
mix so the compiler can expand them; the demo runs Merrill.
"""

from __future__ import annotations

from typing import Any

from app.runs.executor import input_digest_of

_WAVE_A_ROLES = ("teach", "example")
_ROLE_TO_AGENT = {
    "teach": "block_teach",
    "example": "block_example",
    "check": "block_exercise",
    "practice": "block_exercise",
    "test": "block_exercise",
}


def _role_agent(role: str) -> str:
    return _ROLE_TO_AGENT.get(role, "block_exercise")


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
        previous_teach: list[str] = []
        for topic in module.get("topics", []):
            topic_id = str(topic.get("plan_node_id", "topic"))
            wave_a: list[str] = []
            wave_b: list[str] = []
            teach_keys: list[str] = []
            for block in topic.get("blocks", []):
                node_id = str(block.get("plan_node_id", "block"))
                role = str(block.get("role", "practice"))
                agent = _role_agent(role)
                key = f"{agent}:{node_id}"
                if role in _WAVE_A_ROLES:
                    wave_a.append(key)
                    # an example reads the teach text of its topic; a teach block reads
                    # the handoffs of every earlier topic of the module
                    upstream = list(previous_teach) if role == "teach" else list(teach_keys)
                    if role == "teach":
                        teach_keys.append(key)
                    dag.append(
                        {
                            "step_key": key,
                            "handler": "generate_block",
                            "depends_on": ["plan:plan", *upstream],
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
            previous_teach = [*previous_teach, *teach_keys]
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
            spot_count = sum(
                1
                for b in topic.get("blocks", [])
                if str(b.get("block_type", "")) == "spot_the_error"
            )
            for block in topic.get("blocks", []):
                if str(block.get("block_type", "")) != "spot_the_error":
                    continue
                node_id = str(block.get("plan_node_id", "block"))
                sab_key = f"saboteur:{node_id}"
                # A module test whose only task is this exercise must get
                # a planted error; fully_correct is allowed only beside
                # another planted spot block of the same topic.
                allow_fully = not (str(topic.get("kind", "")) == "module_test" and spot_count < 2)
                sab_entry = {
                    "step_key": sab_key,
                    "handler": "saboteur",
                    "depends_on": ["plan:plan"],
                    "topic_kind": str(topic.get("kind", "")),
                    "topic_title": str(topic.get("title", "")),
                    "topic_purpose": " ".join(str(o) for o in topic.get("objectives", [])),
                    "allow_fully_correct": allow_fully,
                    "input_digest": input_digest_of(
                        {"kind": "saboteur", "node": node_id, "course": course_id}
                    ),
                }
                for index, step in enumerate(dag):
                    is_gen = step["handler"] == "generate_block"
                    if is_gen and step["step_key"].endswith(f":{node_id}"):
                        step["depends_on"] = [*step["depends_on"], sab_key]
                        step["topic_kind"] = str(topic.get("kind", ""))
                        step["allow_fully_correct"] = allow_fully
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
        _ = module_id
    dag.append(
        {
            "step_key": "rework:named-blocks",
            "handler": "rework_blocks",
            "depends_on": [
                step["step_key"] for step in dag if step["step_key"].startswith("reader:")
            ],
            "input_digest": input_digest_of({"kind": "rework", "course": course_id}),
        }
    )
    return dag
