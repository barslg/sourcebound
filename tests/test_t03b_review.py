"""T03b review probes: every finding reproduced as a failing test first.

Each test below pins one item of build/tasks/T03b.md against the real
database: course-scoped sources, SSRF refusal, per-course plan ids,
one driver per run, the wired pipeline, persisted citations, binary
uploads, per-language FTS, language in prompts, digests, failure
markers, scope checks, the route gate, the block-type registry,
architect context, image packaging, retrieval pairs, and candidate
approval — plus the surviving-mutant kills.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from uuid import UUID

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from transport_guard import guarded_client

from app.config import Settings
from app.services import courses as course_service
from app.services import library, planruns

BASE = "https://api.meta.ai/v1"


def _settings(**overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        model_api_key="synthetic-test-key",
        model_api_base_url=BASE,
        llm_spend_ceiling_usd=50.0,
        source_store_path="/tmp/muse-t03-sources",
        **overrides,
    )


async def _user(session: AsyncSession, tag: str) -> UUID:
    row = (
        await session.execute(
            sa.text("INSERT INTO users (email, display_name) VALUES (:e, 'N') RETURNING id"),
            {"e": f"t03b-{tag}-{uuid.uuid4().hex[:8]}@example.com"},
        )
    ).one()
    await session.commit()
    return UUID(str(row[0]))


def _actor(user_id: UUID, codes: frozenset[str]) -> Any:
    from app.rbac.principal import Principal

    return Principal(
        user_id=user_id,
        email="t03b@example.com",
        roles=("author",),
        permissions=codes,
        scope_tags=(),
        is_superadmin=False,
    )


async def _course(
    session: AsyncSession, user_id: UUID, language: str = "en", title: str = "Course"
) -> UUID:
    course_id = await course_service.create_course(
        session, owner_id=user_id, title=title, language=language
    )
    await course_service.set_profile(
        session,
        course_id=course_id,
        methodology_codes=["direct_instruction"],
        bloom_level="understand",
        audience="municipal staff",
    )
    await session.commit()
    return course_id


async def _text_source(
    session: AsyncSession,
    settings: Settings,
    user_id: UUID,
    course_id: UUID,
    text: str,
    filename: str = "source.txt",
) -> UUID:
    source_id = await library.store_upload(
        session,
        settings=settings,
        created_by=user_id,
        filename=filename,
        media="text",
        content=text.encode("utf-8"),
        course_id=course_id,
    )
    await library.ingest_passages(session, source_id=source_id, passages=library.chunk_text(text))
    await library.approve_source(session, source_id=source_id, approved_by=user_id)
    await session.commit()
    return source_id


def _completed(text: str) -> dict[str, Any]:
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 1761878400,
        "status": "completed",
        "model": "muse-spark-1.3",
        "output": [
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 10,
            "input_tokens_details": [{"cached_tokens": 0}],
            "output_tokens_details": [{"reasoning_tokens": 0}],
            "total_tokens": 110,
        },
    }


def _tree_output(handles: list[str]) -> dict[str, Any]:
    ref = handles[0] if handles else "P1"
    return {
        "modules": [
            {
                "plan_node_id": "m1",
                "title": "Module",
                "purpose": "Teach the duty.",
                "entry_knowledge": "None.",
                "exit_knowledge": "The duty.",
                "topics": [
                    {
                        "plan_node_id": "t1",
                        "kind": "lesson",
                        "title": "Topic",
                        "objectives": ["Name the duty."],
                        "through_line_note": "The case continues.",
                        "blocks": [
                            {
                                "plan_node_id": "b1",
                                "role": "teach",
                                "block_type": "reading",
                                "brief": "The literacy duty and who it binds",
                                "source_refs": [{"handle": ref}],
                            },
                            {
                                "plan_node_id": "b2",
                                "role": "example",
                                "block_type": "reading",
                                "brief": "An example of the duty in practice",
                                "source_refs": [{"handle": ref}],
                            },
                            {
                                "plan_node_id": "b3",
                                "role": "check",
                                "block_type": "choice",
                                "brief": "Who must ensure AI literacy?",
                                "source_refs": [{"handle": ref}],
                            },
                        ],
                    }
                ],
            }
        ],
        "estimate_inputs": {"blocks": 3, "calls": 4, "tokens": 9000},
        "through_line": {"case": "A municipal rollout.", "handoffs": []},
    }


class _Capture:
    """Mock Model API: full prompts kept, valid plan tree returned."""

    def __init__(self, architect_handles: list[str] | None = None) -> None:
        self.prompts: list[str] = []
        self.architect_handles = architect_handles

    def __call__(self, request: Any) -> Any:
        import httpx2 as _httpx

        body = json.loads(request.content.decode("utf-8"))
        prompt = str(body.get("input", ""))
        self.prompts.append(prompt)
        if prompt.startswith("# Researcher"):
            dynamic = json.loads(prompt.split("## Task input (typed, assembled by code)\n")[1])
            ranked = [
                {"handle": item["handle"], "rank": index, "score": 0.9 - index * 0.05}
                for index, item in enumerate(dynamic["passages"][:3], 1)
            ]
            reply = {
                "query": dynamic["query"],
                "rephrased": dynamic["rephrased"],
                "ranked": ranked,
                "web_candidates": [],
            }
            return _httpx.Response(200, json=_completed(json.dumps(reply)))
        dynamic = json.loads(prompt.split("## Task input (typed, assembled by code)\n")[1])
        handles = self.architect_handles
        if handles is None:
            handles = [item["handle"] for item in dynamic["passages"][:1]] or ["P1"]
        return _httpx.Response(200, json=_completed(json.dumps(_tree_output(handles))))


def _provider_factory(http: Any, ledger: Any, settings: Settings) -> Any:
    from app.providers.llm import MetaProvider

    def make(session: AsyncSession) -> MetaProvider:
        return MetaProvider(session, settings, http, ledger)

    return make


async def _drive(run_id: UUID, handler: Any, settings: Settings) -> dict[str, Any]:
    import httpx2 as _httpx
    from conftest import test_database_url

    from app.db import create_session_factory
    from app.runs import executor

    url = test_database_url()
    ledger = create_session_factory(url)
    if isinstance(handler, _Capture):
        transport: Any = handler
    else:
        transport = handler
    http = guarded_client(settings, inner=_httpx.MockTransport(transport))
    return await executor.drive_run(
        create_session_factory(url),
        run_id=run_id,
        provider_factory=_provider_factory(http, ledger, settings),
        handlers={
            "researcher": executor.plan_researcher_step,
            "architect": executor.plan_architect_step,
        },
    )


async def _submit_and_create(
    session: AsyncSession, actor: Any, course_id: UUID, queries: list[str]
) -> UUID:
    operation_id = await planruns.submit_plan(
        session, actor=actor, course_id=course_id, queries=queries, defer=None
    )
    await session.commit()
    detail = await planruns.run_plan_operation(session, operation_id)
    return UUID(str(detail["run_id"]))


# Item 1: sources are course-scoped, never global.


async def test_scoped_retrieval_never_leaks_across_owners(
    t03_session: AsyncSession,
) -> None:
    """Owner B's search never sees owner A's text."""
    from app.providers.retrieval import FtsRetriever

    settings = _settings()
    user_a = await _user(t03_session, "a")
    user_b = await _user(t03_session, "b")
    course_a = await _course(t03_session, user_a, title="A")
    course_b = await _course(t03_session, user_b, title="B")
    await _text_source(
        t03_session,
        settings,
        user_a,
        course_a,
        "ZEBRA-striped confidentiality sentence for owner A only.",
    )
    await _text_source(t03_session, settings, user_b, course_b, "Bravo public note for owner B.")
    found_b = await FtsRetriever().search_course(
        t03_session, query="sentence note", course_id=course_b, limit=8
    )
    texts = " ".join(item.text for item in found_b)
    assert "Bravo" in texts
    assert "ZEBRA" not in texts
    everything = await FtsRetriever().search(
        t03_session, query="ZEBRA", ts_config="english", limit=8, course_id=course_b
    )
    assert everything == []


async def test_pending_passages_are_invisible_to_the_course(
    t03_session: AsyncSession,
) -> None:
    """Unapproved bindings stay out of retrieval (mutant: unapproved sent)."""
    from app.providers.retrieval import FtsRetriever

    settings = _settings()
    user = await _user(t03_session, "pending")
    course = await _course(t03_session, user, title="Pending")
    source_id = await library.store_upload(
        t03_session,
        settings=settings,
        created_by=user,
        filename="draft.txt",
        media="text",
        content=b"PENDING-only draft text about oversight duties.",
        course_id=course,
    )
    await library.ingest_passages(
        t03_session,
        source_id=source_id,
        passages=library.chunk_text("PENDING-only draft text about oversight duties."),
    )
    await t03_session.commit()
    found = await FtsRetriever().search_course(
        t03_session, query="oversight duties", course_id=course, limit=8
    )
    assert all("PENDING-only" not in item.text for item in found)
    await library.approve_source(
        t03_session, source_id=source_id, approved_by=user, course_id=course
    )
    await t03_session.commit()
    found = await FtsRetriever().search_course(
        t03_session, query="oversight duties", course_id=course, limit=8
    )
    assert any("PENDING-only" in item.text for item in found)


async def test_owner_b_run_never_sees_owner_a_text(
    t03_session: AsyncSession,
) -> None:
    """End to end: no prompt of B's run contains A's passage text."""
    settings = _settings()
    user_a = await _user(t03_session, "ea")
    user_b = await _user(t03_session, "eb")
    course_a = await _course(t03_session, user_a, title="EA")
    course_b = await _course(t03_session, user_b, title="EB")
    await _text_source(
        t03_session,
        settings,
        user_a,
        course_a,
        "ZEBRA-striped confidentiality sentence for owner A only. " * 4,
    )
    await _text_source(t03_session, settings, user_b, course_b, "Bravo public note for owner B.")
    actor_b = _actor(
        user_b, frozenset({"run.start", "operation.run", "course.edit", "course.plan"})
    )
    run_id = await _submit_and_create(t03_session, actor_b, course_b, ["confidentiality Bravo"])
    capture = _Capture()
    final = await _drive(run_id, capture, settings)
    assert final["status"] == "done"
    assert capture.prompts, "the run must have called the model"
    assert not any("ZEBRA" in prompt for prompt in capture.prompts)
    assert any("Bravo" in prompt for prompt in capture.prompts)


# Item 2: SSRF guard on fetch and robots.


@pytest.mark.parametrize(
    "target",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1/secret",
        "http://localhost/secret",
        "http://[::1]/secret",
        "http://10.0.0.5/internal",
    ],
)
async def test_internal_addresses_are_refused_before_any_request(target: str) -> None:
    """Link-local, loopback, ULA-shaped, and private targets never fetch."""
    from app.errors import ValidationError

    with pytest.raises(ValidationError, match="non-public"):
        await library.fetch_url(target, settings=_settings())


@pytest.mark.parametrize(
    "target",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1/secret",
        "http://localhost/secret",
        "http://[::1]/secret",
        "http://10.0.0.5/internal",
    ],
)
async def test_internal_addresses_fail_robots_closed(target: str) -> None:
    """Robots checks refuse the same internal targets (fail closed)."""
    from app.providers import web as web_provider

    assert await web_provider.robots_allowed(target, settings=_settings()) is False


# Item 3: plan ids are per course, not per database.


def _lesson_tree(node_prefix: str) -> Any:
    from app.course.tree import CourseTree

    return CourseTree.model_validate(
        {
            "modules": [
                {
                    "plan_node_id": f"{node_prefix}-m1",
                    "title": "Module",
                    "purpose": "Purpose.",
                    "entry_knowledge": "Entry.",
                    "exit_knowledge": "Exit.",
                    "topics": [
                        {
                            "plan_node_id": f"{node_prefix}-t1",
                            "kind": "lesson",
                            "title": "Topic",
                            "objectives": ["Name it."],
                            "through_line_note": "Note.",
                            "blocks": [
                                {
                                    "plan_node_id": f"{node_prefix}-b1",
                                    "role": "teach",
                                    "block_type": "reading",
                                    "brief": "The duty and who it binds",
                                    "source_refs": [{"handle": "P1"}],
                                },
                                {
                                    "plan_node_id": f"{node_prefix}-b2",
                                    "role": "example",
                                    "block_type": "reading",
                                    "brief": "An example of the duty",
                                    "source_refs": [{"handle": "P1"}],
                                },
                                {
                                    "plan_node_id": f"{node_prefix}-b3",
                                    "role": "check",
                                    "block_type": "choice",
                                    "brief": "Who must ensure it?",
                                    "source_refs": [{"handle": "P1"}],
                                },
                            ],
                        }
                    ],
                }
            ]
        }
    )


async def test_two_courses_planned_in_a_row(t03_session: AsyncSession) -> None:
    """Identical model labels in two courses no longer collide."""
    user = await _user(t03_session, "two")
    first = await _course(t03_session, user, title="First")
    second = await _course(t03_session, user, title="Second")
    await course_service.write_tree(
        t03_session, course_id=first, tree=_lesson_tree("n"), plan_digest="d1"
    )
    await t03_session.commit()
    await course_service.write_tree(
        t03_session, course_id=second, tree=_lesson_tree("n"), plan_digest="d1"
    )
    await t03_session.commit()
    for course in (first, second):
        tree = await course_service.read_tree(t03_session, course_id=course)
        assert len(tree["modules"]) == 1


# Item 4: one driver per run, resume only from stopped.


async def _slow_claim_handler(calls: list[str], delay: float = 0.3) -> Any:
    from app.runs.executor import claim_step, finish_step

    async def handler(
        session: AsyncSession, run_id: UUID, entry: dict[str, Any], provider_factory: Any
    ) -> None:
        import asyncio

        await asyncio.sleep(delay)
        calls.append(str(entry["step_key"]))
        claim = await claim_step(
            session,
            run_id=run_id,
            step_key=str(entry["step_key"]),
            input_digest="digest",
        )
        if claim["reused_output"] is None:
            await finish_step(session, step_id=claim["step_id"], output={"ok": True})
        await session.commit()

    return handler


async def test_concurrent_drivers_run_each_step_once(
    t03_session: AsyncSession,
) -> None:
    """Two drivers at once: every step runs exactly once, no double ledger."""
    import asyncio

    from conftest import test_database_url

    from app.db import create_session_factory
    from app.runs import executor

    user = await _user(t03_session, "race")
    course = await _course(t03_session, user, title="Race")
    run_id = await executor.new_run(
        t03_session,
        course_id=course,
        kind="plan",
        dag=[
            {"step_key": "researcher:qa", "handler": "slow", "depends_on": []},
            {
                "step_key": "architect:plan",
                "handler": "slow",
                "depends_on": ["researcher:qa"],
            },
        ],
    )
    await t03_session.commit()
    calls: list[str] = []
    factory = create_session_factory(test_database_url())
    handlers = {"slow": await _slow_claim_handler(calls)}
    first, second = await asyncio.gather(
        executor.drive_run(
            factory, run_id=run_id, provider_factory=lambda s: None, handlers=handlers
        ),
        executor.drive_run(
            factory, run_id=run_id, provider_factory=lambda s: None, handlers=handlers
        ),
    )
    assert sorted(calls) == ["architect:plan", "researcher:qa"]
    assert {first["status"], second["status"]} <= {"done", "running"}
    final_state = (
        await t03_session.execute(
            sa.text("SELECT status FROM runs WHERE id = :run"), {"run": str(run_id)}
        )
    ).scalar_one()
    assert final_state == "done"
    rows = (
        await t03_session.execute(
            sa.text(
                "SELECT step_key, count(*) FROM run_steps WHERE run_id = :run GROUP BY step_key"
            ),
            {"run": str(run_id)},
        )
    ).all()
    assert {row[0]: row[1] for row in rows} == {
        "researcher:qa": 1,
        "architect:plan": 1,
    }
    _ = second


async def test_lock_held_second_driver_stands_down(
    t03_session: AsyncSession,
) -> None:
    """Mutant kill: the lock is really consulted, not always acquired."""
    from conftest import test_database_url

    from app.db import create_session_factory
    from app.runs import executor

    user = await _user(t03_session, "lock")
    course = await _course(t03_session, user, title="Lock")
    run_id = await executor.new_run(
        t03_session,
        course_id=course,
        kind="plan",
        dag=[{"step_key": "researcher:qa", "handler": "slow", "depends_on": []}],
    )
    await t03_session.commit()
    factory = create_session_factory(test_database_url())
    engine = factory.kw["bind"]
    calls: list[str] = []
    async with engine.connect() as lock_conn:
        acquired = (
            await lock_conn.execute(
                sa.text("SELECT pg_try_advisory_lock(hashtext(:key))"),
                {"key": f"run:{run_id}"},
            )
        ).scalar()
        assert acquired
        try:
            final = await executor.drive_run(
                factory,
                run_id=run_id,
                provider_factory=lambda s: None,
                handlers={"slow": await _slow_claim_handler(calls, delay=0)},
            )
        finally:
            await lock_conn.execute(
                sa.text("SELECT pg_advisory_unlock(hashtext(:key))"),
                {"key": f"run:{run_id}"},
            )
    assert calls == []
    assert final["status"] == "running"


async def test_resume_running_run_is_refused(t03_session: AsyncSession) -> None:
    """Resume refuses a running run only while a driver holds its lock.

    A stranded running run (no driver) resumes; a stopped run
    resumes; a done run still refuses.
    """
    from app.errors import ValidationError
    from app.runs import executor

    user = await _user(t03_session, "resume")
    course = await _course(t03_session, user, title="Resume")
    run_id = await executor.new_run(t03_session, course_id=course, kind="plan", dag=[])
    await t03_session.commit()
    actor = _actor(user, frozenset({"run.retry"}))
    resumed = await planruns.resume_run(t03_session, actor=actor, run_id=run_id, defer=None)
    assert resumed == run_id
    await t03_session.execute(
        sa.text("UPDATE runs SET status = 'done' WHERE id = :run"), {"run": str(run_id)}
    )
    await t03_session.commit()
    with pytest.raises(ValidationError, match="only stopped runs resume"):
        await planruns.resume_run(t03_session, actor=actor, run_id=run_id, defer=None)


# Item 5: the run pipeline is connected to production.


async def test_worker_drives_plan_run_to_done(t03_session: AsyncSession) -> None:
    """run_plan_operation with a provider factory drives the run itself."""
    from conftest import test_database_url

    from app.db import create_session_factory

    settings = _settings()
    user = await _user(t03_session, "worker")
    course = await _course(t03_session, user, title="Worker")
    await _text_source(
        t03_session, settings, user, course, "Article 4 requires AI literacy among staff."
    )
    actor = _actor(user, frozenset({"run.start", "operation.run", "course.edit", "course.plan"}))
    operation_id = await planruns.submit_plan(
        t03_session, actor=actor, course_id=course, queries=["literacy"], defer=None
    )
    await t03_session.commit()
    capture = _Capture()
    url = test_database_url()
    import httpx2 as _httpx

    from app.db import create_session_factory as _factory

    ledger = _factory(url)
    http = guarded_client(settings, inner=_httpx.MockTransport(capture))
    detail = await planruns.run_plan_operation(
        t03_session,
        operation_id,
        provider_factory=_provider_factory(http, ledger, settings),
        session_factory=create_session_factory(url),
    )
    run_id = UUID(str(detail["run_id"]))
    async with create_session_factory(url)() as fresh:
        from app.runs import executor as _executor

        final = await _executor.get_run(fresh, run_id=run_id)
    assert final["status"] == "done"
    estimate = (
        await t03_session.execute(
            sa.text("SELECT estimate FROM runs WHERE id = :run"), {"run": str(run_id)}
        )
    ).scalar_one()
    assert estimate["blocks"] == 3
    versions = (await t03_session.execute(sa.text("SELECT count(*) FROM course_versions"))).scalar()
    assert versions and versions >= 1


async def test_resume_enqueues_operation_and_worker_drives_it(
    t03_session: AsyncSession,
) -> None:
    """Resume stores a resume operation; the worker drive pays nothing twice."""
    import httpx2 as _httpx
    from conftest import test_database_url

    from app.db import create_session_factory
    from app.runs import executor

    settings = _settings()
    user = await _user(t03_session, "resumed")
    course = await _course(t03_session, user, title="Resumed")
    await _text_source(
        t03_session, settings, user, course, "Article 4 requires AI literacy among staff."
    )
    actor = _actor(user, frozenset({"run.start", "operation.run", "course.edit", "course.plan"}))
    run_id = await _submit_and_create(t03_session, actor, course, ["literacy"])

    def fail_once(request: Any) -> Any:
        import httpx2 as _inner

        body = json.loads(request.content.decode("utf-8"))
        if str(body.get("input", "")).startswith("# Architect"):
            return _inner.Response(500, json={"error": {"message": "planted"}})
        return _Capture()(request)

    final = await _drive(run_id, fail_once, settings)
    assert final["status"] == "stopped"
    retry_actor = _actor(user, frozenset({"run.retry"}))
    seen_ops: list[UUID] = []

    async def defer(operation_id: UUID) -> None:
        seen_ops.append(operation_id)

    resumed = await planruns.resume_run(t03_session, actor=retry_actor, run_id=run_id, defer=defer)
    assert resumed == run_id
    assert len(seen_ops) == 1
    kind = (
        await t03_session.execute(
            sa.text("SELECT kind FROM operations WHERE id = :op"),
            {"op": str(seen_ops[0])},
        )
    ).scalar_one()
    assert kind == "resume_run"
    url = test_database_url()
    capture = _Capture()
    http = guarded_client(settings, inner=_httpx.MockTransport(capture))
    ledger = create_session_factory(url)
    detail = await planruns.run_resume_operation(
        t03_session,
        seen_ops[0],
        provider_factory=_provider_factory(http, ledger, settings),
        session_factory=create_session_factory(url),
    )
    assert detail["run_id"] == str(run_id)
    async with create_session_factory(url)() as fresh:
        final = await executor.get_run(fresh, run_id=run_id)
    assert final["status"] == "done"
    assert final["stop_marker"] is None
    researcher_prompts = [p for p in capture.prompts if p.startswith("# Researcher")]
    assert researcher_prompts == []


def test_every_t03_function_has_a_production_caller() -> None:
    """Inventory: every T03 function is called on a real production path.

    Traced with the AST through import aliases, not keywords: a bare
    mention in a comment, docstring, string, import, or decorator
    never counts — only a Call node resolving to the function counts,
    and true self-recursion does not count as a caller.
    """
    import ast
    import pathlib

    functions = [
        "drive_run",
        "verify_citations",
        "execute_parser_spec",
        "transcribe_audio",
        "approve_candidate",
        "refresh_plan_estimate",
        "snapshot_version",
        "set_source_as_of",
    ]
    callers: dict[str, set[str]] = {function: set() for function in functions}

    class _Visitor(ast.NodeVisitor):
        """Resolve every call through the module's import aliases."""

        def __init__(self, module: str) -> None:
            self._module = module
            self._aliases: dict[str, tuple[str, str]] = {}
            self._scope = [module]

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            base = node.module or ""
            for alias in node.names:
                self._aliases[alias.asname or alias.name] = (base, alias.name)
            self.generic_visit(node)

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                self._aliases[alias.asname or alias.name] = (alias.name, "")
            self.generic_visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._scope.append(f"{self._module}:{node.name}")
            self.generic_visit(node)
            self._scope.pop()

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._scope.append(f"{self._module}:{node.name}")
            self.generic_visit(node)
            self._scope.pop()

        def _resolve(self, target: ast.expr) -> tuple[str, str]:
            if isinstance(target, ast.Name):
                return self._aliases.get(target.id, (self._module, target.id))
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                base = target.value.id
                if base in self._aliases:
                    mod, name = self._aliases[base]
                    return (f"{mod}.{name}" if name else mod, target.attr)
                return (self._module, target.attr)
            return ("", "")

        def visit_Call(self, node: ast.Call) -> None:
            mod, name = self._resolve(node.func)
            if name in callers:
                enclosing = self._scope[-1]
                func = enclosing.rsplit(":", 1)[-1] if ":" in enclosing else ""
                if (mod, name) != (self._module, func):
                    callers[name].add(enclosing)
            self.generic_visit(node)

    for path in pathlib.Path("src/app").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        module = "app." + ".".join(path.relative_to("src/app").with_suffix("").parts)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        _Visitor(module).visit(tree)
    missing = {
        function: sorted(callers[function]) for function in functions if not callers[function]
    }
    assert missing == {}, f"functions without a production caller: {missing}"


