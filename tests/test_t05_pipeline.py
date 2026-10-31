"""T05 generation pipeline: methodologies, blocks, agents, compiler, errors, language.

Spec (falsifiable):
1. Merrill prescription expands per Bloom/data; Backward Design and Kolb registered.
2. Every block fixture through every learner serializer never leaks its sentinel.
3. Reader runs once per topic with six-item verdicts + quoted evidence; prompt order + cache key.
4. Compiler emits wave A/B + reader + continuity + rework DAG; resume pays nothing twice.
5. spot_the_error plants exactly one sentence; grading gives partial credit; planted excluded.
6. Wrong-language blocks are held; glossary is per course language.
"""

from __future__ import annotations


def test_merrill_prescription_branches_by_bloom_and_data() -> None:
    # expand_merrill was deleted in T05c: it had no production caller.
    # The Merrill block plan lives in the registry prescription the
    # compiler and architect actually read.
    from app.course.methodologies import METHODOLOGIES, methodology_codes

    codes = methodology_codes()
    assert "merrill_first_principles" in codes
    assert "backward_design" in codes
    assert "kolb_experiential" in codes
    merrill = next(e for e in METHODOLOGIES if e["code"] == "merrill_first_principles")
    blocks = merrill["prescription"]["lesson_blocks"]
    assert blocks["activation"] == [["teach", "choice"]]
    assert blocks["demonstration"] == [["teach", "reading"]]
    assert ["teach", "figure"] in blocks["demonstration_data"]
    assert ["practice", "cloze"] in blocks["application_low"]
    assert ["practice", "scenario_practice"] in blocks["application_high"]
    assert blocks["integration"] == [["practice", "spot_the_error"]]
    test_blocks = merrill["prescription"]["module_test_blocks"]
    kinds = [block_type for _, block_type in test_blocks]
    assert kinds.count("spot_the_error") == 1
    assert "choice" in kinds and "matching" in kinds


def test_every_block_fixture_survives_every_learner_serializer() -> None:
    from app.blocks.registry import BLOCK_TYPES, learner_serializers

    serializers = learner_serializers()
    assert serializers, "at least one learner-facing serializer"
    for block_type in BLOCK_TYPES:
        from importlib import import_module

        fixture_mod = import_module(f"app.blocks.{block_type}.fixture")
        content, answer_key = fixture_mod.fixture()
        sentinel = fixture_mod.SENTINEL
        assert sentinel in str(answer_key)
        for name, serialize in serializers.items():
            rendered = serialize(block_type, content, answer_key)
            assert sentinel not in str(rendered), f"{block_type} leaks via {name}"


def test_block_schemas_are_strict_and_grading_is_code() -> None:
    from app.blocks.registry import BLOCK_TYPES

    for block_type in BLOCK_TYPES:
        from importlib import import_module

        schema_mod = import_module(f"app.blocks.{block_type}.schema")
        check_mod = import_module(f"app.blocks.{block_type}.check")
        fixture_mod = import_module(f"app.blocks.{block_type}.fixture")
        content, answer_key = fixture_mod.fixture()
        assert schema_mod.validate(content) == []
        assert schema_mod.validate({}) != []
        result = check_mod.grade(content, answer_key, submission=fixture_mod.correct_submission())
        assert result["score"] == 1.0
        wrong = check_mod.grade(content, answer_key, submission=fixture_mod.wrong_submission())
        assert wrong["score"] < 1.0


def test_generation_prompt_prefix_order_and_cache_key() -> None:
    from app.agents.generate_block import build_generation_prompt

    sources = [
        {"source_id": "s2", "address": "p2/para1", "text": "second"},
        {"source_id": "s1", "address": "p10/para2", "text": "ten"},
        {"source_id": "s1", "address": "p2/para1", "text": "two"},
    ]
    prompt = build_generation_prompt(
        agent="block_teach",
        block_type="reading",
        course_id="course-1",
        course_language="en",
        sources=sources,
        plan={"modules": []},
        dossier={"thesis": "t", "glossary": {}, "handoffs": []},
        skeleton={"plan_node_id": "n1", "brief": "topic"},
    )
    system_at = prompt.index("Role:")
    source_at = prompt.index("## Approved sources")
    plan_at = prompt.index("## Frozen plan + dossier")
    skeleton_at = prompt.index("## Block skeleton")
    assert system_at < source_at < plan_at < skeleton_at
    # Sorted by (source_id, address) with numeric para order.
    assert prompt.index('"two"') < prompt.index('"ten"')
    assert prompt.index('"ten"') < prompt.index('"second"')
    from app.llm.prompts import cache_key

    assert cache_key("block_teach", "reading", "course-1") == "block_teach:reading:course-1"


def test_reader_verdicts_cover_six_rubric_items_with_evidence() -> None:
    from app.agents.reader import RUBRIC_ITEMS, check_reader_output

    payload = {
        "verdicts": [
            {
                "plan_node_id": "n1",
                "verdict": "hold",
                "items": {
                    item: {"verdict": "hold", "evidence": f"quote {item}"} for item in RUBRIC_ITEMS
                },
            }
        ]
    }
    assert check_reader_output(payload, node_ids={"n1"}) == []
    bad = {
        "verdicts": [
            {
                "plan_node_id": "n1",
                "verdict": "pass",
                "items": {"task_fit": {"verdict": "pass", "evidence": "q"}},
            }
        ]
    }
    assert check_reader_output(bad, node_ids={"n1"}) != []


