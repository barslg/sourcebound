"""T03 live test: one real plan on a 3-page synthetic source.

Runs only with MODEL_API_KEY (marked `live`, skipped otherwise):
the researcher ranks real passages and the architect writes a plan
where every block carries at least one source ref resolving to a
passage. Synthetic text only, never real user data.
"""

from __future__ import annotations

import os
import uuid
from uuid import UUID

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.live

LIVE_TEXT = (
    "Synthetic Act, page one. The literacy duty belongs to providers and deployers.\n\n"
    "Staff who operate AI systems must complete basic training each year.\n\n"
    "Municipal registries log every high-risk use with its legal basis.\f"
    "Synthetic Act, page two. Human oversight is required before any automated refusal.\n\n"
    "Oversight decisions are recorded with the reviewer name and date.\f"
    "Synthetic Act, page three. Fines follow Article 99 ceilings for serious breaches.\n\n"
    "Minor breaches lead to warnings and supervised correction plans."
)


async def _maybe_key() -> str | None:
    return os.environ.get("MODEL_API_KEY")


async def test_live_plan_on_synthetic_source(t03_session) -> None:  # type: ignore[no-untyped-def]
    from conftest import test_database_url
    from t02_helpers import make_session_factory

    from app.config import Settings
    from app.db import create_session_factory
    from app.providers.llm import MetaProvider
    from app.runs import executor
    from app.services import courses as course_service
    from app.services import library, planruns

    key = await _maybe_key()
    if not key:
        pytest.skip("MODEL_API_KEY is not set; live plan test skipped")
    settings = Settings(
        _env_file=None, model_api_key=key, source_store_path="/tmp/muse-t03-sources"
    )
    user_id = UUID(
        str(
            (
                await t03_session.execute(
                    sa.text(
                        "INSERT INTO users (email, display_name) VALUES (:e, 'N') RETURNING id"
                    ),
                    {"e": f"live-{uuid.uuid4().hex[:12]}@example.com"},
                )
            ).one()[0]
        )
    )
    course_id = await course_service.create_course(
        t03_session, owner_id=user_id, title="Synthetic literacy", language="en"
    )
    await course_service.set_profile(
        t03_session,
        course_id=course_id,
        methodology_codes=["direct_instruction"],
        bloom_level="understand",
        audience="municipal staff",
    )
    source_id = await library.store_upload(
        t03_session,
        settings=settings,
        created_by=user_id,
        filename="synthetic.txt",
        media="text",
        content=LIVE_TEXT.encode("utf-8"),
    )
    await library.ingest_passages(
        t03_session,
        source_id=source_id,
        passages=library.chunk_text(LIVE_TEXT, kind="pdf"),
        ts_config="english",
    )
    await library.approve_source(t03_session, source_id=source_id, approved_by=user_id)
    await t03_session.commit()

    from app.rbac.principal import Principal

    actor = Principal(
        user_id=user_id,
        email="live@example.com",
        roles=("author",),
        permissions=frozenset({"run.start", "operation.run", "course.edit"}),
        scope_tags=(),
        is_superadmin=False,
    )
    operation_id = await planruns.submit_plan(
        t03_session,
        actor=actor,
        course_id=course_id,
        queries=["Who owns the AI literacy duty?"],
        defer=None,
    )
    await t03_session.commit()
    detail = await planruns.run_plan_operation(t03_session, operation_id)
    run_id = UUID(str(detail["run_id"]))
    url = test_database_url()
    ledger = create_session_factory(url)

    def make(session):  # type: ignore[no-untyped-def]
        return MetaProvider(session, settings, None, ledger)

    final = await executor.drive_run(
        make_session_factory(url),
        run_id=run_id,
        provider_factory=make,
        handlers={
            "researcher": executor.plan_researcher_step,
            "architect": executor.plan_architect_step,
        },
    )
    assert final["status"] == "done"
    tree = await course_service.read_tree(t03_session, course_id=course_id)
    blocks = [
        block
        for module in tree["modules"]
        for topic in module["topics"]
        for block in topic["blocks"]
    ]
    assert blocks, "the live plan must contain blocks"
    passage_count = (await t03_session.execute(sa.text("SELECT count(*) FROM passages"))).scalar()
    assert passage_count and passage_count >= 3
    for step in final["steps"]:
        assert step["status"] == "done"
    architect_output = (
        await t03_session.execute(
            sa.text(
                "SELECT output FROM run_steps WHERE run_id = :run AND step_key = 'architect:plan'"
            ),
            {"run": str(run_id)},
        )
    ).scalar_one()
    plan_blocks = [
        block
        for module in architect_output["modules"]
        for topic in module["topics"]
        for block in topic["blocks"]
    ]
    assert plan_blocks
    for block in plan_blocks:
        assert block["source_refs"], f"block {block['plan_node_id']} has no source ref"
        for ref in block["source_refs"]:
            exists = (
                await t03_session.execute(
                    sa.text("SELECT 1 FROM passages WHERE id = :passage"),
                    {"passage": ref["passage_id"]},
                )
            ).first()
            assert exists, f"block {block['plan_node_id']} cites a missing passage"
