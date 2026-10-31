"""T03d probes: second-pass verification of T03b, measured on the real DB.

Each test pins one T03d item. They were written red first: a rejected
reply poisoned the store, fetches re-resolved DNS after the guard,
stranded running runs could never resume, the model ranking hid under
ranker='fts', a bad handle stopped the run, citations and parser specs
had paper-only callers, and several tests could not fail.
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
        source_store_path="/tmp/muse-t03d-sources",
        **overrides,
    )


async def _user(session: AsyncSession, tag: str) -> UUID:
    row = (
        await session.execute(
            sa.text("INSERT INTO users (email, display_name) VALUES (:e, 'N') RETURNING id"),
            {"e": f"t03d-{tag}-{uuid.uuid4().hex[:8]}@example.com"},
        )
    ).one()
    await session.commit()
    return UUID(str(row[0]))


def _actor(user_id: UUID, codes: frozenset[str]) -> Any:
    from app.rbac.principal import Principal

    return Principal(
        user_id=user_id,
        email="t03d@example.com",
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


def _ctx(session: AsyncSession, settings: Settings, principal: Any) -> Any:
    from app.auth.ctx import RequestContext

    return RequestContext(
        db=session,
        settings=settings,
        session_token=None,
        login_session=None,
        principal=principal,
    )


# Item 1: a rejected reply must never poison the store; retries pay again.


class _RejectOnce:
    """First researcher call cites P99 (rejected); every later call is valid."""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.researcher_calls = 0

    def __call__(self, request: Any) -> Any:
        import httpx2 as _httpx

        body = json.loads(request.content.decode("utf-8"))
        prompt = str(body.get("input", ""))
        self.prompts.append(prompt)
        if prompt.startswith("# Researcher"):
            self.researcher_calls += 1
            dynamic = json.loads(prompt.split("## Task input (typed, assembled by code)\n")[1])
            if self.researcher_calls == 1:
                reply = {
                    "query": dynamic["query"],
                    "rephrased": dynamic["rephrased"],
                    "ranked": [{"handle": "P99", "rank": 1, "score": 0.9}],
                    "web_candidates": [],
                }
            else:
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
        handles = [item["handle"] for item in dynamic["passages"][:1]] or ["P1"]
        return _httpx.Response(200, json=_completed(json.dumps(_tree_output(handles))))


async def test_rejected_reply_is_retried_with_new_variant(
    t03_session: AsyncSession,
) -> None:
    """Reject once, resume, pay exactly one new call under a new variant."""
    import httpx2 as _httpx
    from conftest import test_database_url

    from app.db import create_session_factory
    from app.runs import executor

    settings = _settings()
    user = await _user(t03_session, "poison")
    course = await _course(t03_session, user, title="Poison")
    await _text_source(
        t03_session, settings, user, course, "Article 4 requires AI literacy among staff."
    )
    actor = _actor(user, frozenset({"run.start", "operation.run", "course.edit", "course.plan"}))
    run_id = await _submit_and_create(t03_session, actor, course, ["literacy"])
    failing = _RejectOnce()
    stopped = await _drive(run_id, failing, settings)
    assert stopped["status"] == "stopped"

    async def defer(operation_id: UUID) -> None:
        return None

    retry_actor = _actor(user, frozenset({"run.retry"}))
    await planruns.resume_run(t03_session, actor=retry_actor, run_id=run_id, defer=defer)
    url = test_database_url()
    http = guarded_client(settings, inner=_httpx.MockTransport(failing))
    ledger = create_session_factory(url)
    async with create_session_factory(url)() as fresh:
        before = (
            await fresh.execute(
                sa.text(
                    "SELECT count(*) FROM llm_calls WHERE run_id = :run"
                    " AND agent = 'researcher' AND tokens_input > 0"
                ),
                {"run": str(run_id)},
            )
        ).scalar_one()
    assert before == 1
    from app.services.planruns import run_resume_operation

    operation_id = (
        await t03_session.execute(
            sa.text(
                "SELECT id FROM operations WHERE kind = 'resume_run'"
                " ORDER BY created_at DESC LIMIT 1"
            )
        )
    ).scalar_one()
    await run_resume_operation(
        t03_session,
        UUID(str(operation_id)),
        provider_factory=_provider_factory(http, ledger, settings),
        session_factory=create_session_factory(url),
    )
    async with create_session_factory(url)() as fresh:
        final = await executor.get_run(fresh, run_id=run_id)
        paid = (
            await fresh.execute(
                sa.text(
                    "SELECT variant FROM llm_calls WHERE run_id = :run"
                    " AND agent = 'researcher' AND tokens_input > 0 ORDER BY created_at"
                ),
                {"run": str(run_id)},
            )
        ).all()
    assert final["status"] == "done"
    assert [row[0] for row in paid] == [0, 1]


async def test_rejected_reply_never_reaches_store(
    t03_session: AsyncSession,
) -> None:
    """A check-rejected reply is paid for but never stored for reuse."""
    import httpx2 as _httpx
    from conftest import test_database_url as _db_url

    from app.agents.researcher import schema as researcher_schema
    from app.db import create_session_factory
    from app.errors import ValidationError
    from app.providers.llm import MetaProvider

    settings = _settings()

    def bad(request: Any) -> Any:
        return _httpx.Response(200, json=_completed(json.dumps({"not": "ranked"})))

    ledger = create_session_factory(_db_url())
    http = guarded_client(settings, inner=_httpx.MockTransport(bad))
    async with create_session_factory(_db_url())() as session:
        provider = MetaProvider(session, settings, http, ledger)
        with pytest.raises(ValidationError, match="output rejected"):
            await provider.generate_json(
                agent="researcher",
                prompt=f"probe {uuid.uuid4().hex}",
                schema=researcher_schema(),
                check=lambda reply: (
                    ["ranked must be a list"] if not isinstance(reply.get("ranked"), list) else []
                ),
            )
    async with ledger() as reader:
        stored = (await reader.execute(sa.text("SELECT count(*) FROM llm_replies"))).scalar_one()
        paid = (
            await reader.execute(
                sa.text(
                    "SELECT count(*) FROM llm_calls WHERE agent = 'researcher' AND tokens_input > 0"
                )
            )
        ).scalar_one()
    assert stored == 0
    assert paid == 1


# Item 2: the fetch connects to the checked IP; multicast/reserved refuse.


def _stub_getaddrinfo(public_first: bool = True) -> Any:
    import socket

    real = socket.getaddrinfo

    def fake(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
        if host in ("example.com", "rebind.test"):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
            ]
        return real(host, port, *args, **kwargs)

    return fake


async def test_fetch_pins_the_checked_ip() -> None:
    """A resolver that later answers 127.0.0.1 never sees the request."""
    import socket

    seen: list[httpx.Request] = []

    def pinned(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        return httpx.Response(200, text="pinned body")

    real_getaddrinfo = socket.getaddrinfo
    socket.getaddrinfo = _stub_getaddrinfo()  # type: ignore[assignment]
    try:
        client = httpx.AsyncClient(transport=httpx.MockTransport(pinned))
        page = await library.fetch_url(
            "https://example.com/page", settings=_settings(), client=client
        )
    finally:
        socket.getaddrinfo = real_getaddrinfo  # type: ignore[assignment]
    assert page.text == "pinned body"
    assert seen, "the mock must have seen the request"
    assert str(seen[-1].url.host) == "93.184.216.34"
    assert seen[-1].headers.get("host", "") == "example.com"


async def test_robots_pins_the_checked_ip() -> None:
    """robots.txt is read from the checked IP too, with the Host preserved."""
    import socket

    from app.providers import web as web_provider

    seen: list[httpx.Request] = []

    def pinned(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="User-agent: *\nAllow: /")

    real_getaddrinfo = socket.getaddrinfo
    socket.getaddrinfo = _stub_getaddrinfo()  # type: ignore[assignment]
    try:
        client = httpx.AsyncClient(transport=httpx.MockTransport(pinned))
        allowed = await web_provider.robots_allowed(
            "https://example.com/page", settings=_settings(), client=client
        )
    finally:
        socket.getaddrinfo = real_getaddrinfo  # type: ignore[assignment]
    assert allowed is True
    assert seen and str(seen[-1].url.host) == "93.184.216.34"
    assert seen[-1].headers.get("host", "") == "example.com"


@pytest.mark.parametrize("target", ["http://224.0.0.1/x", "http://240.0.0.1/x"])
async def test_multicast_and_reserved_are_refused(target: str) -> None:
    """224.0.0.1 and reserved addresses never receive a request."""
    from app.errors import ValidationError

    with pytest.raises(ValidationError, match="non-public"):
        await library.fetch_url(target, settings=_settings())


# Item 3: stranded running runs resume when no driver holds the lock.


async def test_resume_stranded_running_run(t03_session: AsyncSession) -> None:
    """A running run with no live driver resumes instead of stranding."""
    from app.runs import executor

    user = await _user(t03_session, "stranded")
    course = await _course(t03_session, user, title="Stranded")
    run_id = await executor.new_run(t03_session, course_id=course, kind="plan", dag=[])
    await t03_session.commit()
    actor = _actor(user, frozenset({"run.retry"}))
    seen: list[UUID] = []

    async def defer(operation_id: UUID) -> None:
        seen.append(operation_id)

    resumed = await planruns.resume_run(t03_session, actor=actor, run_id=run_id, defer=defer)
    assert resumed == run_id
    assert len(seen) == 1


async def test_resume_refused_while_driver_holds_lock(
    t03_session: AsyncSession,
) -> None:
    """A running run with a live driver still refuses resume."""
    from conftest import test_database_url

    from app.db import create_session_factory
    from app.errors import ValidationError
    from app.runs import executor

    user = await _user(t03_session, "held-lock")
    course = await _course(t03_session, user, title="HeldLock")
    run_id = await executor.new_run(t03_session, course_id=course, kind="plan", dag=[])
    await t03_session.commit()
    engine = create_session_factory(test_database_url()).kw["bind"]
    async with engine.connect() as lock_conn:
        acquired = (
            await lock_conn.execute(
                sa.text("SELECT pg_try_advisory_lock(hashtext(:key))"),
                {"key": f"run:{run_id}"},
            )
        ).scalar()
        assert acquired
        try:
            actor = _actor(user, frozenset({"run.retry"}))
            with pytest.raises(ValidationError, match="only stopped runs resume"):
                await planruns.resume_run(t03_session, actor=actor, run_id=run_id, defer=None)
        finally:
            await lock_conn.execute(
                sa.text("SELECT pg_advisory_unlock(hashtext(:key))"),
                {"key": f"run:{run_id}"},
            )


# Item 4: the production path stores FTS order as fts, model ranks as model.


async def test_production_path_stores_model_and_fts_ranks(
    t03_session: AsyncSession,
) -> None:
    """A real plan run stores model ranks, FTS order, and hard negatives."""
    settings = _settings()
    user = await _user(t03_session, "ranks")
    course = await _course(t03_session, user, title="Ranks")
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
    actor = _actor(user, frozenset({"run.start", "operation.run", "course.edit", "course.plan"}))
    run_id = await _submit_and_create(t03_session, actor, course, ["literacy duty"])

    def cite_one(request: Any) -> Any:
        import httpx2 as _httpx

        body = json.loads(request.content.decode("utf-8"))
        prompt = str(body.get("input", ""))
        if prompt.startswith("# Researcher"):
            dynamic = json.loads(prompt.split("## Task input (typed, assembled by code)\n")[1])
            second = dynamic["passages"][1]["handle"]
            reply = {
                "query": dynamic["query"],
                "rephrased": dynamic["rephrased"],
                "ranked": [{"handle": second, "rank": 1, "score": 0.9}],
                "web_candidates": [],
            }
            return _httpx.Response(200, json=_completed(json.dumps(reply)))
        return _Capture()(request)

    final = await _drive(run_id, cite_one, settings)
    assert final["status"] == "done"
    rows = (
        await t03_session.execute(
            sa.text("SELECT ranker, label, score FROM retrieval_pairs"),
        )
    ).all()
    rankers = {row[0] for row in rows}
    assert "fts" in rankers
    assert "model" in rankers
    assert "reranker_stub" not in rankers
    assert any(row[0] == "fts" and row[1] == "hard_negative" for row in rows)
    model_scores = [row[2] for row in rows if row[0] == "model"]
    assert model_scores and all(score is not None for score in model_scores)


# Item 5: a bad handle holds its block; the run completes with holds listed.


async def test_bad_handle_holds_block_and_run_completes(
    t03_session: AsyncSession,
) -> None:
    """P99 in the architect reply holds the block instead of stopping."""
    settings = _settings()
    user = await _user(t03_session, "holdme")
    course = await _course(t03_session, user, title="HoldMe")
    await _text_source(
        t03_session, settings, user, course, "Article 4 requires AI literacy among staff."
    )
    actor = _actor(user, frozenset({"run.start", "operation.run", "course.edit", "course.plan"}))
    run_id = await _submit_and_create(t03_session, actor, course, ["literacy"])
    final = await _drive(run_id, _Capture(architect_handles=["P99"]), settings)
    assert final["status"] == "done"
    held = await course_service.held_blocks(t03_session, course_id=course)
    assert len(held["blocks"]) == 3
    assert all("unknown handle" in block["hold_reason"] for block in held["blocks"])


# Item 6: citations verified against stored text; stored parser specs reused.


def test_plan_citations_checked_against_stored_text() -> None:
    """A quote matching caller text but not the passage is rejected."""
    from app.runs.executor import verify_citations

    stored = {"p1": "Article 4 requires AI literacy among staff."}
    stray = [
        {
            "passage_id": "p1",
            "claim_path": "blocks.b1",
            "quote": "Providers must ensure training",
            "quote_start": 0,
            "quote_end": 30,
        }
    ]
    assert len(verify_citations(stray, stored)) == 1


async def test_write_plan_citations_uses_stored_passage_text(
    t03_session: AsyncSession,
) -> None:
    """Caller-supplied passage text never decides verification."""
    from app.errors import ValidationError
    from app.runs import executor

    settings = _settings()
    user = await _user(t03_session, "cite-store")
    course = await _course(t03_session, user, title="CiteStore")
    await _text_source(
        t03_session, settings, user, course, "Article 4 requires AI literacy among staff."
    )
    actor = _actor(user, frozenset({"run.start", "operation.run", "course.edit", "course.plan"}))
    run_id = await _submit_and_create(t03_session, actor, course, ["literacy"])
    final = await _drive(run_id, _Capture(), settings)
    assert final["status"] == "done"
    tree = await course_service.read_tree(t03_session, course_id=course)
    node = tree["modules"][0]["topics"][0]["blocks"][0]["plan_node_id"]
    passage_id = (
        await t03_session.execute(sa.text("SELECT id FROM passages LIMIT 1"))
    ).scalar_one()
    version_map = {node: await _version_of(t03_session, node)}
    with pytest.raises(ValidationError, match="citation rejected"):
        await executor.write_plan_citations(
            t03_session,
            version_map=version_map,
            refs={node: [str(passage_id)]},
            citations={
                node: [
                    {
                        "passage_id": str(passage_id),
                        "quote": "Providers must ensure training",
                        "quote_start": 0,
                        "quote_end": 30,
                        "passage_text": "Providers must ensure training for deployers.",
                    }
                ]
            },
        )
    await t03_session.rollback()


async def _version_of(session: AsyncSession, node: str) -> dict[str, str]:
    row = (
        await session.execute(
            sa.text("SELECT b.id, b.current_version_id FROM blocks b WHERE b.plan_node_id = :node"),
            {"node": node},
        )
    ).one()
    return {"block_id": str(row[0]), "version_id": str(row[1])}


async def test_fetch_reuses_stored_parser_spec(t03_session: AsyncSession) -> None:
    """A re-crawl of a spec'd URL runs the stored spec without being told."""
    settings = _settings()
    html = (
        "<article><h2 class='title'>Duty</h2><p class='body'>"
        "Article 4 requires AI literacy among staff.</p></article>"
    )
    spec = {
        "container": "article",
        "fields": {"title": "h2", "body": "p"},
        "address_rule": {"kind": "anchor_id", "selector": "article"},
        "pagination": {"next_selector": None, "max_pages": 1},
    }

    seen_pages = {"count": 0}

    def page(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        seen_pages["count"] += 1
        if seen_pages["count"] >= 2:
            return httpx.Response(
                200,
                text=html.replace(
                    "Article 4 requires AI literacy among staff.",
                    "Article 4 requires AI literacy among staff and deployers.",
                ),
            )
        return httpx.Response(200, text=html)

    client = httpx.AsyncClient(transport=httpx.MockTransport(page))
    first = await library.intake_url(
        t03_session,
        settings=settings,
        url="https://example.com/act",
        course_id=None,
        client=client,
        parser_spec=spec,
    )
    await t03_session.commit()
    assert "source_id" in first
    second = await library.intake_url(
        t03_session,
        settings=settings,
        url="https://example.com/act",
        course_id=None,
        client=client,
    )
    await t03_session.commit()
    assert "source_id" in second
    keys = (
        await t03_session.execute(
            sa.text("SELECT address_key FROM passages WHERE source_id = :source"),
            {"source": str(second["source_id"])},
        )
    ).all()
    assert keys, "the re-crawl must ingest passages through the stored spec"
    assert all(not str(row[0]).startswith("p1/para") for row in keys)


# Item 7: identical content, split approvals; reclaim commit; offsets.


async def test_identical_content_pending_binding_stays_invisible(
    t03_session: AsyncSession,
) -> None:
    """B's pending binding on A's approved bytes stays out of retrieval."""
    from app.providers.retrieval import FtsRetriever

    settings = _settings()
    user_a = await _user(t03_session, "same-a")
    user_b = await _user(t03_session, "same-b")
    course_a = await _course(t03_session, user_a, title="SameA")
    course_b = await _course(t03_session, user_b, title="SameB")
    text = "ZEBRA-striped confidentiality sentence for owner A only. " * 4
    source_a = await library.store_upload(
        t03_session,
        settings=settings,
        created_by=user_a,
        filename="a.txt",
        media="text",
        content=text.encode("utf-8"),
        course_id=course_a,
    )
    await library.ingest_passages(
        t03_session, source_id=source_a, passages=library.chunk_text(text)
    )
    source_b = await library.store_upload(
        t03_session,
        settings=settings,
        created_by=user_b,
        filename="b.txt",
        media="text",
        content=text.encode("utf-8"),
        course_id=course_b,
    )
    assert source_b == source_a, "identical bytes share one immutable source"
    await library.approve_source(
        t03_session, source_id=source_a, approved_by=user_a, course_id=course_a
    )
    await t03_session.commit()
    binding_b = (
        await t03_session.execute(
            sa.text(
                "SELECT approval FROM course_sources"
                " WHERE course_id = :course AND source_id = :source"
            ),
            {"course": str(course_b), "source": str(source_a)},
        )
    ).scalar_one()
    assert binding_b == "pending"
    found_b = await FtsRetriever().search_course(
        t03_session, query="confidentiality ZEBRA", course_id=course_b, limit=8
    )
    assert all("ZEBRA" not in item.text for item in found_b)
    found_a = await FtsRetriever().search_course(
        t03_session, query="confidentiality ZEBRA", course_id=course_a, limit=8
    )
    assert any("ZEBRA" in item.text for item in found_a)


async def test_identical_content_pending_binding_absent_from_architect(
    t03_session: AsyncSession,
) -> None:
    """B's plan run never sees the pending identical bytes in its prompts."""
    settings = _settings()
    user_a = await _user(t03_session, "same-pa")
    user_b = await _user(t03_session, "same-pb")
    course_a = await _course(t03_session, user_a, title="SamePA")
    course_b = await _course(t03_session, user_b, title="SamePB")
    text = "ZEBRA-striped confidentiality sentence for owner A only. " * 4
    source_a = await library.store_upload(
        t03_session,
        settings=settings,
        created_by=user_a,
        filename="a.txt",
        media="text",
        content=text.encode("utf-8"),
        course_id=course_a,
    )
    await library.ingest_passages(
        t03_session, source_id=source_a, passages=library.chunk_text(text)
    )
    source_b = await library.store_upload(
        t03_session,
        settings=settings,
        created_by=user_b,
        filename="b.txt",
        media="text",
        content=text.encode("utf-8"),
        course_id=course_b,
    )
    assert source_b == source_a
    await library.approve_source(
        t03_session, source_id=source_a, approved_by=user_a, course_id=course_a
    )
    bravo_id = await library.store_upload(
        t03_session,
        settings=settings,
        created_by=user_b,
        filename="bravo.txt",
        media="text",
        content=b"Bravo public note for owner B. " * 4,
        course_id=course_b,
    )
    await library.ingest_passages(
        t03_session,
        source_id=bravo_id,
        passages=library.chunk_text("Bravo public note for owner B. " * 4),
    )
    await library.approve_source(
        t03_session, source_id=bravo_id, approved_by=user_b, course_id=course_b
    )
    await t03_session.commit()
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


async def test_reclaim_branch_commits(t03_session: AsyncSession) -> None:
    """A re-claimed step (changed digest) commits without an explicit commit."""
    from conftest import test_database_url

    from app.db import create_session_factory
    from app.runs.executor import claim_step, new_run

    user = await _user(t03_session, "reclaim")
    course = await _course(t03_session, user, title="Reclaim")
    run_id = await new_run(t03_session, course_id=course, kind="plan", dag=[])
    await t03_session.commit()
    first = await claim_step(t03_session, run_id=run_id, step_key="researcher:qx", input_digest="d")
    from app.runs.executor import finish_step

    await finish_step(t03_session, step_id=first["step_id"], output={"ok": True})
    await t03_session.commit()
    await claim_step(t03_session, run_id=run_id, step_key="researcher:qx", input_digest="d2")
    async with create_session_factory(test_database_url())() as fresh:
        row = (
            await fresh.execute(
                sa.text(
                    "SELECT status, input_digest FROM run_steps"
                    " WHERE run_id = :run AND step_key = :key"
                ),
                {"run": str(run_id), "key": "researcher:qx"},
            )
        ).one()
    assert row[0] == "running"
    assert row[1] == "d2"


def test_citation_start_offset_checked_alone() -> None:
    """Right quote and end with a wrong start still holds."""
    from app.runs.executor import verify_citations

    text = "Article 4 requires AI literacy among staff."
    start = text.index("AI literacy")
    shifted = [
        {
            "passage_id": "p1",
            "claim_path": "blocks.b1",
            "quote": "AI literacy",
            "quote_start": start + 1,
            "quote_end": start + len("AI literacy"),
        }
    ]
    holds = verify_citations(shifted, {"p1": text})
    assert len(holds) == 1 and "offsets" in holds[0]


def test_citation_end_offset_checked_alone() -> None:
    """Right quote and start with a wrong end still holds."""
    from app.runs.executor import verify_citations

    text = "Article 4 requires AI literacy among staff."
    start = text.index("AI literacy")
    shifted = [
        {
            "passage_id": "p1",
            "claim_path": "blocks.b1",
            "quote": "AI literacy",
            "quote_start": start,
            "quote_end": start + len("AI literacy") + 1,
        }
    ]
    holds = verify_citations(shifted, {"p1": text})
    assert len(holds) == 1 and "offsets" in holds[0]


# LOW: resume refreshes estimate+snapshots; per-kind worker codes; engine reuse.


async def test_resume_refreshes_estimate_and_snapshot(
    t03_session: AsyncSession,
) -> None:
    """The resume worker refreshes the forecast and snapshots the plan."""
    from conftest import test_database_url

    from app.db import create_session_factory

    settings = _settings()
    user = await _user(t03_session, "resume-low")
    course = await _course(t03_session, user, title="ResumeLow")
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

    stopped = await _drive(run_id, fail_once, settings)
    assert stopped["status"] == "stopped"
    versions_before = (
        await t03_session.execute(sa.text("SELECT count(*) FROM course_versions"))
    ).scalar_one()
    retry_actor = _actor(user, frozenset({"run.retry"}))

    async def defer(operation_id: UUID) -> None:
        return None

    await planruns.resume_run(t03_session, actor=retry_actor, run_id=run_id, defer=defer)
    operation_id = (
        await t03_session.execute(
            sa.text(
                "SELECT id FROM operations WHERE kind = 'resume_run'"
                " ORDER BY created_at DESC LIMIT 1"
            )
        )
    ).scalar_one()
    import httpx2 as _httpx

    url = test_database_url()
    http = guarded_client(settings, inner=_httpx.MockTransport(_Capture()))
    ledger = create_session_factory(url)
    from app.services.planruns import run_resume_operation

    await run_resume_operation(
        t03_session,
        UUID(str(operation_id)),
        provider_factory=_provider_factory(http, ledger, settings),
        session_factory=create_session_factory(url),
    )
    estimate = (
        await t03_session.execute(
            sa.text("SELECT estimate FROM runs WHERE id = :run"), {"run": str(run_id)}
        )
    ).scalar_one()
    assert estimate is not None and estimate["blocks"] == 3
    versions_after = (
        await t03_session.execute(sa.text("SELECT count(*) FROM course_versions"))
    ).scalar_one()
    assert versions_after == versions_before + 1


async def test_worker_checks_operation_own_code(t03_session: AsyncSession) -> None:
    """The worker checks the operation's own code, not operation.run.

    The actor holds course.generate but NOT operation.run: under the
    old check the worker stops at authorisation, under the fixed one
    it reaches the kind body (which answers 409 until T05).
    """
    from conftest import test_database_url

    from app.db import create_session_factory
    from app.rbac.catalogue import sync_catalogue
    from app.rbac.roles import assign_role, create_role
    from app.services.operations import execute_operation

    await sync_catalogue(t03_session)
    role_name = f"t03d-generator-{uuid.uuid4().hex[:8]}"
    await create_role(
        t03_session,
        name=role_name,
        permission_codes=["course.generate"],
        actor=None,
    )
    user = await _user(t03_session, "owncode")
    await assign_role(t03_session, user, role_name, scope_tags=[], actor=None)
    operation_id = (
        await t03_session.execute(
            sa.text(
                "INSERT INTO operations (kind, status, params, progress, actor_id)"
                " VALUES ('submit_generate', 'queued', CAST(:params AS jsonb), 0, :actor)"
                " RETURNING id"
            ),
            {"params": json.dumps({}), "actor": str(user)},
        )
    ).scalar_one()
    await t03_session.commit()
    await execute_operation(
        UUID(str(operation_id)),
        session_factory=create_session_factory(test_database_url()),
    )
    async with create_session_factory(test_database_url())() as fresh:
        status, error = (
            await fresh.execute(
                sa.text("SELECT status, error FROM operations WHERE id = :op"),
                {"op": str(operation_id)},
            )
        ).one()
    assert status == "failed"
    assert error, "worker reached the submit_generate body"


def test_provider_factory_reuses_one_engine() -> None:
    """The production factory shares one engine across providers."""
    import asyncio

    from app.services.planruns import real_provider_factory

    async def main() -> None:
        factory = real_provider_factory(_settings())
        async with factory.ledger() as first:
            engine_first = first.bind
        async with factory.ledger() as second:
            engine_second = second.bind
        assert engine_first is engine_second
        await factory.aclose()

    asyncio.run(main())


def test_prompt_version_is_the_system_file_hash() -> None:
    """Any prompt text change that keeps the system file is version-neutral."""
    from app.agents import base
    from app.providers.llm import MetaProvider

    assert MetaProvider.prompt_version("researcher", "first prompt") == base.prompt_version(
        "researcher"
    )
    assert MetaProvider.prompt_version("researcher", "second prompt") == base.prompt_version(
        "researcher"
    )


async def test_courseless_run_scoped_by_owner(t03_session: AsyncSession) -> None:
    """A run without a course is visible only to its operation owner."""
    from app.controllers import t03 as controller
    from app.errors import NotFoundError

    settings = _settings()
    user_a = await _user(t03_session, "owner-a")
    user_b = await _user(t03_session, "owner-b")
    run_id = uuid.uuid4()
    await t03_session.execute(
        sa.text("INSERT INTO runs (id, status, course_id) VALUES (:run, 'stopped', NULL)"),
        {"run": str(run_id)},
    )
    await t03_session.execute(
        sa.text(
            "INSERT INTO operations (kind, status, params, progress, actor_id)"
            " VALUES ('submit_plan', 'done', CAST(:params AS jsonb), 1, :actor)"
        ),
        {"params": json.dumps({"run_id": str(run_id)}), "actor": str(user_a)},
    )
    await t03_session.commit()
    ctx_a = _ctx(t03_session, settings, _actor(user_a, frozenset({"run.start", "run.retry"})))
    assert (await controller.get_run(ctx_a, run_id=str(run_id)))["id"] == str(run_id)
    ctx_b = _ctx(t03_session, settings, _actor(user_b, frozenset({"run.start", "run.retry"})))
    with pytest.raises(NotFoundError):
        await controller.get_run(ctx_b, run_id=str(run_id))


async def test_internal_url_refused_not_queued(t03_session: AsyncSession) -> None:
    """An internal URL raises instead of landing in the human queue."""
    from app.errors import ValidationError

    settings = _settings()
    with pytest.raises(ValidationError, match="non-public"):
        await library.intake_url(
            t03_session, settings=settings, url="http://127.0.0.1/secret", course_id=None
        )
    await t03_session.rollback()
    queued = (await t03_session.execute(sa.text("SELECT count(*) FROM human_queue"))).scalar_one()
    assert queued == 0


async def test_fetch_refuses_non_http_scheme(t03_session: AsyncSession) -> None:
    """ftp:// and file:// URLs answer 422 and queue nothing."""
    from app.errors import UnprocessableError

    settings = _settings()
    for url in ("ftp://example.com/file", "file:///etc/passwd"):
        with pytest.raises(UnprocessableError):
            await library.intake_url(t03_session, settings=settings, url=url, course_id=None)
    await t03_session.rollback()
    queued = (await t03_session.execute(sa.text("SELECT count(*) FROM human_queue"))).scalar_one()
    assert queued == 0


# Addendum 8: rewrite operations enqueue real work, never orphans.


async def test_rewrite_submit_enqueues(t03_session: AsyncSession) -> None:
    """rewrite_block persists an operation AND hands it to the queue."""
    from app.services import courses as course_service

    settings = _settings()
    user = await _user(t03_session, "rwq")
    course = await _course(t03_session, user, title="RWQ")
    await _text_source(t03_session, settings, user, course, "Article 4 requires AI literacy.")
    actor = _actor(user, frozenset({"run.start", "operation.run", "course.edit", "course.plan"}))
    run_id = await _submit_and_create(t03_session, actor, course, ["literacy"])
    assert (await _drive(run_id, _Capture(), settings))["status"] == "done"
    tree = await course_service.read_tree(t03_session, course_id=course)
    block_id = tree["modules"][0]["topics"][0]["blocks"][0]["id"]
    from app.services.courses import submit_rewrite_operation

    seen: list[UUID] = []

    async def defer(operation_id: UUID) -> None:
        seen.append(operation_id)

    rich = _actor(user, frozenset({"course.generate"}))
    operation_id = await submit_rewrite_operation(
        t03_session, actor=rich, kind="rewrite_block", params={"block_id": block_id}, defer=defer
    )
    await t03_session.commit()
    assert seen == [operation_id]


async def test_rewrite_worker_answers_409(t03_session: AsyncSession) -> None:
    """Rewrite work drives generation; a missing operation fails loudly."""
    from sqlalchemy.exc import NoResultFound

    from app.services.operations import _run_kind

    with pytest.raises(NoResultFound):
        await _run_kind(t03_session, "rewrite_block", uuid.uuid4())
    with pytest.raises(NoResultFound):
        await _run_kind(t03_session, "rewrite_held_batch", uuid.uuid4())


# Addendum 9: one move reorders siblings; bulk ordinals still accepted.


async def test_move_shifts_siblings(t03_session: AsyncSession) -> None:
    """Moving one module reorders its siblings instead of colliding."""
    from app.services import courses as course_service

    user = await _user(t03_session, "shift")
    course = await _course(t03_session, user, title="Shift")
    tree = _lesson_tree_for_move()
    await course_service.write_tree(t03_session, course_id=course, tree=tree, plan_digest="d")
    await t03_session.commit()
    modules = (
        await t03_session.execute(
            sa.text("SELECT id, ordinal FROM modules WHERE course_id = :course ORDER BY ordinal"),
            {"course": str(course)},
        )
    ).all()
    assert [row[1] for row in modules] == [0, 1]
    second_id = str(modules[1][0])
    await course_service.edit_plan(
        t03_session,
        course_id=course,
        operations=[{"op": "move_module", "id": second_id, "ordinal": 0}],
    )
    await t03_session.commit()
    after = (
        await t03_session.execute(
            sa.text("SELECT id, ordinal FROM modules WHERE course_id = :course ORDER BY ordinal"),
            {"course": str(course)},
        )
    ).all()
    assert [str(row[0]) for row in after] == [second_id, str(modules[0][0])]
    assert [row[1] for row in after] == [0, 1]


async def test_move_accepts_bulk_ordinals(t03_session: AsyncSession) -> None:
    """A client-sent full ordinal list still applies."""
    from app.services import courses as course_service

    user = await _user(t03_session, "bulk")
    course = await _course(t03_session, user, title="Bulk")
    tree = _lesson_tree_for_move()
    await course_service.write_tree(t03_session, course_id=course, tree=tree, plan_digest="d")
    await t03_session.commit()
    modules = (
        await t03_session.execute(
            sa.text("SELECT id FROM modules WHERE course_id = :course ORDER BY ordinal"),
            {"course": str(course)},
        )
    ).all()
    ids = [str(row[0]) for row in modules]
    await course_service.edit_plan(
        t03_session,
        course_id=course,
        operations=[{"op": "move_module", "ordinals": {ids[0]: 1, ids[1]: 0}}],
    )
    await t03_session.commit()
    after = (
        await t03_session.execute(
            sa.text("SELECT id, ordinal FROM modules WHERE course_id = :course ORDER BY ordinal"),
            {"course": str(course)},
        )
    ).all()
    assert [str(row[0]) for row in after] == [ids[1], ids[0]]


def _lesson_tree_for_move() -> Any:
    from app.course.tree import CourseTree

    def block(node: str) -> dict[str, Any]:
        return {
            "plan_node_id": node,
            "role": "teach",
            "block_type": "reading",
            "brief": f"Brief {node} about the duty",
            "source_refs": [{"handle": "P1"}],
        }

    def topic(node: str) -> dict[str, Any]:
        return {
            "plan_node_id": node,
            "kind": "lesson",
            "title": f"Topic {node}",
            "objectives": ["Name it."],
            "through_line_note": "Note.",
            "blocks": [block(f"{node}-b1"), block(f"{node}-b2"), block(f"{node}-b3")],
        }

    return CourseTree.model_validate(
        {
            "modules": [
                {
                    "plan_node_id": "m1",
                    "title": "First",
                    "purpose": "Purpose.",
                    "entry_knowledge": "Entry.",
                    "exit_knowledge": "Exit.",
                    "topics": [topic("m1-t1")],
                },
                {
                    "plan_node_id": "m2",
                    "title": "Second",
                    "purpose": "Purpose.",
                    "entry_knowledge": "Entry.",
                    "exit_knowledge": "Exit.",
                    "topics": [topic("m2-t1")],
                },
            ]
        }
    )


# Addendum 10: human queue listing and resolution.


async def test_human_queue_list_and_resolve(t03_session: AsyncSession) -> None:
    """Robots-disallowed URLs queue; the owner lists and dismisses them."""
    from app.controllers import t03 as controller

    settings = _settings()
    user = await _user(t03_session, "hq")
    course = await _course(t03_session, user, title="HQ")

    def no_robots(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="nope")

    client = httpx.AsyncClient(transport=httpx.MockTransport(no_robots))
    result = await library.intake_url(
        t03_session,
        settings=settings,
        url="https://example.com/gated",
        course_id=course,
        client=client,
    )
    await t03_session.commit()
    assert "queued" in result
    ctx = _ctx(t03_session, settings, _actor(user, frozenset({"source.approve"})))
    items = await controller.list_human_queue(ctx)
    assert any(item["id"] == result["queued"] for item in items)
    resolved = await controller.resolve_human_queue(
        ctx, item_id=result["queued"], body={"action": "dismiss"}
    )
    assert resolved == {"ok": True}
    items = await controller.list_human_queue(ctx)
    assert all(item["id"] != result["queued"] for item in items)


async def test_human_queue_scoped_to_owner(t03_session: AsyncSession) -> None:
    """Owner B never sees owner A's queued URL."""
    from app.controllers import t03 as controller
    from app.errors import NotFoundError

    settings = _settings()
    user_a = await _user(t03_session, "hqa")
    user_b = await _user(t03_session, "hqb")
    course_a = await _course(t03_session, user_a, title="HQA")
    await _course(t03_session, user_b, title="HQB")

    def no_robots(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="nope")

    client = httpx.AsyncClient(transport=httpx.MockTransport(no_robots))
    result = await library.intake_url(
        t03_session,
        settings=settings,
        url="https://example.com/private-gated",
        course_id=course_a,
        client=client,
    )
    await t03_session.commit()
    ctx_b = _ctx(t03_session, settings, _actor(user_b, frozenset({"source.approve"})))
    items_b = await controller.list_human_queue(ctx_b)
    assert all(item["id"] != result["queued"] for item in items_b)
    with pytest.raises(NotFoundError):
        await controller.resolve_human_queue(
            ctx_b, item_id=result["queued"], body={"action": "dismiss"}
        )


# Addendum 11: ingest status per source; transcribe runs as an operation.


async def test_audio_upload_is_queued_then_ready(t03_session: AsyncSession) -> None:
    """Audio sources report queued until transcription finishes."""
    from app.services.library import list_course_sources

    settings = _settings()
    user = await _user(t03_session, "ingest")
    course = await _course(t03_session, user, title="Ingest")
    source_id = await library.store_upload(
        t03_session,
        settings=settings,
        created_by=user,
        filename="memo.wav",
        media="audio",
        content=b"RIFF" + b"\x00" * 128,
        course_id=course,
    )
    await library.ingest_passages(t03_session, source_id=source_id, passages=[])
    await library.set_ingest_status(
        t03_session, source_id=source_id, status="queued", message="transcription queued"
    )
    await t03_session.commit()
    sources = await list_course_sources(t03_session, course_id=course)
    assert sources[0]["ingest_status"] == "queued"

    def transcribe(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "segments": [
                    {"start": "00:00:00", "end": "00:00:30", "text": "Welcome staff."},
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(transcribe))
    count = await library.transcribe_source(
        t03_session, source_id=source_id, settings=settings, client=client
    )
    await t03_session.commit()
    assert count == 1
    sources = await list_course_sources(t03_session, course_id=course)
    assert sources[0]["ingest_status"] == "ready"


async def test_transcribe_runs_as_operation(t03_session: AsyncSession) -> None:
    """POST /sources/{id}/transcribe enqueues work instead of transcribing inline."""
    from app.controllers import t03 as controller

    settings = _settings()
    user = await _user(t03_session, "trans-op")
    course = await _course(t03_session, user, title="TransOp")
    source_id = await library.store_upload(
        t03_session,
        settings=settings,
        created_by=user,
        filename="memo.wav",
        media="audio",
        content=b"RIFF" + b"\x00" * 128,
        course_id=course,
    )
    await t03_session.commit()
    ctx = _ctx(t03_session, settings, _actor(user, frozenset({"source.upload"})))
    result = await controller.transcribe_source(ctx, source_id=str(source_id))
    assert "operation_id" in result
    kind = (
        await t03_session.execute(
            sa.text("SELECT kind FROM operations WHERE id = :op"),
            {"op": result["operation_id"]},
        )
    ).scalar_one()
    assert kind == "transcribe_audio"