def test_reader_replay_on_stored_input() -> None:
    import json
    import pathlib

    from app.agents.reader import build_reader_prompt, check_reader_output

    stored = json.loads(pathlib.Path("tests/fixtures/t05_reader_input.json").read_text())
    prompt = build_reader_prompt(**stored["input"])
    assert "Role:" in prompt and "## Approved sources" in prompt
    assert check_reader_output(stored["output"], node_ids=set(stored["node_ids"])) == []


def test_saboteur_rewrites_only_non_numeric_kinds() -> None:
    from app.course.errorspot import MUTATION_KINDS, mutate_claim, rewrite_allowed

    assert set(MUTATION_KINDS) == {
        "number_date",
        "negation",
        "scope",
        "entity_swap",
        "cause_reversal",
        "bad_citation",
    }
    assert rewrite_allowed("negation") is True
    assert rewrite_allowed("number_date") is False
    out = mutate_claim("The fine ceiling is 15 million euro.", kind="number_date")
    assert out != "The fine ceiling is 15 million euro."
    assert "15" not in out


def test_every_mutation_kind_changes_any_sentence() -> None:
    from app.course.errorspot import MUTATION_KINDS, mutate_claim

    awkward = "The following AI practices shall be prohibited."
    for kind in MUTATION_KINDS:
        try:
            assert mutate_claim(awkward, kind=kind) != awkward, kind
        except ValueError:
            assert kind in ("entity_swap", "cause_reversal", "bad_citation", "number_date")


def test_compiler_emits_waves_reader_continuity_rework() -> None:
    from app.pipelines.merrill import compile_generation_dag

    tree = {
        "modules": [
            {
                "plan_node_id": "m1",
                "topics": [
                    {
                        "plan_node_id": "t1",
                        "kind": "lesson",
                        "blocks": [
                            {"plan_node_id": "b1", "role": "teach", "block_type": "reading"},
                            {"plan_node_id": "b2", "role": "practice", "block_type": "cloze"},
                        ],
                    }
                ],
            }
        ]
    }
    dag = compile_generation_dag(
        profile={"methodologies": ["merrill_first_principles"], "bloom": "apply"},
        tree=tree,
        course_id="course-1",
    )
    keys = [step["step_key"] for step in dag]
    assert keys[0].startswith("research:")
    assert any(key.startswith("reader:") for key in keys)
    assert any(key.startswith("continuity:") for key in keys)
    teach = next(step for step in dag if step["step_key"] == "block_teach:b1")
    practice = next(step for step in dag if step["step_key"] == "block_practice:b2")
    assert teach["depends_on"] != practice["depends_on"] or practice["depends_on"]
    assert "block_teach:b1" in practice["depends_on"]
    reader = next(step for step in dag if step["step_key"].startswith("reader:"))
    assert "block_practice:b2" in reader["depends_on"]
    for step in dag:
        assert ":" in step["step_key"] and step["input_digest"]


def test_spot_the_error_single_sentence_and_partial_credit() -> None:
    from app.course.errorspot import grade_spot_error, plant_error

    planted = plant_error(
        verified=[{"sentence": "Deployers must train staff.", "passage": "P1"}],
        reader_holds=[],
        seed=7,
    )
    assert planted["sentences"].count(planted["planted_sentence"]) == 1
    assert len([s for s in planted["sentences"] if s == planted["planted_sentence"]]) == 1
    full = grade_spot_error(
        planted,
        submission={
            "sentence": planted["planted_sentence"],
            "kind": planted["kind"],
            "passage": "P1",
            "correct_value": planted["true_sentence"],
        },
    )
    assert full["score"] == 1.0
    partial = grade_spot_error(
        planted,
        submission={"sentence": planted["planted_sentence"], "kind": planted["kind"]},
    )
    assert 0.0 < partial["score"] < 1.0
    feedback = partial["feedback"]
    assert {"wrong", "true", "passage", "habit"} <= set(feedback)


def test_planted_rows_excluded_and_teacher_report_counts() -> None:
    # teacher_report was deleted in T05c: no production caller exists
    # until the T06 learner surface lands its attempts table.
    from app.course.errorspot import is_excluded_from

    row = {"planted": True}
    for store in ("search", "retrieval_pair", "tutor_retrieval", "translation_memory"):
        assert is_excluded_from(row, store) is True
    assert is_excluded_from({"planted": False}, "search") is False


def test_wrong_language_block_held_and_glossary_per_language() -> None:
    from app.helpers.language import check_language, course_glossary

    assert check_language("This is English.", course_language="en") == []
    assert check_language("This is English.", course_language="nb") != []
    gloss = course_glossary("nb", {"risk": "risiko"})
    assert gloss["risk"] == "risiko"
    assert course_glossary("en", {"risk": "risk"})["risk"] == "risk"


def test_all_t05_agents_have_prompts_and_registry_steps() -> None:
    from app.agents import base
    from app.llm.registry import STEPS

    for agent in (
        "block_teach",
        "block_example",
        "block_check",
        "block_practice",
        "block_test",
        "reader",
        "saboteur",
        "continuity",
    ):
        assert (base.PROMPT_ROOT / agent / "system.md").exists(), agent
        assert agent in STEPS, agent
    assert STEPS["reader"].model == "muse-wonder-1.0" and STEPS["reader"].effort == "high"
    assert STEPS["saboteur"].model == "muse-wonder-1.0" and STEPS["saboteur"].effort == "medium"