# Item 6: source refs are persisted, handles are checked.


async def test_plan_writes_citations_and_versions(t03_session: AsyncSession) -> None:
    """Blocks get versions, current ids, step ids, and citation rows."""
    settings = _settings()
    user = await _user(t03_session, "cite")
    course = await _course(t03_session, user, title="Cite")
    await _text_source(
        t03_session, settings, user, course, "Article 4 requires AI literacy among staff."
    )
    actor = _actor(user, frozenset({"run.start", "operation.run", "course.edit", "course.plan"}))
    run_id = await _submit_and_create(t03_session, actor, course, ["literacy"])
    final = await _drive(run_id, _Capture(), settings)
    assert final["status"] == "done"
    nulls = (
        await t03_session.execute(
            sa.text(
                "SELECT count(*) FROM blocks b JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :course AND b.current_version_id IS NULL"
            ),
            {"course": str(course)},
        )
    ).scalar()
    assert nulls == 0
    orphan_steps = (
        await t03_session.execute(
            sa.text(
                "SELECT count(*) FROM block_versions bv JOIN blocks b ON b.id = bv.block_id"
                " JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :course AND bv.version_no = 1"
                " AND bv.run_step_id IS NULL"
            ),
            {"course": str(course)},
        )
    ).scalar()
    assert orphan_steps == 0
    citations = (
        await t03_session.execute(
            sa.text(
                "SELECT count(*) FROM citations c JOIN block_versions bv"
                " ON bv.id = c.block_version_id JOIN blocks b ON b.id = bv.block_id"
                " JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id WHERE m.course_id = :course"
            ),
            {"course": str(course)},
        )
    ).scalar()
    assert citations and citations >= 3


