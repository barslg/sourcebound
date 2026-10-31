"""T05c probes: every measured review item gets a red-first regression test.

Unit probes need no DB; DB probes run on the real test database through
the compiled DAG with a mock provider (the product path, no stub of the
executor). Caller for every helper here is the T05c acceptance in
build/tasks/T05c.md; each test proves the fact it names, nothing else.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

# --- M6: grading -------------------------------------------------------------

PLANTED = {
    "sentences": ["Alpha must apply.", "Beta must never apply."],
    "planted_sentence": "Beta must never apply.",
    "planted_index": 1,
    "kind": "negation",
    "passage": "P1",
    "true_sentence": "Beta must apply.",
    "fully_correct": False,
}


def _grade(**submission: Any) -> float:
    from app.course.errorspot import grade_spot_error

    return float(grade_spot_error(PLANTED, submission=submission)["score"])


def test_grading_nine_cases() -> None:
    full = {
        "sentence": "Beta must never apply.",
        "kind": "negation",
        "passage": "P1",
        "correct_value": "Beta must apply.",
    }
    assert _grade(**full) == 1.0
    # wrong sentence earns nothing, even with right kind/passage/fix
    assert (
        _grade(
            sentence="Alpha must apply.",
            kind="negation",
            passage="P1",
            correct_value="Beta must apply.",
        )
        == 0.0
    )
    # "none" on a planted exercise earns nothing
    assert (
        _grade(sentence="none", kind="negation", passage="P1", correct_value="Beta must apply.")
        == 0.0
    )
    # trailing space / case still counts as the exact sentence
    assert (
        _grade(
            sentence="  beta must never apply. ",
            kind="negation",
            passage="P1",
            correct_value="Beta must apply.",
        )
        == 1.0
    )
    # partial credit only behind the right sentence
    assert _grade(sentence="Beta must never apply.") == 0.4
    assert _grade(sentence="Beta must never apply.", kind="negation") == 0.65
    assert _grade(sentence="Beta must never apply.", kind="negation", passage="P1") == 0.8
    assert (
        _grade(sentence_index=1, kind="negation", passage="P1", correct_value="Beta must apply.")
        == 1.0
    )
    from app.course.errorspot import grade_spot_error

    correct_exercise = {**PLANTED, "fully_correct": True, "planted_index": -1}
    assert grade_spot_error(correct_exercise, submission={"sentence": "none"})["score"] == 1.0
    assert (
        grade_spot_error(correct_exercise, submission={"sentence": "Alpha must apply."})["score"]
        == 0.0
    )


def test_fully_correct_is_seeded_in_production_picker() -> None:
    from app.course.errorspot import plant_error

    verified = [
        {"sentence": "Providers shall ensure literacy.", "passage": "P1"},
        {"sentence": "Deployers shall train staff.", "passage": "P2"},
    ]
    outcomes = {
        plant_error(
            verified=verified,
            reader_holds=[],
            seed=seed,
            fully_correct=None,
            mutated="A planted sentence.",
        )["fully_correct"]
        for seed in range(40)
    }
    assert outcomes == {True, False}


# --- M7: number_date protects references -------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "Article 5(1) lists practices fined up to EUR 10 000 000.",
        "Articles 4, 5 and 6 set duties backed by EUR 10 000 000 fines.",
        "Regulation (EU) 2024/1689 sets fines of EUR 10 000 000.",
        "Under paragraph 3 the fine is EUR 10 000 000.",
        "Artikkel 5 i forordningen gir bøter på EUR 10 000 000.",
        "Article 99(4) caps fines at EUR 15 000 000 or 3 % of turnover.",
    ],
)
def test_number_date_never_mutates_a_reference(sentence: str) -> None:
    from app.course.errorspot import mutate_claim

    mutated = mutate_claim(sentence, kind="number_date")
    for token in re.findall(
        r"Article \d+|Articles [\d, and]+|2024/1689|paragraph 3|Artikkel 5|99\(4\)", sentence
    ):
        assert token in mutated, (sentence, mutated)
    assert mutated != sentence


def test_number_date_with_only_a_reference_raises() -> None:
    from app.course.errorspot import mutate_claim

    with pytest.raises(ValueError):
        mutate_claim("Article 5 applies here.", kind="number_date")


# --- H3: one picker ------------------------------------------------------------

VERIFIED = [
    {
        "sentence": "Reuse notice: EUR-Lex texts are reusable with acknowledgement.",
        "passage": "P1",
        "is_content": False,
    },
    {"sentence": "Providers shall ensure a sufficient level of AI literacy.", "passage": "P2"},
    {
        "sentence": "Non-compliance shall be subject to fines of up to EUR 35 000 000.",
        "passage": "P3",
    },
]


def test_picker_ignores_non_content_and_key_matches_pick() -> None:
    from app.course.errorspot import pick_sentence, plant_error

    for seed in range(12):
        pick = pick_sentence(VERIFIED, seed=seed)
        assert pick["is_content"] is not False
        assert "Reuse notice" not in pick["sentence"]
        exercise = plant_error(
            verified=VERIFIED,
            reader_holds=[],
            seed=seed,
            mutated="MUTATED BY SABOTEUR",
            kind=pick["kind"],
            pick=pick,
        )
        if exercise["fully_correct"]:
            continue
        assert exercise["true_sentence"] == pick["sentence"]
        assert exercise["passage"] == pick["passage"]
        assert exercise["sentences"][exercise["planted_index"]] == "MUTATED BY SABOTEUR"


def test_non_content_classifier_flags_reuse_and_source_lines() -> None:
    from app.course.errorspot import is_content_text

    assert not is_content_text("Reuse notice: EUR-Lex legal texts are reusable.")
    assert not is_content_text("Source: Regulation (EU) 2024/1689, Official Journal")
    assert is_content_text("Providers shall ensure AI literacy of their staff.")


def test_mutations_work_for_nb_and_uk() -> None:
    from app.course.errorspot import mutate_claim

    nb = mutate_claim("Arbeidsgiveren skal sikre opplæring av ansatte.", kind="negation")
    assert "ikke" in nb
    uk = mutate_claim("Роботодавці повинні забезпечити навчання персоналу.", kind="negation")
    assert uk != "Роботодавці повинні забезпечити навчання персоналу."


# --- LOW: language tie, stop marker, schemas ----------------------------------


def test_language_tie_is_unknown_not_en() -> None:
    from app.services.library import detect_language

    assert detect_language(" the og ") == "unknown"


def test_stop_marker_is_a_plain_sentence() -> None:
    from app.runs.executor import safe_stop_marker

    marker = safe_stop_marker(
        "stopped at architect:plan: ValidationError: 1 validation error ('modules',)"
    )
    assert "ValidationError" not in marker
    assert "modules" not in marker
    assert marker.endswith(".")


def test_architect_schema_enums_equal_registries() -> None:
    from app.agents.base import AGENT_SCHEMAS
    from app.blocks.registry import BLOCK_TYPES
    from app.course.errorspot import MUTATION_KINDS
    from app.course.tree import ARCHITECT_SCHEMA

    block_schema = ARCHITECT_SCHEMA["properties"]["modules"]["items"]["properties"]["topics"][
        "items"
    ]["properties"]["blocks"]["items"]["properties"]
    assert block_schema["block_type"]["enum"] == sorted(BLOCK_TYPES)
    assert block_schema["role"]["enum"] == ["teach", "example", "check", "practice", "test"]
    saboteur = AGENT_SCHEMAS["saboteur"]
    assert saboteur["properties"]["kind"]["enum"] == sorted(MUTATION_KINDS)


def test_startup_check_covers_every_agent_and_block_schema() -> None:
    from app.agents.base import AGENT_SCHEMAS, validate_agents
    from app.course.tree import BLOCK_GENERATOR_SCHEMAS
    from app.llm.schema import validate_strict_subset

    validate_agents()
    assert len(AGENT_SCHEMAS) >= 12
    for block_type, schema in BLOCK_GENERATOR_SCHEMAS.items():
        validate_strict_subset(schema, name=f"generator:{block_type}")


def test_frozen_plan_skeleton_carries_no_mutable_state() -> None:
    from app.services.planruns import frozen_plan

    tree = {
        "modules": [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "plan_node_id": "m1",
                "title": "M",
                "topics": [
                    {
                        "id": "22222222-2222-2222-2222-222222222222",
                        "plan_node_id": "t1",
                        "kind": "lesson",
                        "title": "T",
                        "blocks": [
                            {
                                "id": "33333333-3333-3333-3333-333333333333",
                                "plan_node_id": "b1",
                                "role": "teach",
                                "block_type": "reading",
                                "brief": "Teach it",
                                "status": "held",
                                "hold_reason": "bad",
                                "source_handles": ["P1"],
                            }
                        ],
                    }
                ],
            }
        ]
    }
    frozen = frozen_plan(tree)
    text = str(frozen)
    assert "held" not in text and "bad" not in text
    assert not UUID_RE.search(text)
    block = frozen["modules"][0]["topics"][0]["blocks"][0]
    assert block == {
        "plan_node_id": "b1",
        "role": "teach",
        "block_type": "reading",
        "brief": "Teach it",
        "source_handles": ["P1"],
    }


def test_generation_handlers_registered_and_digest_sees_prompt() -> None:
    """Mutant kills: handlers in plan_handlers; digest changes with prompt."""
    from app.runs.executor import step_digest
    from app.services.planruns import plan_handlers

    handlers = plan_handlers()
    for name in ("generate_block", "read_topic", "read_continuity", "rework_blocks", "saboteur"):
        assert name in handlers, name
    assert step_digest(agent="reader", prompt="alpha") != step_digest(agent="reader", prompt="beta")


def test_uuid_dependency_rejects_malformed_ids() -> None:
    from app.errors import NotFoundError
    from app.routes.ids import parse_uuid_or_404

    with pytest.raises(NotFoundError):
        parse_uuid_or_404("not-a-uuid")
    assert str(parse_uuid_or_404("12345678-1234-1234-1234-123456789012")).startswith("12345678")


# --- 12a: submit guards ---------------------------------------------------------


async def _actor(session: AsyncSession) -> tuple[Any, UUID]:
    import uuid as _uuid

    from app.rbac.principal import Principal

    user_id = UUID(
        str(
            (
                await session.execute(
                    sa.text(
                        "INSERT INTO users (email, display_name) VALUES (:e, 'N') RETURNING id"
                    ),
                    {"e": f"t05c-{_uuid.uuid4().hex[:10]}@example.com"},
                )
            ).one()[0]
        )
    )
    actor = Principal(
        user_id=user_id,
        email="t05c@example.com",
        roles=("author",),
        permissions=frozenset({"course.plan", "course.generate"}),
        scope_tags=(),
        is_superadmin=False,
    )
    return actor, user_id


async def test_submit_plan_and_generate_refuse_empty_course(t03_session: AsyncSession) -> None:
    from app.errors import UnprocessableError
    from app.services import courses as course_service
    from app.services import planruns

    actor, user_id = await _actor(t03_session)
    course_id = await course_service.create_course(
        t03_session, owner_id=user_id, title="Empty", language="en"
    )
    await course_service.set_profile(
        t03_session,
        course_id=course_id,
        methodology_codes=["direct_instruction"],
        bloom_level="apply",
        audience="staff",
    )
    await t03_session.commit()
    with pytest.raises(UnprocessableError) as plan_exc:
        await planruns.submit_plan(t03_session, actor=actor, course_id=course_id, queries=["q"])
    assert "source" in str(plan_exc.value).lower()
    with pytest.raises(UnprocessableError):
        await planruns.submit_generate(t03_session, actor=actor, course_id=course_id)
    calls = (await t03_session.execute(sa.text("SELECT count(*) FROM llm_calls"))).scalar()
    assert int(calls or 0) == 0


# --- DB integration through the compiled DAG -----------------------------------


def _mock_handler(hold_first_reader: bool = True) -> Any:
    """Mock transport: verbatim quotes; first reader pass holds b2, re-read passes."""
    import json as _json

    import demo_t05
    import httpx2

    state = {"reader_calls": 0}
    base = demo_t05.mock_demo_handler()

    def handler(request: Any) -> Any:
        body = _json.loads(request.content.decode("utf-8"))
        prompt = str(body.get("input", ""))
        if prompt.startswith("# Reader"):
            state["reader_calls"] += 1
            if hold_first_reader and state["reader_calls"] == 1:
                import demo_t05 as _d

                section = _d._section(prompt, "Block skeleton")  # noqa: SLF001
                from app.agents.reader import RUBRIC_ITEMS

                reply = {"verdicts": []}
                for block in section["blocks"]:
                    verdict = "hold" if block["plan_node_id"] == "b2" else "pass"
                    reply["verdicts"].append(
                        {
                            "plan_node_id": block["plan_node_id"],
                            "verdict": verdict,
                            "items": {
                                item: {
                                    "verdict": "hold"
                                    if (verdict == "hold" and item == "answer_key")
                                    else "pass",
                                    "evidence": f"checked {item}",
                                }
                                for item in RUBRIC_ITEMS
                            },
                        }
                    )
                return httpx2.Response(200, json=demo_t05._completed(_json.dumps(reply)))  # noqa: SLF001
        return base(request)

    handler.state = state  # type: ignore[attr-defined]
    return handler


async def _drive_mock(t03_session: AsyncSession, run_id: UUID, handler: Any) -> dict[str, Any]:
    from conftest import test_database_url as _db_url
    from transport_guard import guarded_client

    from app.config import Settings
    from app.db import create_session_factory
    from app.providers.llm import MetaProvider
    from app.runs import executor

    settings = Settings(
        _env_file=None,
        model_api_key="synthetic-test-key",
        model_api_base_url="https://api.meta.ai/v1",
        llm_spend_ceiling_usd=50.0,
        source_store_path="/tmp/muse-t05c",
    )
    import httpx2

    factory = create_session_factory(_db_url())
    http = guarded_client(settings, inner=httpx2.MockTransport(handler))

    def make(session: AsyncSession) -> MetaProvider:
        return MetaProvider(session, settings, http, factory)

    return await executor.drive_run(
        factory,
        run_id=run_id,
        provider_factory=make,
        handlers=__import__("demo_t05").generation_handlers(),
    )


async def _snapshot_course(session: AsyncSession, course_id: UUID) -> dict[str, Any]:
    blocks = (
        await session.execute(
            sa.text(
                "SELECT b.plan_node_id, b.status, b.hold_reason,"
                " b.current_version_id, b.created_at"
                " FROM blocks b JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :c ORDER BY b.plan_node_id"
            ),
            {"c": str(course_id)},
        )
    ).all()
    citations = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM citations c"
                " JOIN block_versions bv ON bv.id = c.block_version_id"
                " JOIN blocks b ON b.id = bv.block_id JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id WHERE m.course_id = :c"
            ),
            {"c": str(course_id)},
        )
    ).scalar()
    versions = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM block_versions bv JOIN blocks b ON b.id = bv.block_id"
                " JOIN topics t ON t.id = b.topic_id JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :c"
            ),
            {"c": str(course_id)},
        )
    ).scalar()
    return {
        "blocks": [(str(r[0]), str(r[1]), r[2], str(r[3]), str(r[4])) for r in blocks],
        "citations": int(citations or 0),
        "versions": int(versions or 0),
    }


async def test_two_courses_sharing_ids_and_second_run_free(t03_session: AsyncSession) -> None:
    """H1/H2/H4/M3/M5: B generates, A untouched, reader sees full blocks, rerun pays 0."""
    import demo_t05

    from app.config import Settings

    settings = Settings(
        _env_file=None,
        model_api_key="synthetic-test-key",
        source_store_path="/tmp/muse-t05c",
        llm_spend_ceiling_usd=50.0,
    )
    # Course A: same plan ids, hand-written tree, one version per block.
    demo_a = await demo_t05.setup_demo(
        t03_session, settings=settings, email=demo_t05.fresh_email("t05c-a")
    )
    await demo_t05.write_demo_tree(t03_session, course_id=demo_a["course_id"])
    # Course B: the generated one.
    demo_b = await demo_t05.setup_demo(
        t03_session, settings=settings, email=demo_t05.fresh_email("t05c-b")
    )
    await demo_t05.write_demo_tree(t03_session, course_id=demo_b["course_id"])
    before_a = await _snapshot_course(t03_session, demo_a["course_id"])

    planted = demo_t05.plant_spot_error(demo_b["passages"])
    dag = demo_t05.demo_dag(
        course_id=demo_b["course_id"],
        tree=demo_t05.demo_tree(),
        sources=demo_t05.demo_sources(demo_b["passages"]),
        dossier=demo_t05.demo_dossier(),
        planted=planted,
    )
    from app.runs import executor

    run_id = await executor.new_run(
        t03_session, course_id=demo_b["course_id"], kind="generation", dag=dag
    )
    await t03_session.commit()
    handler = _mock_handler()
    final = await _drive_mock(t03_session, run_id, handler)
    assert final["status"] == "done", [
        (s["step_key"], s["status"], s["error"]) for s in final["steps"]
    ]

    after_a = await _snapshot_course(t03_session, demo_a["course_id"])
    assert after_a == before_a, "course A must be untouched by B's run"

    rows = (
        await t03_session.execute(
            sa.text(
                "SELECT b.plan_node_id, b.block_type, b.status, bv.content, bv.answer_key"
                " FROM blocks b JOIN block_versions bv ON bv.id = b.current_version_id"
                " JOIN topics t ON t.id = b.topic_id JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :c ORDER BY b.plan_node_id"
            ),
            {"c": str(demo_b["course_id"])},
        )
    ).all()
    assert [r[0] for r in rows] == ["b1", "b2", "b3", "b4"]
    assert all(r[2] != "draft" for r in rows), rows
    by_node = {str(r[0]): r for r in rows}
    choice = dict(by_node["b2"][3] or {})
    assert choice.get("options"), f"choice stored per type, got {choice}"
    assert "planted_index" not in str(by_node["b4"][3])
    # reader requests carry the FULL learner content and the key
    reader_reqs = (
        await t03_session.execute(
            sa.text("SELECT request FROM llm_calls WHERE run_id = :r AND agent = 'reader'"),
            {"r": str(run_id)},
        )
    ).all()
    assert reader_reqs
    joined = " ".join(str((r[0] or {}).get("input", "")) for r in reader_reqs)
    assert "Yes" in joined, "choice options must reach the reader"
    assert "planted_index" in joined, "the planted index must reach the reader"
    _m = UUID_RE.search(joined)
    assert not _m, (
        f"UUID in reader prompt: {joined[max(0, _m.start() - 120) : _m.end() + 120] if _m else ''}"
    )
    prompts = (
        await t03_session.execute(
            sa.text("SELECT request FROM llm_calls WHERE run_id = :r"), {"r": str(run_id)}
        )
    ).all()
    assert not any(UUID_RE.search(str((r[0] or {}).get("input", ""))) for r in prompts), (
        "no UUID in any prompt"
    )
    # key == planted source
    key = dict(by_node["b4"][4] or {})
    content = dict(by_node["b4"][3] or {})
    if not key.get("fully_correct"):
        assert content["sentences"][int(key["planted_index"])] == key["planted_sentence"]
        passage_texts = [p["text"] for p in demo_b["passages"]]
        assert any(key["true_sentence"] in text for text in passage_texts), key
    # learner renders carry no UUID
    from app.blocks.registry import learner_serializers

    render = learner_serializers()["card"]
    for _node, btype, _status, block_content, block_key in rows:
        rendered = render(str(btype), dict(block_content or {}), dict(block_key or {}))
        assert not UUID_RE.search(str(rendered)), (btype, rendered)

    # mutant kills on the product path: wave B saw wave A text, the
    # reader saw the sources, rework re-versioned the held block, and
    # the answer key never merged into learner content.
    gen_inputs = [
        str((r[0] or {}).get("input", ""))
        for r in (
            await t03_session.execute(
                sa.text(
                    "SELECT request FROM llm_calls WHERE run_id = :r AND agent = 'block_check'"
                ),
                {"r": str(run_id)},
            )
        ).all()
    ]
    assert gen_inputs and any(
        "Staff apply the cited passage at work." in text for text in gen_inputs
    ), "wave B prompt must contain wave A text"
    assert any("AI literacy" in str((r[0] or {}).get("input", "")) for r in reader_reqs), (
        "reader prompt must carry the sources"
    )
    assert "correct" not in str(by_node["b2"][3]), "key must not merge into choice content"
    b2_versions = (
        await t03_session.execute(
            sa.text(
                "SELECT count(*) FROM block_versions bv JOIN blocks b ON b.id = bv.block_id"
                " JOIN topics t ON t.id = b.topic_id JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :c AND b.plan_node_id = 'b2'"
            ),
            {"c": str(demo_b["course_id"])},
        )
    ).scalar()
    assert int(b2_versions or 0) >= 3, f"rework must re-version b2, got {b2_versions}"
    assert by_node["b2"][2] == "accepted", by_node["b2"]

    # second identical run: 0 paid calls through the compiled DAG
    run2 = await executor.new_run(
        t03_session, course_id=demo_b["course_id"], kind="generation", dag=dag
    )
    await t03_session.commit()
    final2 = await _drive_mock(t03_session, run2, _mock_handler(hold_first_reader=False))
    assert final2["status"] == "done", final2
    paid2 = (
        await t03_session.execute(
            sa.text("SELECT count(*) FROM llm_calls WHERE run_id = :r AND cost_usd > 0"),
            {"r": str(run2)},
        )
    ).scalar()
    assert int(paid2 or 0) == 0, f"second run paid {paid2}"


async def test_rework_exception_marks_step_failed_and_stops_run(
    t03_session: AsyncSession,
) -> None:
    """M1: a raising rework leaves no step running and the run resumable."""
    import demo_t05

    from app.config import Settings
    from app.runs import executor

    settings = Settings(
        _env_file=None,
        model_api_key="synthetic-test-key",
        source_store_path="/tmp/muse-t05c",
        llm_spend_ceiling_usd=50.0,
    )
    demo = await demo_t05.setup_demo(
        t03_session, settings=settings, email=demo_t05.fresh_email("t05c-m1")
    )
    await demo_t05.write_demo_tree(t03_session, course_id=demo["course_id"])
    gen_entry = {
        "step_key": "block_check:b2",
        "handler": "generate_block",
        "depends_on": [],
        "course_id": str(demo["course_id"]),
        "course_language": "en",
        "block_type": "choice",
        "sources": demo_t05.demo_sources(demo["passages"]),
        "skeleton": {"plan_node_id": "b2", "brief": "Who must ensure AI literacy?"},
        "plan": {},
        "dossier": {},
    }
    dag = [
        {
            "step_key": "rework:named-blocks",
            "handler": "rework_blocks",
            "depends_on": [],
            "named": ["b2"],
            "reasons": {"b2": "bad key"},
        },
        gen_entry,
    ]
    run_id = await executor.new_run(
        t03_session, course_id=demo["course_id"], kind="generation", dag=dag
    )
    await t03_session.commit()

    def raising_factory(session: AsyncSession) -> Any:
        class Boom:
            async def generate_json(self, **kwargs: Any) -> Any:
                raise RuntimeError("provider exploded")

        return Boom()

    final = await executor.drive_run(
        __import__("app.db", fromlist=["create_session_factory"]).create_session_factory(
            __import__("conftest").test_database_url()
        ),
        run_id=run_id,
        provider_factory=raising_factory,
        handlers={
            "generate_block": executor.generate_block_step,
            "rework_blocks": executor.rework_blocks_step,
        },
    )
    assert final["status"] == "stopped", final["status"]
    steps = (
        await t03_session.execute(
            sa.text("SELECT step_key, status FROM run_steps WHERE run_id = :r"), {"r": str(run_id)}
        )
    ).all()
    assert all(str(s[1]) != "running" for s in steps), steps
    by_key = {str(s[0]): str(s[1]) for s in steps}
    assert by_key["block_check:b2"] == "failed", by_key
    assert by_key["rework:named-blocks"] == "failed", by_key
    run_status = (
        await t03_session.execute(
            sa.text("SELECT status FROM runs WHERE id = :r"), {"r": str(run_id)}
        )
    ).scalar()
    assert run_status == "stopped"


async def test_rewrite_operation_regenerates_only_named_blocks(
    t03_session: AsyncSession,
) -> None:
    """M2: rewrite_block re-versions only the named block, variant > 0."""
    import demo_t05

    from app.config import Settings
    from app.services import planruns

    settings = Settings(
        _env_file=None,
        model_api_key="synthetic-test-key",
        source_store_path="/tmp/muse-t05c",
        llm_spend_ceiling_usd=50.0,
    )
    demo = await demo_t05.setup_demo(
        t03_session, settings=settings, email=demo_t05.fresh_email("t05c-m2")
    )
    await demo_t05.write_demo_tree(t03_session, course_id=demo["course_id"])
    before = (
        await t03_session.execute(
            sa.text(
                "SELECT b.plan_node_id, b.id, b.current_version_id FROM blocks b"
                " JOIN topics t ON t.id = b.topic_id JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :c ORDER BY b.plan_node_id"
            ),
            {"c": str(demo["course_id"])},
        )
    ).all()
    b2_id = str(next(r[1] for r in before if r[0] == "b2"))
    op_id = (
        await t03_session.execute(
            sa.text(
                "INSERT INTO operations (kind, status, params, progress, actor_id)"
                " VALUES ('rewrite_block', 'queued', CAST(:p AS jsonb), 0, :a) RETURNING id"
            ),
            {
                "p": f'{{"course_id": "{demo["course_id"]}", "block_id": "{b2_id}"}}',
                "a": str(demo["user_id"]),
            },
        )
    ).one()[0]
    await t03_session.commit()

    from conftest import test_database_url as _db_url

    from app.db import create_session_factory

    factory = create_session_factory(_db_url())
    handler = _mock_handler(hold_first_reader=False)

    import httpx2
    from transport_guard import guarded_client

    from app.providers.llm import MetaProvider

    http = guarded_client(settings, inner=httpx2.MockTransport(handler))

    def make(session: AsyncSession) -> MetaProvider:
        return MetaProvider(session, settings, http, factory)

    result = await planruns.run_generate_operation(
        t03_session, UUID(str(op_id)), provider_factory=make, session_factory=factory
    )
    after = (
        await t03_session.execute(
            sa.text(
                "SELECT b.plan_node_id, b.current_version_id FROM blocks b"
                " JOIN topics t ON t.id = b.topic_id JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :c ORDER BY b.plan_node_id"
            ),
            {"c": str(demo["course_id"])},
        )
    ).all()
    before_map = {str(r[0]): str(r[2]) for r in before}
    after_map = {str(r[0]): str(r[1]) for r in after}
    assert after_map["b2"] != before_map["b2"], "named block must be re-versioned"
    for node in ("b1", "b3", "b4"):
        assert after_map[node] == before_map[node], f"{node} must keep its version"
    variants = (
        await t03_session.execute(
            sa.text("SELECT variant FROM llm_calls WHERE run_id = :r AND agent LIKE 'block_%'"),
            {"r": result["run_id"]},
        )
    ).all()
    assert variants and all(int(v[0]) > 0 for v in variants), variants


async def test_rejected_reply_is_never_stored_or_replayed(t03_session: AsyncSession) -> None:
    """12b/c: reject once, run again, exactly one new paid call each time."""
    import httpx2
    from conftest import test_database_url as _db_url

    from app.db import create_session_factory
    from app.providers.llm import MetaProvider

    factory = create_session_factory(_db_url())
    calls = {"n": 0}

    def handler(request: Any) -> Any:
        calls["n"] += 1
        return httpx2.Response(200, json=_completed('{"modules": []}'))

    def _completed(text: str) -> dict[str, Any]:
        return {
            "id": "resp_1",
            "object": "response",
            "created_at": 1761878400,
            "status": "completed",
            "model": "muse-wonder-1.0",
            "output": [
                {
                    "type": "message",
                    "id": "m",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": text, "annotations": []}],
                }
            ],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "input_tokens_details": [{"cached_tokens": 0}],
                "output_tokens_details": [{"reasoning_tokens": 0}],
                "total_tokens": 15,
            },
        }

    from app.config import Settings

    settings = Settings(
        _env_file=None,
        model_api_key="synthetic-test-key",
        model_api_base_url="https://api.meta.ai/v1",
        llm_spend_ceiling_usd=50.0,
    )
    http = httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler), base_url="https://api.meta.ai/v1"
    )
    async with factory() as session:
        provider = MetaProvider(session, settings, http, factory)
        from app.agents import architect
        from app.course.methodologies import prescription_for
        from app.errors import ValidationError

        prescription = prescription_for(["direct_instruction"])
        for _ in range(2):
            with pytest.raises(ValidationError):
                await provider.generate_json(
                    agent="architect",
                    prompt="plan please",
                    schema=architect.schema(),
                    run_id=None,
                    step_id=None,
                    check=lambda reply: __import__(
                        "app.runs.executor", fromlist=["_rejectable_architect_errors"]
                    )._rejectable_architect_errors(reply, prescription, {"P1"}),
                )
    assert calls["n"] == 2, "each rejected reply must be paid again, never replayed"
    stored = (await t03_session.execute(sa.text("SELECT count(*) FROM llm_replies"))).scalar()
    assert int(stored or 0) == 0


def test_every_openapi_id_route_uses_the_uuid_dependency() -> None:
    """13: every {x_id} path param in the spec is parsed by ids.py."""
    import inspect

    from app.main import create_app
    from app.routes import ids

    _ = create_app(sync_on_startup=False)
    from app.routes.ops import operations_router
    from app.routes.t02 import invites_router, notifications_router, users_router
    from app.routes.t03 import blocks_router, courses_router, runs_router, sources_router

    all_routes = [
        route
        for router in (
            courses_router,
            sources_router,
            runs_router,
            blocks_router,
            operations_router,
            users_router,
            invites_router,
            notifications_router,
        )
        for route in router.routes
    ]
    checked = 0
    for route in all_routes:
        path = getattr(route, "path", "")
        for param in re.findall(r"\{(\w+)\}", path):
            if not param.endswith("_id"):
                continue
            dependant = getattr(route, "dependant", None)
            if dependant is None:
                continue
            sources = [getattr(dep.call, "__module__", "") for dep in dependant.dependencies]
            assert ids.__name__ in sources, (path, param, sources)
            checked += 1
    assert checked >= 10, checked
    _ = inspect


async def test_frozen_handles_come_from_plan_version_not_current(
    t03_session: AsyncSession,
) -> None:
    """Live H2 regression: generation replaces the current version with
    content that carries no source_handles; the frozen plan must stay
    identical (handles from the plan version), or run 2 pays again."""
    import demo_t05

    from app.config import Settings
    from app.services import courses as course_service
    from app.services.planruns import frozen_plan

    settings = Settings(
        _env_file=None,
        model_api_key="synthetic-test-key",
        source_store_path="/tmp/muse-t05c",
        llm_spend_ceiling_usd=50.0,
    )
    demo = await demo_t05.setup_demo(
        t03_session, settings=settings, email=demo_t05.fresh_email("frozen")
    )
    await demo_t05.write_demo_tree(t03_session, course_id=demo["course_id"])
    before = frozen_plan(await course_service.read_tree(t03_session, course_id=demo["course_id"]))
    # Simulate generation: a new current version whose content has no handles.
    await t03_session.execute(
        sa.text(
            "INSERT INTO block_versions (block_id, version_no, content, answer_key,"
            ' schema_version) SELECT b.id, 99, CAST(\'{"body": "generated"}\' AS jsonb),'
            " NULL, '1' FROM blocks b JOIN topics t ON t.id = b.topic_id"
            " JOIN modules m ON m.id = t.module_id WHERE m.course_id = :c"
        ),
        {"c": str(demo["course_id"])},
    )
    await t03_session.execute(
        sa.text(
            "UPDATE blocks b SET current_version_id = bv.id FROM block_versions bv"
            " WHERE bv.block_id = b.id AND bv.version_no = 99 AND b.topic_id IN"
            " (SELECT t.id FROM topics t JOIN modules m ON m.id = t.module_id"
            " WHERE m.course_id = :c)"
        ),
        {"c": str(demo["course_id"])},
    )
    await t03_session.commit()
    after = frozen_plan(await course_service.read_tree(t03_session, course_id=demo["course_id"]))
    assert after == before
    handles = [
        b["source_handles"] for m in after["modules"] for t in m["topics"] for b in t["blocks"]
    ]
    assert handles and all(h == ["P1"] for h in handles), handles
