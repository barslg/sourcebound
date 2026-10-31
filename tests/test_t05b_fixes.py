"""T05b regression tests: verdicts change state, keys stay server-side, grading is exact."""

from __future__ import annotations


def test_planted_is_held_sentence_and_true_not_shown() -> None:
    from app.course.errorspot import plant_error

    ex = plant_error(
        verified=[{"sentence": "Deployers shall ensure literacy.", "passage": "P1"}],
        reader_holds=[
            {
                "sentence": "Deployers need not ensure literacy.",
                "passage": "P1",
                "true_sentence": "Deployers shall ensure literacy.",
            }
        ],
        seed=1,
    )
    assert ex["planted_sentence"] == "Deployers need not ensure literacy."
    assert ex["true_sentence"] == "Deployers shall ensure literacy."
    assert ex["true_sentence"] not in ex["sentences"]
    assert ex["sentences"].count(ex["planted_sentence"]) == 1


def test_planted_index_seeded_not_always_zero() -> None:
    from app.course.errorspot import plant_error

    idx = set()
    for seed in range(12):
        ex = plant_error(
            verified=[
                {"sentence": "Alpha shall apply.", "passage": "P1"},
                {"sentence": "Beta shall apply.", "passage": "P1"},
                {"sentence": "Gamma shall apply.", "passage": "P1"},
            ],
            reader_holds=[],
            seed=seed,
            kind="negation",
        )
        idx.add(ex["planted_index"])
        assert ex["sentences"][ex["planted_index"]] == ex["planted_sentence"]
    assert len(idx) > 1


def test_mutations_have_no_announcement_or_hardcoded_facts() -> None:
    from app.course.errorspot import MUTATION_KINDS, mutate_claim

    s = "Providers shall keep records for inspection."
    for kind in MUTATION_KINDS:
        try:
            out = mutate_claim(s, kind=kind)
        except ValueError:
            assert kind in ("entity_swap", "cause_reversal", "bad_citation", "number_date"), kind
            continue
        assert out != s, kind
        for bad in ("[citation", "(under the GDPR", "(as of", "2027", "reversed)"):
            assert bad not in out, (kind, out)


def test_number_date_skips_article_reference() -> None:
    from app.course.errorspot import mutate_claim

    out = mutate_claim("Under Article 5 the fine is 35 million euro.", kind="number_date")
    assert "Article 5" in out
    assert "35 million" not in out


def test_grading_correct_value_must_match() -> None:
    from app.course.errorspot import grade_spot_error, plant_error

    ex = plant_error(
        verified=[{"sentence": "Fine is 35 million.", "passage": "P1"}], reader_holds=[], seed=0
    )
    wrong_val = grade_spot_error(
        ex,
        submission={
            "sentence": ex["planted_sentence"],
            "kind": ex["kind"],
            "passage": "P1",
            "correct_value": "x",
        },
    )
    right_val = grade_spot_error(
        ex,
        submission={
            "sentence": ex["planted_sentence"],
            "kind": ex["kind"],
            "passage": "P1",
            "correct_value": ex["true_sentence"],
        },
    )
    assert wrong_val["score"] < right_val["score"]
    assert right_val["score"] == 1.0


def test_fully_correct_via_check_module() -> None:
    from app.blocks.spot_the_error.check import grade

    content = {"sentences": ["Alpha shall apply.", "Beta shall apply."]}
    key = {
        "planted_sentence": "",
        "planted_index": -1,
        "kind": "negation",
        "passage": "P1",
        "true_sentence": "",
        "fully_correct": True,
    }
    assert grade(content, key, {"sentence": "none"})["score"] == 1.0
    assert grade(content, key, {"sentence": ""})["score"] == 1.0
    assert grade(content, key, {"sentence": "Alpha shall apply."})["score"] == 0.0


def test_teacher_report_miss_is_not_false_alarm() -> None:
    from app.course.errorspot import teacher_report

    rep = teacher_report(
        [
            {"kind": "negation", "hit": True},
            {"kind": "negation", "hit": False, "false_alarm": True},
            {"kind": "negation", "hit": False},
        ]
    )
    assert rep["negation"] == {"hits": 1, "false_alarms": 1}


def test_spot_content_never_carries_key() -> None:
    from app.blocks.spot_the_error.fixture import SENTINEL, fixture
    from app.blocks.spot_the_error.schema import validate

    content, key = fixture()
    assert "planted_index" not in content and "kind" not in content
    assert validate(content) == []
    assert SENTINEL in str(key)


def test_reader_schema_fixed_items() -> None:
    from app.agents.reader import check_reader_output, schema
    from app.llm.schema import validate_strict_subset

    validate_strict_subset(schema(), name="reader")
    items = {
        k: {"verdict": "pass", "evidence": "q"}
        for k in (
            "task_fit",
            "grounding",
            "continuity",
            "answer_key",
            "audience_language",
            "planted_error",
        )
    }
    ok = {"verdicts": [{"plan_node_id": "n1", "verdict": "pass", "items": items}]}
    assert check_reader_output(ok, node_ids={"n1"}) == []
    assert (
        check_reader_output(
            {
                "verdicts": [
                    {
                        "plan_node_id": "n1",
                        "verdict": "pass",
                        "items": {"task_fit": {"verdict": "pass", "evidence": "q"}},
                    }
                ]
            },
            node_ids={"n1"},
        )
        != []
    )