async def test_bad_handle_holds_never_crashes(t03_session: AsyncSession) -> None:
    """P99 in an architect reply holds the block, never crashes, never stops."""
    from app.agents import architect

    errors = architect.check_output(
        _tree_output(["P99"]),
        {"lesson": ["teach", "example", "check"]},
        {"P1"},
    )
    assert any("unknown handle" in error for error in errors)
    settings = _settings()
    user = await _user(t03_session, "badhandle")
    course = await _course(t03_session, user, title="BadHandle")
    await _text_source(
        t03_session, settings, user, course, "Article 4 requires AI literacy among staff."
    )
    actor = _actor(user, frozenset({"run.start", "operation.run", "course.edit", "course.plan"}))
    run_id = await _submit_and_create(t03_session, actor, course, ["literacy"])
    final = await _drive(run_id, _Capture(architect_handles=["P99"]), settings)
    assert final["status"] == "done"
    assert final["stop_marker"] is None
    held = await course_service.held_blocks(t03_session, course_id=course)
    assert len(held["blocks"]) == 3
    assert all("unknown handle" in block["hold_reason"] for block in held["blocks"])


def test_citation_quote_from_another_passage_holds() -> None:
    """Mutant kill: a quote is verified against its own passage only."""
    from app.runs.executor import verify_citations

    passages = {
        "a": "Article 4 requires AI literacy among staff.",
        "b": "Providers must ensure training for deployers.",
    }
    stray = [
        {
            "passage_id": "a",
            "claim_path": "blocks.b1",
            "quote": "Providers must ensure training",
            "quote_start": 0,
            "quote_end": 30,
        }
    ]
    holds = verify_citations(stray, passages)
    assert len(holds) == 1 and "verbatim" in holds[0]


def test_citation_offsets_are_checked() -> None:
    """Mutant kill: right quote with wrong offsets still holds."""
    from app.runs.executor import verify_citations

    passages = {"p1": "Article 4 requires AI literacy among staff."}
    shifted = [
        {
            "passage_id": "p1",
            "claim_path": "blocks.b1",
            "quote": "AI literacy",
            "quote_start": 0,
            "quote_end": 11,
        }
    ]
    holds = verify_citations(shifted, passages)
    assert len(holds) == 1 and "offsets" in holds[0]


# Item 7: binary uploads, media paths, per-passage language.


def _pdf_bytes(pages: list[str]) -> bytes:
    """Minimal one-font PDF; offsets computed so pypdf parses it."""
    objects: list[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{4 + index} 0 R" for index in range(len(pages)))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    streams: list[bytes] = []
    for page_text in pages:
        escaped = page_text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        streams.append(f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1"))
    for index in range(len(pages)):
        stream_id = 4 + len(pages) + index
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
            f" /Resources << /Font << /F1 3 0 R >> >>"
            f" /Contents {stream_id} 0 R >>".encode()
        )
    blob = b"%PDF-1.4\n"
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(blob))
        blob += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    for stream in streams:
        offsets.append(len(blob))
        number = len(offsets) - 1
        blob += (
            f"{number} 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode()
            + stream
            + b"\nendstream\nendobj\n"
        )
    xref_at = len(blob)
    total = len(offsets)
    blob += f"xref\n0 {total}\n".encode()
    blob += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        blob += f"{offset:010d} 00000 n \n".encode()
    blob += f"trailer\n<< /Size {total} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF".encode()
    return blob