def test_language_per_course_language() -> None:
    from app.helpers.language import check_language

    assert check_language("Ansatte må få opplæring og veiledning.", course_language="nb") == []
    assert check_language("Ansatte må få opplæring og veiledning.", course_language="en") != []
    assert check_language("Працівники повинні пройти навчання.", course_language="uk") == []
    assert check_language("Працівники повинні пройти навчання.", course_language="en") != []
    assert check_language("Die Mitarbeiter müssen geschult werden.", course_language="de") == []


def test_methodologies_differ_in_dag() -> None:
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
                            {"plan_node_id": "b2", "role": "check", "block_type": "choice"},
                        ],
                    }
                ],
            }
        ]
    }
    dags = {}
    for m in ("merrill_first_principles", "backward_design", "kolb_experiential"):
        dags[m] = [
            (s["step_key"], tuple(s["depends_on"]))
            for s in compile_generation_dag(
                profile={"methodology_codes": [m], "bloom_level": "apply"},
                tree=tree,
                course_id="c1",
                methodology=m,
            )
        ]
    assert dags["merrill_first_principles"] != dags["backward_design"]
    assert dags["merrill_first_principles"] != dags["kolb_experiential"]


def test_per_type_generator_schemas() -> None:
    from app.agents.generate_block import schema_for
    from app.llm.schema import validate_strict_subset

    for bt in (
        "reading",
        "choice",
        "matching",
        "cloze",
        "scenario_practice",
        "figure",
        "spot_the_error",
    ):
        validate_strict_subset(schema_for(bt), name=bt)
    assert "options" in str(schema_for("choice"))
    assert schema_for("choice") != schema_for("reading")


def test_code_required_keys_subset_of_schema() -> None:
    from app.agents.base import validate_agents

    validate_agents()


def test_reader_replay_on_logged_live_call() -> None:
    import json
    import pathlib

    from app.agents.reader import check_reader_output

    path = pathlib.Path("tests/fixtures/t05_reader_live.json")
    if not path.exists():
        import pytest

        pytest.skip("live reader fixture not exported yet (run make check-live)")
    data = json.loads(path.read_text())
    response = data["response"] or {}
    text = ""
    for item in response.get("output", []):
        for part in item.get("content", []):
            if part.get("type") == "output_text":
                text = part.get("text", "")
    payload = json.loads(text) if text else response
    node_ids = {v["plan_node_id"] for v in payload.get("verdicts", [])}
    assert node_ids, "logged reader reply carries verdicts"
    assert check_reader_output(payload, node_ids=node_ids) == []


def test_sanitize_json_strips_nul() -> None:
    from app.llm.calls import sanitize_json

    dirty = {"a": "x\x00y", "b": ["z\x00", {"c": 1}], "n": 3}
    clean = sanitize_json(dirty)
    assert "\x00" not in str(clean)
    assert clean["a"] == "xy" and clean["n"] == 3


def test_stop_marker_never_carries_reply_text() -> None:
    from app.runs.executor import safe_stop_marker

    marker = safe_stop_marker("stopped at reader:t1: LLMError: model said SECRET_REPLY_TEXT")
    assert "SECRET_REPLY_TEXT" not in marker
    assert "LLMError" in marker


def test_derive_parser_spec_from_html() -> None:
    from app.agents.librarian import derive_parser_spec, execute_parser_spec, validate_parser_spec

    html = "<article><h2>Duty</h2><p>Article 4 requires literacy.</p></article>"
    spec = derive_parser_spec(html)
    assert spec is not None and validate_parser_spec(spec) == []
    records = execute_parser_spec(html, spec)
    assert records and "literacy" in records[0]["body"]


def test_saboteur_kind_without_rewrite_raises() -> None:
    import pytest

    from app.course.errorspot import plant_error

    with pytest.raises(ValueError):
        plant_error(
            verified=[{"sentence": "Providers shall keep records.", "passage": "P1"}],
            reader_holds=[],
            seed=0,
            kind="entity_swap",
        )


def test_compiler_inserts_saboteur_before_spot_block() -> None:
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
                            {
                                "plan_node_id": "b4",
                                "role": "practice",
                                "block_type": "spot_the_error",
                            }
                        ],
                    }
                ],
            }
        ]
    }
    dag = compile_generation_dag(
        profile={"methodology_codes": ["merrill_first_principles"], "bloom_level": "apply"},
        tree=tree,
        course_id="c1",
        methodology="merrill_first_principles",
    )
    keys = [s["step_key"] for s in dag]
    assert any(k.startswith("saboteur:") for k in keys), keys
    gen = next(s for s in dag if s["step_key"].endswith(":b4") and s["handler"] == "generate_block")
    assert any(d.startswith("saboteur:") for d in gen["depends_on"])