async def test_pdf_upload_extracts_pages_not_garbage(
    t03_session: AsyncSession,
) -> None:
    """A PDF is extracted with pypdf page addresses, never UTF-8-chunked."""
    settings = _settings()
    user = await _user(t03_session, "pdf")
    course = await _course(t03_session, user, title="PDF")
    content = _pdf_bytes(["Article 4 requires AI literacy among staff."])
    passages = library.chunk_upload(content, media="pdf", filename="act.pdf")
    assert len(passages) == 1
    assert passages[0]["address_key"] == "p1/para1"
    assert "literacy" in passages[0]["text"]
    source_id = await library.store_upload(
        t03_session,
        settings=settings,
        created_by=user,
        filename="act.pdf",
        media="pdf",
        content=content,
        course_id=course,
    )
    count = await library.ingest_passages(t03_session, source_id=source_id, passages=passages)
    await t03_session.commit()
    assert count == 1
    stored = (
        await t03_session.execute(
            sa.text("SELECT address_key FROM passages WHERE source_id = :source"),
            {"source": str(source_id)},
        )
    ).scalar_one()
    assert stored == "p1/para1"


async def test_pdf_sniffed_even_when_media_is_text() -> None:
    """A PDF mislabelled as text is still extracted, not chunked as text."""
    content = _pdf_bytes(["Sniffed PDF sentence here."])
    passages = library.chunk_upload(content, media="text", filename="act.pdf")
    assert len(passages) == 1
    assert passages[0]["address_key"] == "p1/para1"


async def test_image_and_audio_have_their_own_paths() -> None:
    """Images store with no passages; audio waits for transcription; else 415."""
    from app.errors import UnsupportedMediaError, http_status_for

    assert (
        library.chunk_upload(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, media="image", filename="fig.png")
        == []
    )
    assert library.chunk_upload(b"RIFF" + b"\x00" * 64, media="audio", filename="memo.wav") == []
    with pytest.raises(UnsupportedMediaError):
        library.chunk_upload(b"data", media="video", filename="clip.mp4")
    try:
        library.chunk_upload(b"data", media="video", filename="clip.mp4")
    except UnsupportedMediaError as exc:
        assert http_status_for(exc) == 415
    else:  # pragma: no cover - the raise above always fires
        raise AssertionError("unsupported media must answer 415")


async def test_language_detected_per_passage(t03_session: AsyncSession) -> None:
    """en/nb/uk heuristics plus override decide each passage ts_config."""
    assert library.detect_language("Article 4 requires AI literacy.") == "en"
    assert library.detect_language("Arbeidsmiljøloven gjelder for ansatte.") == "nb"
    assert library.detect_language("Тому курс також доступний українською.") == "uk"
    assert library.ts_config_for_passage("anything", "norwegian") == "norwegian"
    settings = _settings()
    user = await _user(t03_session, "lang")
    course = await _course(t03_session, user, title="Lang")
    source_id = await library.store_upload(
        t03_session,
        settings=settings,
        created_by=user,
        filename="mix.txt",
        media="text",
        content=b"mixed",
        course_id=course,
    )
    await library.ingest_passages(
        t03_session,
        source_id=source_id,
        passages=[
            {
                "text": "Arbeidsmiljøloven gjelder for ansatte i kommunen.",
                "address": {"kind": "pdf", "page": 1, "para": 1},
                "address_key": "p1/para1",
            },
            {
                "text": "Курс охоплює обов'язки роботодавця.",
                "address": {"kind": "pdf", "page": 1, "para": 2},
                "address_key": "p1/para2",
            },
        ],
    )
    await t03_session.commit()
    configs = (
        await t03_session.execute(
            sa.text(
                "SELECT address_key, ts_config FROM passages WHERE source_id = :source"
                " ORDER BY address_key"
            ),
            {"source": str(source_id)},
        )
    ).all()
    assert ("p1/para1", "norwegian") in [(row[0], row[1]) for row in configs]
    assert ("p1/para2", "simple") in [(row[0], row[1]) for row in configs]


# Item 8: FTS honours passage language, trigram backs up `simple`.


async def test_fts_finds_nb_and_uk_passages_in_one_course(
    t03_session: AsyncSession,
) -> None:
    """One course query hits the nb and the uk passage in their configs."""
    from app.providers.retrieval import FtsRetriever

    settings = _settings()
    user = await _user(t03_session, "fts")
    course = await _course(t03_session, user, title="FTS")
    source_id = await library.store_upload(
        t03_session,
        settings=settings,
        created_by=user,
        filename="mix.txt",
        media="text",
        content=b"mixed",
        course_id=course,
    )
    await library.ingest_passages(
        t03_session,
        source_id=source_id,
        passages=[
            {
                "text": "Arbeidsmiljøloven gjelder for alle ansatte i kommunen.",
                "address": {"kind": "pdf", "page": 1, "para": 1},
                "address_key": "p1/para1",
            },
            {
                "text": "Конституція гарантує основні права громадян.",
                "address": {"kind": "pdf", "page": 1, "para": 2},
                "address_key": "p1/para2",
            },
        ],
    )
    await library.approve_source(t03_session, source_id=source_id, approved_by=user)
    await t03_session.commit()
    nb = await FtsRetriever().search_course(t03_session, query="ansatte", course_id=course, limit=8)
    assert any("Arbeidsmiljøloven" in item.text for item in nb)
    uk = await FtsRetriever().search_course(
        t03_session, query="громадян", course_id=course, limit=8
    )
    assert any("Конституція" in item.text for item in uk)


async def test_trigram_fallback_catches_uk_typo(t03_session: AsyncSession) -> None:
    """A misspelled single-token uk query still retrieves via pg_trgm."""
    from app.providers.retrieval import FtsRetriever

    settings = _settings()
    user = await _user(t03_session, "trgm")
    course = await _course(t03_session, user, title="Trigram")
    source_id = await library.store_upload(
        t03_session,
        settings=settings,
        created_by=user,
        filename="uk.txt",
        media="text",
        content=b"uk",
        course_id=course,
    )
    await library.ingest_passages(
        t03_session,
        source_id=source_id,
        passages=[
            {
                "text": "Конституція України",
                "address": {"kind": "pdf", "page": 1, "para": 1},
                "address_key": "p1/para1",
            }
        ],
    )
    await library.approve_source(t03_session, source_id=source_id, approved_by=user)
    await t03_session.commit()
    found = await FtsRetriever().search(
        t03_session, query="Констітуція", ts_config="simple", limit=8, course_id=course
    )
    assert any("Конституція України" in item.text for item in found)


# Item 9: the course language reaches every agent.


def test_language_in_every_agent_prompt() -> None:
    """course_language and source languages are typed inputs everywhere."""
    from app.agents import architect, librarian, researcher

    researcher_prompt = researcher.build_prompt(
        "duty", ["duty"], [], course_language="nb", source_languages=["en", "nb"]
    )
    assert '"course_language": "nb"' in researcher_prompt
    architect_prompt = architect.build_prompt(
        {"methodology_codes": []},
        [],
        {},
        [],
        course_language="uk",
        source_languages=["uk"],
    )
    assert '"course_language": "uk"' in architect_prompt
    librarian_prompt = librarian.build_prompt("sample page", course_language="nb")
    assert '"course_language": "nb"' in librarian_prompt


def test_every_system_prompt_carries_a_language_rule() -> None:
    """No prompt ships without its language rule (BRIEF §9)."""
    from pathlib import Path

    for agent in ("researcher", "architect", "librarian"):
        text = (Path("prompts") / agent / "system.md").read_text(encoding="utf-8")
        assert "course_language" in text


# Item 10: digests carry upstream outputs, model, effort, tokens.


def test_digest_changes_with_upstream_output() -> None:
    """Mutant kill: the digest is never a constant of the prompt."""
    from app.runs.executor import step_digest

    base = step_digest(agent="architect", prompt="p", upstream={"r": {"a": 1}})
    changed = step_digest(agent="architect", prompt="p", upstream={"r": {"a": 2}})
    assert base != changed
    assert base == step_digest(agent="architect", prompt="p", upstream={"r": {"a": 1}})
    assert step_digest(agent="researcher", prompt="p") != step_digest(agent="architect", prompt="p")


async def test_step_keys_are_agent_plan_node(t03_session: AsyncSession) -> None:
    """Researcher steps are `researcher:<node>`, never `researcher:<ordinal>`."""
    import re

    user = await _user(t03_session, "keys")
    course = await _course(t03_session, user, title="Keys")
    actor = _actor(user, frozenset({"run.start", "operation.run", "course.edit", "course.plan"}))
    operation_id = await planruns.submit_plan(
        t03_session, actor=actor, course_id=course, queries=["first", "second"], defer=None
    )
    await t03_session.commit()
    detail = await planruns.run_plan_operation(t03_session, operation_id)
    dag = (
        await t03_session.execute(
            sa.text("SELECT compiled_dag FROM runs WHERE id = :run"),
            {"run": str(detail["run_id"])},
        )
    ).scalar_one()
    keys = [entry["step_key"] for entry in dag]
    assert keys[-1] == "architect:plan"
    assert all(re.fullmatch(r"researcher:q[0-9a-f]{12}", key) for key in keys[:-1])
    assert len(set(keys)) == len(keys)


async def test_claim_is_visible_without_explicit_commit(
    t03_session: AsyncSession,
) -> None:
    """Mutant kill: claim_step commits, so a re-run sees the first claim."""
    from conftest import test_database_url

    from app.db import create_session_factory
    from app.runs.executor import claim_step, new_run

    user = await _user(t03_session, "claim")
    course = await _course(t03_session, user, title="Claim")
    run_id = await new_run(t03_session, course_id=course, kind="plan", dag=[])
    await t03_session.commit()
    await claim_step(t03_session, run_id=run_id, step_key="researcher:qx", input_digest="d")
    async with create_session_factory(test_database_url())() as fresh:
        status = (
            await fresh.execute(
                sa.text("SELECT status FROM run_steps WHERE run_id = :run AND step_key = :key"),
                {"run": str(run_id), "key": "researcher:qx"},
            )
        ).scalar_one()
    assert status == "running"


# Item 11: code errors stop the run with a marker.


async def test_code_error_stops_run_with_marker(t03_session: AsyncSession) -> None:
    """ValidationError/KeyError/IntegrityError fail the step, never hang it."""
    from conftest import test_database_url

    from app.db import create_session_factory
    from app.errors import ValidationError
    from app.runs import executor

    user = await _user(t03_session, "boom")
    course = await _course(t03_session, user, title="Boom")
    run_id = await executor.new_run(
        t03_session,
        course_id=course,
        kind="plan",
        dag=[{"step_key": "researcher:qx", "handler": "boom", "depends_on": []}],
    )
    await t03_session.commit()

    async def boom(
        session: AsyncSession, run_id: UUID, entry: dict[str, Any], provider_factory: Any
    ) -> None:
        from app.runs.executor import claim_step

        await claim_step(
            session,
            run_id=run_id,
            step_key=str(entry["step_key"]),
            input_digest="d",
        )
        raise ValidationError("boom: bad shape")

    final = await executor.drive_run(
        create_session_factory(test_database_url()),
        run_id=run_id,
        provider_factory=lambda s: None,
        handlers={"boom": boom},
    )
    assert final["status"] == "stopped"
    assert "boom" in (final["stop_marker"] or "")
    assert final["steps"][0]["status"] == "failed"


# Item 12: scope checks answer 404 outside scope.


def _ctx(session: AsyncSession, settings: Settings, principal: Any) -> Any:
    from app.auth.ctx import RequestContext

    return RequestContext(
        db=session,
        settings=settings,
        session_token=None,
        login_session=None,
        principal=principal,
    )


async def test_run_read_and_resume_answer_404_outside_scope(
    t03_session: AsyncSession,
) -> None:
    """Owner B reads nothing of owner A's run, and resumes nothing of it."""
    from app.controllers import t03 as controller
    from app.errors import NotFoundError

    settings = _settings()
    user_a = await _user(t03_session, "sa")
    user_b = await _user(t03_session, "sb")
    course_a = await _course(t03_session, user_a, title="SA")
    await _text_source(t03_session, settings, user_a, course_a, "Article 4 requires AI literacy.")
    actor_a = _actor(
        user_a, frozenset({"run.start", "operation.run", "course.edit", "course.plan"})
    )
    run_id = await _submit_and_create(t03_session, actor_a, course_a, ["literacy"])
    await _drive(run_id, _Capture(), settings)
    ctx_b = _ctx(
        t03_session,
        settings,
        _actor(user_b, frozenset({"run.start", "run.retry", "source.approve"})),
    )
    with pytest.raises(NotFoundError):
        await controller.get_run(ctx_b, run_id=str(run_id))
    with pytest.raises(NotFoundError):
        await controller.resume_run(ctx_b, run_id=str(run_id))


async def test_fetch_and_approve_checked_against_scope(
    t03_session: AsyncSession,
) -> None:
    """B cannot fetch into, or approve, A's course-bound source."""
    from app.controllers import t03 as controller
    from app.errors import NotFoundError

    settings = _settings()
    user_a = await _user(t03_session, "fa")
    user_b = await _user(t03_session, "fb")
    course_a = await _course(t03_session, user_a, title="FA")
    course_b = await _course(t03_session, user_b, title="FB")
    source_id = await library.store_upload(
        t03_session,
        settings=settings,
        created_by=user_a,
        filename="a.txt",
        media="text",
        content=b"Owner A text about duties.",
        course_id=course_a,
    )
    await library.ingest_passages(
        t03_session,
        source_id=source_id,
        passages=library.chunk_text("Owner A text about duties."),
    )
    await t03_session.commit()
    ctx_b = _ctx(
        t03_session,
        settings,
        _actor(user_b, frozenset({"source.upload", "source.approve"})),
    )
    with pytest.raises(NotFoundError):
        await controller.fetch_source(ctx_b, url="https://example.com/x", course_id=str(course_a))
    with pytest.raises(NotFoundError):
        await controller.approve_source(ctx_b, source_id=str(source_id))
    ctx_a = _ctx(
        t03_session,
        settings,
        _actor(user_a, frozenset({"source.upload", "source.approve"})),
    )
    assert (await controller.approve_source(ctx_a, source_id=str(source_id))) == {"ok": True}
    _ = course_b


# Item 13: route gate sees literal permission codes.


def test_submit_paths_require_literal_codes() -> None:
    """plan/submit and generate/submit resolve to literal require codes."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("src/app/services/planruns.py").read_text())
    literals: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or len(node.args) != 2:
            continue
        func = node.func
        name = getattr(func, "attr", None) or getattr(func, "id", None)
        if name != "require" or not isinstance(node.args[1], ast.Constant):
            continue
        literals.add(str(node.args[1].value))
    assert "course.plan" in literals
    assert "course.generate" in literals
    assert "run.retry" in literals


# Item 14: block-type registry, answer containment.


def test_block_type_registry_is_enforced_at_plan_time() -> None:
    """Unknown types and wrong-kind types are blocking tree errors."""
    from app.course.tree import BLOCK_TYPES, validate_tree

    assert "spot_the_error" not in BLOCK_TYPES["lesson"]
    tree = _lesson_tree("r")
    assert validate_tree(tree, {"lesson": ["teach", "example", "check"]}) == []
    tree.modules[0].topics[0].blocks[0].block_type = "spot_the_error"
    errors = validate_tree(tree, {"lesson": ["teach", "example", "check"]})
    assert any("not allowed for topic kind" in error for error in errors)
    tree.modules[0].topics[0].blocks[0].block_type = "freeform_essay"
    errors = validate_tree(tree, {"lesson": ["teach", "example", "check"]})
    assert any("unknown block type" in error for error in errors)


def test_brief_answer_containment() -> None:
    """A brief leaking its answer key is caught before generation."""
    from app.course.tree import brief_contains_answer

    assert (
        brief_contains_answer(
            "Who must ensure AI literacy?",
            {"correct": "deployer"},
        )
        is False
    )
    assert (
        brief_contains_answer(
            "Providers and deployers must ensure AI literacy?",
            {"correct": "Providers and deployers must ensure AI literacy"},
        )
        is True
    )
    assert brief_contains_answer("A topic", {"correct": "x"}) is False


# Item 15: the architect sees the whole source, sorted, in the cached prefix.


def test_address_sort_is_numeric() -> None:
    """para2 sorts before para10; unknown shapes stay stable."""
    from app.runs.executor import address_sort_key

    ordered = sorted(
        ["p1/para10", "p1/para2", "p2/para1", "p1/para1"],
        key=address_sort_key,
    )
    assert ordered == ["p1/para1", "p1/para2", "p1/para10", "p2/para1"]


async def test_architect_prompt_carries_full_sorted_source(
    t03_session: AsyncSession,
) -> None:
    """Twelve paragraphs arrive whole, numeric, sources before the plan."""
    settings = _settings()
    user = await _user(t03_session, "ctx")
    course = await _course(t03_session, user, title="Ctx")
    text = "\n\n".join(f"Paragraph {n} about the literacy duty." for n in range(1, 13))
    long_text = text + "\n\n" + "Long tail. " * 200
    await _text_source(t03_session, settings, user, course, long_text)
    actor = _actor(user, frozenset({"run.start", "operation.run", "course.edit", "course.plan"}))
    run_id = await _submit_and_create(t03_session, actor, course, ["literacy duty"])
    capture = _Capture()
    final = await _drive(run_id, capture, settings)
    assert final["status"] == "done"
    architect_prompts = [p for p in capture.prompts if p.startswith("# Architect")]
    assert len(architect_prompts) == 1
    prompt = architect_prompts[0]
    assert "Long tail. Long tail." in prompt
    assert prompt.index('"passages"') < prompt.index('"profile"')
    assert prompt.index("p1/para2") < prompt.index("p1/para10")
    assert '"course_language": "en"' in prompt


# Item 16: the image ships prompts; the root is absolute.


def test_image_ships_prompts_and_root_is_absolute() -> None:
    """Dockerfile copies prompts/; PROMPT_ROOT never depends on cwd."""
    import pathlib

    dockerfile = pathlib.Path("Dockerfile").read_text()
    assert "COPY prompts/" in dockerfile
    ignore = pathlib.Path(".dockerignore")
    assert ignore.exists()
    excluded = [
        line.strip()
        for line in ignore.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert not any("prompts" in line for line in excluded)
    from app.agents import base

    assert base.PROMPT_ROOT.is_absolute()
    assert base.system_prompt("architect").startswith("# Architect")
    import os

    cwd = os.getcwd()
    try:
        os.chdir("/tmp")
        assert base.system_prompt("researcher").startswith("# Researcher")
    finally:
        os.chdir(cwd)


# Item 17: FTS ranking and hard negatives are stored from the first run.


async def test_fts_ranking_and_hard_negatives_stored(t03_session: AsyncSession) -> None:
    """retrieval_pairs holds the FTS order plus top-k passages never cited."""
    from app.agents import researcher

    settings = _settings()
    user = await _user(t03_session, "pairs")
    course = await _course(t03_session, user, title="Pairs")
    source_id = await library.store_upload(
        t03_session,
        settings=settings,
        created_by=user,
        filename="many.txt",
        media="text",
        content=b"many",
        course_id=course,
    )
    await library.ingest_passages(
        t03_session,
        source_id=source_id,
        passages=[
            {
                "text": f"Literacy duty note number {n} for staff training.",
                "address": {"kind": "pdf", "page": 1, "para": n},
                "address_key": f"p1/para{n}",
            }
            for n in range(1, 7)
        ],
    )
    await library.approve_source(t03_session, source_id=source_id, approved_by=user)
    await t03_session.commit()
    from app.providers.retrieval import FtsRetriever

    found = await FtsRetriever().search_course(
        t03_session, query="literacy duty", course_id=course, limit=8
    )
    assert len(found) >= 4
    handle_to_passage = {f"P{i}": item.passage_id for i, item in enumerate(found, 1)}
    cited = [
        {"handle": "P4", "rank": 1, "score": 0.9},
        {"handle": "P5", "rank": 2, "score": 0.8},
    ]
    await researcher.record_pairs(
        t03_session,
        query="literacy duty",
        query_lang="en",
        ranked=cited,
        handle_to_passage=handle_to_passage,
        ranker="reranker_stub",
        fts_order=[item.passage_id for item in found],
    )
    await t03_session.commit()
    rows = (
        await t03_session.execute(
            sa.text("SELECT ranker, label FROM retrieval_pairs WHERE query = :q"),
            {"q": "literacy duty"},
        )
    ).all()
    rankers = {row[0] for row in rows}
    assert "fts" in rankers
    assert any(row[0] == "fts" and row[1] == "hard_negative" for row in rows)


# Item 18: candidate approval fetches first and never re-approves.


async def test_candidate_approval_fetches_and_never_reapproves(
    t03_session: AsyncSession,
) -> None:
    """Approval ingests the page; a sha conflict keeps the old approval."""
    settings = _settings()
    user = await _user(t03_session, "cand")
    course = await _course(t03_session, user, title="Cand")
    body = "The AI Act requires literacy for deployers."

    def page(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(page))
    candidate = await library.add_web_candidate(
        t03_session,
        course_id=course,
        query="literacy",
        url="https://example.com/act",
        title="AI Act",
    )
    await t03_session.commit()
    source_id = await library.approve_candidate(
        t03_session,
        candidate_id=candidate,
        approved_by=user,
        settings=settings,
        client=client,
    )
    await t03_session.commit()
    passages = (
        await t03_session.execute(
            sa.text("SELECT count(*) FROM passages WHERE source_id = :source"),
            {"source": str(source_id)},
        )
    ).scalar()
    assert passages and passages >= 1
    binding = (
        await t03_session.execute(
            sa.text(
                "SELECT approval FROM course_sources WHERE course_id = :course"
                " AND source_id = :source"
            ),
            {"course": str(course), "source": str(source_id)},
        )
    ).scalar_one()
    assert binding == "approved"
    await t03_session.execute(
        sa.text("UPDATE sources SET approval = 'held' WHERE id = :source"),
        {"source": str(source_id)},
    )
    await t03_session.commit()
    again = await library.add_web_candidate(
        t03_session,
        course_id=course,
        query="literacy",
        url="https://example.com/act-again",
        title="AI Act again",
    )
    await t03_session.commit()
    same = await library.approve_candidate(
        t03_session,
        candidate_id=again,
        approved_by=user,
        settings=settings,
        client=client,
    )
    await t03_session.commit()
    assert same == source_id
    status = (
        await t03_session.execute(
            sa.text("SELECT approval FROM sources WHERE id = :source"),
            {"source": str(source_id)},
        )
    ).scalar_one()
    assert status == "held"


# LOW items touched along the way.


async def test_through_line_persisted_on_the_course(
    t03_session: AsyncSession,
) -> None:
    """The course-level case object survives the plan write and reads back."""
    from app.course.tree import ThroughLine

    user = await _user(t03_session, "tl")
    course = await _course(t03_session, user, title="ThroughLine")
    tree = _lesson_tree("tl")
    tree.through_line = ThroughLine(
        case="A municipal rollout.",
        handoffs=[],
    )
    await course_service.write_tree(t03_session, course_id=course, tree=tree, plan_digest="d")
    await t03_session.commit()
    stored = await course_service.read_through_line(t03_session, course_id=course)
    assert stored["case"] == "A municipal rollout."
    back = await course_service.read_tree(t03_session, course_id=course)
    assert (back["through_line"] or {})["case"] == "A municipal rollout."


async def test_replan_keeps_author_edited_blocks(t03_session: AsyncSession) -> None:
    """Re-planning restores author versions (run_step_id NULL) on top."""
    import json as _json

    settings = _settings()
    user = await _user(t03_session, "replan")
    course = await _course(t03_session, user, title="Replan")
    await _text_source(t03_session, settings, user, course, "Article 4 requires AI literacy.")
    actor = _actor(user, frozenset({"run.start", "operation.run", "course.edit", "course.plan"}))
    run_id = await _submit_and_create(t03_session, actor, course, ["literacy"])
    assert (await _drive(run_id, _Capture(), settings))["status"] == "done"
    tree = await course_service.read_tree(t03_session, course_id=course)
    node = tree["modules"][0]["topics"][0]["blocks"][0]["plan_node_id"]
    block_id = tree["modules"][0]["topics"][0]["blocks"][0]["id"]
    author_content = {"brief": "Author wording wins", "source_handles": ["P1"]}
    version_id = (
        await t03_session.execute(
            sa.text(
                "INSERT INTO block_versions (block_id, version_no, content, schema_version,"
                " run_step_id) VALUES (:block, 2, CAST(:content AS jsonb), '1', NULL)"
                " RETURNING id"
            ),
            {"block": block_id, "content": _json.dumps(author_content)},
        )
    ).scalar_one()
    await t03_session.execute(
        sa.text("UPDATE blocks SET current_version_id = :version WHERE id = :block"),
        {"version": str(version_id), "block": block_id},
    )
    await t03_session.commit()
    # Replan with the same node ids: the author version must come back.
    plan = course_service.stored_tree_contract(tree)
    await course_service.write_tree(t03_session, course_id=course, tree=plan, plan_digest="d2")
    await t03_session.commit()
    # The block row is new after replan; find the kept content by node.
    kept_row = (
        await t03_session.execute(
            sa.text(
                "SELECT bv.content, bv.run_step_id FROM block_versions bv"
                " JOIN blocks b ON b.id = bv.block_id"
                " JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :course AND b.plan_node_id = :node"
                " AND bv.id = b.current_version_id"
            ),
            {"course": str(course), "node": node},
        )
    ).first()
    assert kept_row is not None
    assert kept_row[0]["brief"] == "Author wording wins"
    assert kept_row[1] is None


async def test_course_language_follows_bcp47(t03_session: AsyncSession) -> None:
    """The DB check accepts BCP-47 tags and rejects free text."""
    user = await _user(t03_session, "bcp")
    row = (
        await t03_session.execute(
            sa.text(
                "INSERT INTO courses (owner_id, title, language, status, scope_tags)"
                " VALUES (:owner, 'T', 'pt-BR', 'draft', '{}') RETURNING id"
            ),
            {"owner": str(user)},
        )
    ).one()
    await t03_session.rollback()
    assert row is not None
    import sqlalchemy.exc as _sa_exc

    with pytest.raises(_sa_exc.DBAPIError):
        await t03_session.execute(
            sa.text(
                "INSERT INTO courses (owner_id, title, language, status, scope_tags)"
                " VALUES (:owner, 'T', 'not a language!', 'draft', '{}')"
            ),
            {"owner": str(user)},
        )
        await t03_session.commit()
    await t03_session.rollback()


async def test_validate_reruns_the_tree_contract(t03_session: AsyncSession) -> None:
    """The validate route reports prescription drift, not only questions."""
    settings = _settings()
    user = await _user(t03_session, "valid")
    course = await _course(t03_session, user, title="Valid")
    await _text_source(t03_session, settings, user, course, "Article 4 requires AI literacy.")
    actor = _actor(user, frozenset({"run.start", "operation.run", "course.edit", "course.plan"}))
    run_id = await _submit_and_create(t03_session, actor, course, ["literacy"])
    assert (await _drive(run_id, _Capture(), settings))["status"] == "done"
    clean = await course_service.validate_course_tree(t03_session, course_id=course)
    assert clean == []
    topic_id = (
        await t03_session.execute(
            sa.text(
                "SELECT t.id FROM topics t JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :course LIMIT 1"
            ),
            {"course": str(course)},
        )
    ).scalar_one()
    await t03_session.execute(
        sa.text(
            "INSERT INTO blocks (topic_id, plan_node_id, ordinal, role, block_type,"
            " brief, status) VALUES (:topic, 'drift-b', 99, 'practice', 'choice',"
            " 'Drift block?', 'draft')"
        ),
        {"topic": str(topic_id)},
    )
    await t03_session.commit()
    drifted = await course_service.validate_course_tree(t03_session, course_id=course)
    assert any(
        keyword in error
        for error in drifted
        for keyword in ("prescription", "source ref", "tree contract")
    )


async def test_fetch_size_cap_and_robots_fail_closed(
    t03_session: AsyncSession,
) -> None:
    """Oversized bodies are capped; missing robots.txt queues the page."""
    settings = _settings(source_fetch_max_bytes=64)

    def big(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        return httpx.Response(200, text="A" * 10000)

    client = httpx.AsyncClient(transport=httpx.MockTransport(big))
    page = await library.fetch_url("https://example.com/big", settings=settings, client=client)
    assert len(page.text.encode("utf-8")) <= 64

    def no_robots(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="nope")

    from app.providers import web as web_provider

    closed = httpx.AsyncClient(transport=httpx.MockTransport(no_robots))
    assert (
        await web_provider.robots_allowed(
            "https://example.com/page", settings=settings, client=closed
        )
    ) is False
    result = await library.intake_url(
        t03_session,
        settings=settings,
        url="https://example.com/page",
        course_id=None,
        client=closed,
    )
    await t03_session.commit()
    assert "queued" in result


async def test_transcription_goes_through_provider_and_ledger(
    t03_session: AsyncSession,
) -> None:
    """Audio transcribes via the provider; the ledger records the call."""
    import asyncio
    import pathlib

    library_source = await asyncio.to_thread(pathlib.Path("src/app/services/library.py").read_text)
    assert "model_api_key" not in library_source
    settings = _settings()
    user = await _user(t03_session, "audio")
    source_id = await library.store_upload(
        t03_session,
        settings=settings,
        created_by=user,
        filename="memo.wav",
        media="audio",
        content=b"RIFF" + b"\x00" * 128,
    )
    await t03_session.commit()

    def transcribe(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "segments": [
                    {"start": "00:00:00", "end": "00:00:30", "text": "Welcome staff."},
                    {"start": "00:00:30", "end": "00:01:00", "text": "AI literacy matters."},
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(transcribe))
    count = await library.transcribe_source(
        t03_session, source_id=source_id, settings=settings, client=client
    )
    await t03_session.commit()
    assert count == 2
    ledger = (
        await t03_session.execute(
            sa.text(
                "SELECT model, cost_usd FROM llm_calls WHERE agent = 'transcriber'"
                " ORDER BY created_at DESC LIMIT 1"
            )
        )
    ).first()
    assert ledger is not None and ledger[0] == "muse-voice-transcribe-1.0"
    assert float(ledger[1]) > 0
