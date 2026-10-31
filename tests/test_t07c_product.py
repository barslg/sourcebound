"""T07c product path on the mock provider: translate, serve, grade, recover.

Every test drives the product path on the real test database: a course
tree written through write_tree, submit_translate, the worker body
(run_translate_operation / execute_operation) through the compiled DAG
with the same handlers the worker uses, and the learner preview route
over HTTP. Only the model transport is a mock. Mutants M9, M14, M16 and
M17 of build/notes/T07b-review.md have the tests named after them.
Caller of every helper here: these tests.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from typing import Any
from uuid import UUID

import demo_t05
import httpx
import httpx2
import pytest
import sqlalchemy as sa
from conftest import test_database_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from test_t07c_probes import EN_FILLER, _live_pair
from transport_guard import guarded_client

from app.config import Settings
from app.providers.llm import MetaProvider
from app.services import planruns

NB_PREFIX = (
    "Dette er og skal være slik at det ikke bare gjelder når som kan eller må men om denne også "
    "derfor fordi mellom gjennom etter før blir av hva: "
)
TERM_MAP = {
    "deployers": "anvendere",
    "ensure": "sikre",
    "office": "kontoret",
    "training": "opplæring",
}


def _apply_terms(text: str, term_map: dict[str, str]) -> str:
    for source, target in term_map.items():
        text = re.sub(rf"\b{source}\b", target, text, flags=re.IGNORECASE)
    return text


def _factory() -> async_sessionmaker[AsyncSession]:
    """Session factory without a pool: tests open many, none may hold a connection."""
    engine = create_async_engine(test_database_url(), poolclass=NullPool)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class TranslatorRig:
    """Mock translator: nb-looking output, glossary-aware, per-batch switches."""

    def __init__(self, term_map: dict[str, str] | None = None) -> None:
        self.term_map = term_map or {}
        self.posts: list[str] = []
        self.echo_once: set[str] = set()
        self.drift_marker = ""
        self.drift_map: dict[str, str] = {}

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content.decode("utf-8"))
        prompt = str(body.get("input", ""))
        self.posts.append(prompt)
        if "## Terms\n" in prompt:
            terms = json.loads(prompt.split("## Terms\n", 1)[1].split("\n\n## ")[0])
            reply: dict[str, Any] = {
                "segments": [{"id": t, "text": self.term_map.get(t, t)} for t in terms]
            }
            return httpx2.Response(200, json=demo_t05._completed(json.dumps(reply)))  # noqa: SLF001
        segments = json.loads(prompt.split("## Segments\n", 1)[1])
        out = []
        for seg in segments:
            text = str(seg["text"])
            marker = next((m for m in self.echo_once if str(seg["id"]).startswith(m)), None)
            if marker is not None:
                self.echo_once.discard(marker)
                out.append({"id": seg["id"], "text": text})
                continue
            mapping = (
                self.drift_map if self.drift_marker and self.drift_marker in text else self.term_map
            )
            out.append({"id": seg["id"], "text": f"{NB_PREFIX}{_apply_terms(text, mapping)} [nb]"})
        return httpx2.Response(200, json=demo_t05._completed(json.dumps({"segments": out})))  # noqa: SLF001


class Env:
    """Settings, provider factory over the rig, and the actor builder."""

    def __init__(self, rig: TranslatorRig | None = None) -> None:
        self.rig = rig or TranslatorRig()
        self.settings = Settings(
            _env_file=None,
            model_api_key="synthetic-test-key",
            model_api_base_url="https://api.meta.ai/v1",
            llm_spend_ceiling_usd=50.0,
            source_store_path="/tmp/muse-t07c-product",
        )
        self.factory = _factory()
        http = guarded_client(self.settings, inner=httpx2.MockTransport(self.rig.handler))

        def make(session: AsyncSession) -> MetaProvider:
            return MetaProvider(session, self.settings, http, self.factory)

        self.provider_factory: Callable[[AsyncSession], MetaProvider] = make

    def actor(self, user_id: UUID) -> Any:
        from app.rbac.principal import Principal

        return Principal(
            user_id=user_id,
            email="t07c@example.com",
            roles=("author",),
            permissions=frozenset({"course.generate", "run.retry"}),
            scope_tags=(),
            is_superadmin=False,
        )


def _specs() -> list[dict[str, Any]]:
    true_en, planted_en = _live_pair()
    return [
        {
            "node": "b1",
            "type": "reading",
            "content": {
                "body": "Deployers must ensure staff training and safety at work in the office.",
                "citations": [{"passage_id": "P1", "quote": "q"}],
            },
            "key": {},
        },
        {
            "node": "b2",
            "type": "choice",
            "content": {
                "stem": "Who must ensure staff training at work and in the office?",
                "options": ["Deployers who are here", "Nobody who is here"],
            },
            "key": {"correct": 0},
        },
        {
            "node": "b3",
            "type": "cloze",
            "content": {
                "text": "___ must ensure ___ among staff and workers in the office today.",
                "blanks": ["___", "___"],
            },
            "key": {"blanks": ["Deployers", "training"]},
        },
        {
            "node": "b4",
            "type": "spot_the_error",
            "content": {"sentences": [EN_FILLER, planted_en]},
            "key": {
                "planted_sentence": planted_en,
                "planted_index": 1,
                "kind": "negation",
                "passage": "P1",
                "true_sentence": true_en,
                "fully_correct": False,
            },
        },
    ]


_ROLE = {"reading": "teach", "choice": "check", "cloze": "practice", "spot_the_error": "practice"}


async def _seed(
    session: AsyncSession, env: Env, prefix: str, specs: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    from app.course.tree import BlockSkeleton, CourseTree, ModuleNode, SourceRef, TopicNode
    from app.services import courses
    from app.services.courses import write_tree

    chosen = specs if specs is not None else _specs()
    demo = await demo_t05.setup_demo(
        session, settings=env.settings, email=demo_t05.fresh_email(prefix)
    )
    blocks = [
        BlockSkeleton(
            plan_node_id=s["node"],
            role=_ROLE[s["type"]],
            block_type=s["type"],
            brief="b",
            source_refs=[SourceRef(handle="P1")],
        )
        for s in chosen
    ]
    tree = CourseTree(
        modules=[
            ModuleNode(
                plan_node_id="m1",
                title="M",
                purpose="p",
                entry_knowledge="e",
                exit_knowledge="x",
                topics=[
                    TopicNode(
                        plan_node_id="t1",
                        kind="lesson",
                        title="T",
                        objectives=["o"],
                        through_line_note="c",
                        blocks=blocks,
                    )
                ],
            )
        ]
    )
    await write_tree(session, course_id=demo["course_id"], tree=tree, plan_digest="t07c")
    for s in chosen:
        row = (
            await session.execute(
                sa.text(
                    "SELECT b.current_version_id FROM blocks b JOIN topics t ON t.id = b.topic_id"
                    " JOIN modules m ON m.id = t.module_id"
                    " WHERE b.plan_node_id = :n AND m.course_id = :c"
                ),
                {"n": s["node"], "c": str(demo["course_id"])},
            )
        ).one()
        await session.execute(
            sa.text(
                "UPDATE block_versions SET content = CAST(:c AS jsonb),"
                " answer_key = CAST(:k AS jsonb) WHERE id = :v"
            ),
            {"v": str(row[0]), "c": json.dumps(s["content"]), "k": json.dumps(s["key"])},
        )
    await courses.snapshot_version(session, course_id=demo["course_id"])
    await session.commit()
    return demo


async def _translate(
    session: AsyncSession,
    env: Env,
    demo: dict[str, Any],
    languages: tuple[str, ...] = ("nb",),
    glossary: dict[str, str] | None = None,
) -> dict[str, Any]:
    op = await planruns.submit_translate(
        session,
        actor=env.actor(demo["user_id"]),
        course_id=demo["course_id"],
        languages=list(languages),
        glossary=glossary,
    )
    return await planruns.run_translate_operation(
        session, op, provider_factory=env.provider_factory, session_factory=env.factory
    )


async def _rows(session: AsyncSession, course_id: UUID, lang: str = "nb") -> dict[str, Any]:
    rows = (
        await session.execute(
            sa.text(
                "SELECT tb.plan_node_id, tb.status, tb.reason, tb.content,"
                " tb.translated_answer_key FROM translation_blocks tb"
                " JOIN course_translations ct ON ct.id = tb.translation_id"
                " JOIN course_versions cv ON cv.id = ct.course_version_id"
                " WHERE cv.course_id = :c AND ct.language = :l ORDER BY ct.created_at, 1"
            ),
            {"c": str(course_id), "l": lang},
        )
    ).all()
    return {
        str(r[0]): {
            "status": str(r[1]),
            "reason": str(r[2] or ""),
            "content": dict(r[3] or {}),
            "key": dict(r[4] or {}),
        }
        for r in rows
    }


# --- HTTP harness ---------------------------------------------------------


def _app_settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url=test_database_url(),
        session_secret_key="t07c-session-secret",  # type: ignore[arg-type]
        session_secure_cookie=False,
    )


async def _actor_cookie(
    session: AsyncSession, user_id: UUID, role: str, codes: list[str]
) -> tuple[dict[str, str], str]:
    from app.auth.session import cookie_name_for, cookie_value_for, create_session
    from app.rbac.catalogue import sync_catalogue
    from app.rbac.roles import assign_role, create_role

    await sync_catalogue(session)
    exists = (
        await session.execute(sa.text("SELECT 1 FROM roles WHERE name = :n"), {"n": role})
    ).first()
    if exists is None:
        await create_role(session, name=role, permission_codes=codes)
    await assign_role(session, user_id, role)
    token, csrf = await create_session(session, user_id)
    await session.commit()
    settings = _app_settings()
    name = cookie_name_for(settings.app_env, settings.session_cookie_name)
    secret = settings.session_secret_key.get_secret_value() if settings.session_secret_key else None
    return {name: cookie_value_for(token, secret)}, csrf


async def _http(cookies: dict[str, str] | None = None) -> httpx.AsyncClient:
    from app import db as db_module
    from app.main import create_app

    app = create_app(_app_settings())
    factory = _factory()

    async def _session() -> Any:
        sess = factory()
        try:
            yield sess
            await sess.commit()
        except Exception:
            await sess.rollback()
            raise
        finally:
            await sess.close()

    app.dependency_overrides[db_module.get_session] = _session
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
        cookies=cookies or {},
    )


async def _other_user(session: AsyncSession, prefix: str) -> UUID:
    row = (
        await session.execute(
            sa.text("INSERT INTO users (email, display_name) VALUES (:e, 'O') RETURNING id"),
            {"e": demo_t05.fresh_email(prefix)},
        )
    ).one()
    await session.commit()
    return UUID(str(row[0]))


# --- M9: worker dispatch of submit_translate ---------------------------------


async def test_m9_worker_dispatch_runs_submit_translate(
    t03_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M9: execute_operation -> _run_kind -> run_translate_operation, as the job does."""
    from app.services.operations import execute_operation

    env = Env()
    demo = await _seed(t03_session, env, "m9")
    await _actor_cookie(
        t03_session, demo["user_id"], "t07c-author", ["course.generate", "learner.view"]
    )
    op_id = await planruns.submit_translate(
        t03_session,
        actor=env.actor(demo["user_id"]),
        course_id=demo["course_id"],
        languages=["nb"],
    )
    monkeypatch.setattr(planruns, "real_provider_factory", lambda _settings: env.provider_factory)
    await execute_operation(op_id, session_factory=env.factory)
    op = (
        await t03_session.execute(
            sa.text("SELECT status, result, error FROM operations WHERE id = :o"),
            {"o": str(op_id)},
        )
    ).one()
    assert op[0] == "done", op
    run = (
        await t03_session.execute(
            sa.text("SELECT status, kind FROM runs WHERE id = :r"), {"r": op[1]["run_id"]}
        )
    ).one()
    assert tuple(run) == ("done", "translation")
    rows = await _rows(t03_session, demo["course_id"])
    assert sorted(rows) == ["b1", "b2", "b3", "b4"], rows
    assert all(r["status"] == "ok" for r in rows.values()), {
        k: (v["status"], v["reason"]) for k, v in rows.items()
    }


# --- M14 / M16: learner preview route over HTTP --------------------------------


async def test_m14_m16_learner_route_serves_translation_to_the_owner_only(
    t03_session: AsyncSession,
) -> None:
    env = Env()
    demo = await _seed(t03_session, env, "m14")
    await _translate(t03_session, env, demo)
    codes = ["learner.view", "course.generate"]
    owner_cookies, _ = await _actor_cookie(t03_session, demo["user_id"], "t07c-view", codes)
    other = await _other_user(t03_session, "m16")
    other_cookies, _ = await _actor_cookie(t03_session, other, "t07c-view", codes)
    nobody = await _other_user(t03_session, "m16-noperm")
    url = f"/api/v1/courses/{demo['course_id']}/learner"
    async with await _http(owner_cookies) as client:
        nb = await client.get(url, params={"lang": "nb"})
        assert nb.status_code == 200, nb.text
        blocks = {b["plan_node_id"]: b for b in nb.json()["blocks"]}
        # M14: a view that ignores translations serves the English body
        assert blocks["b1"]["content"]["body"].startswith(NB_PREFIX), blocks["b1"]
        assert blocks["b1"]["language"] == "nb" and blocks["b1"]["fallback_notice"] is None
        header = await client.get(url, headers={"Accept-Language": "nb-NO,nb;q=0.9"})
        assert header.json()["blocks"][0]["language"] == "nb"
        en = await client.get(url, params={"lang": "en"})
        assert en.json()["blocks"][0]["content"]["body"].startswith("Deployers must")
        # the preview never carries an answer key
        text = nb.text
        for leak in ("answer_key", "planted_sentence", "true_sentence", '"correct"'):
            assert leak not in text, leak
    async with await _http(other_cookies) as client:
        # M16: a route without the owner check answers 200 here
        assert (await client.get(url, params={"lang": "nb"})).status_code == 404
    async with await _http() as client:
        assert (await client.get(url)).status_code == 401
    async with await _http({}) as client:
        assert (await client.get(url)).status_code == 401
    nobody_cookies, _ = await _actor_cookie(t03_session, nobody, "t07c-none", ["course.create"])
    async with await _http(nobody_cookies) as client:
        assert (await client.get(url)).status_code == 403


# --- M17: the translated key is the translation, not the source -------------------


async def test_m17_translated_key_is_not_the_source_key(t03_session: AsyncSession) -> None:
    from app.translation.variants import learner_block

    env = Env(TranslatorRig(TERM_MAP))
    demo = await _seed(t03_session, env, "m17")
    await _translate(t03_session, env, demo)
    rows = await _rows(t03_session, demo["course_id"])
    cloze = rows["b3"]
    assert cloze["status"] == "ok", cloze
    blanks = cloze["key"]["blanks"]
    assert blanks != ["Deployers", "training"], "M17: source key stored as the translated key"
    assert all(b.startswith(NB_PREFIX) and "anvendere" in blanks[0] for b in blanks[:1]), blanks
    spot = rows["b4"]
    assert spot["status"] == "ok", spot
    true_en, planted_en = _live_pair()
    assert spot["key"]["true_sentence"] != true_en
    assert spot["key"]["planted_sentence"] == spot["content"]["sentences"][1] != planted_en
    # the grader contract: content and key of ONE language, translated when ok
    source = next(s for s in _specs() if s["node"] == "b3")
    served = await learner_block(
        t03_session,
        course_id=demo["course_id"],
        language="nb",
        block={
            "plan_node_id": "b3",
            "block_type": "cloze",
            "content": source["content"],
            "answer_key": source["key"],
        },
        source_language="en",
    )
    assert served["language"] == "nb" and served["answer_key"]["blanks"] == blanks
    assert served["content"]["text"] == cloze["content"]["text"]
    fallback = await learner_block(
        t03_session,
        course_id=demo["course_id"],
        language="uk",
        block={
            "plan_node_id": "b3",
            "block_type": "cloze",
            "content": source["content"],
            "answer_key": source["key"],
        },
        source_language="en",
    )
    assert fallback["language"] == "en" and fallback["answer_key"] == source["key"]
    assert fallback["fallback_notice"] and "ukrainsk" not in fallback["fallback_notice"]


async def test_translated_cloze_and_choice_grade_like_the_source(
    t03_session: AsyncSession,
) -> None:
    from app.blocks.choice.check import grade as grade_choice
    from app.blocks.cloze.check import grade as grade_cloze

    env = Env(TranslatorRig(TERM_MAP))
    demo = await _seed(t03_session, env, "grade")
    await _translate(t03_session, env, demo)
    rows = await _rows(t03_session, demo["course_id"])
    cloze = rows["b3"]
    nb = grade_cloze(cloze["content"], cloze["key"], {"blanks": cloze["key"]["blanks"]})
    en = grade_cloze(
        _specs()[2]["content"], _specs()[2]["key"], {"blanks": ["Deployers", "training"]}
    )
    assert nb["score"] == en["score"] == 1.0
    wrong = grade_cloze(cloze["content"], cloze["key"], {"blanks": ["Deployers", "training"]})
    assert wrong["score"] < 1.0, "the English answer is wrong on the nb exercise"
    choice = rows["b2"]
    assert grade_choice(choice["content"], choice["key"], {"picked": 0})["score"] == 1.0


async def test_parity_submissions_from_raw_replies_not_from_the_key(
    t03_session: AsyncSession,
) -> None:
    """The live parity method, run on the mock path: right scores 1.0, wrong does not."""
    from importlib import import_module

    from t07_parity import english_submissions, raw_segments, translated_submissions

    env = Env(TranslatorRig(TERM_MAP))
    demo = await _seed(t03_session, env, "parity")
    result = await _translate(t03_session, env, demo)
    rows = await _rows(t03_session, demo["course_id"])
    for spec in _specs():
        if spec["type"] not in ("choice", "cloze", "spot_the_error"):
            continue
        grade = import_module(f"app.blocks.{spec['type']}.check").grade
        raw = await raw_segments(
            t03_session, run_id=UUID(result["run_id"]), node=spec["node"], language="nb"
        )
        row = rows[spec["node"]]
        en_right, en_wrong = english_submissions(spec["type"], spec["key"], spec["content"])
        nb_right, nb_wrong = translated_submissions(spec["type"], spec["key"], row["content"], raw)
        en = grade(spec["content"], spec["key"], en_right)["score"]
        nb = grade(row["content"], row["key"], nb_right)["score"]
        assert nb == en == 1.0, (spec["node"], nb, en)
        assert grade(row["content"], row["key"], nb_wrong)["score"] < 1.0, spec["node"]
        assert grade(spec["content"], spec["key"], en_wrong)["score"] < 1.0, spec["node"]


# --- MEDIUM 5: translations survive publish, die with a block change --------------


async def test_publish_after_translate_serves_nb_and_a_block_change_falls_back(
    t03_session: AsyncSession,
) -> None:
    from app.services.courses import publish_course

    env = Env()
    demo = await _seed(t03_session, env, "publish")
    await _translate(t03_session, env, demo)
    await publish_course(t03_session, course_id=demo["course_id"])
    await t03_session.commit()
    view = await planruns.learner_course_view(
        t03_session, course_id=demo["course_id"], language="nb"
    )
    assert [b["language"] for b in view["blocks"]] == ["nb"] * 4, view["blocks"]
    assert [lang["language"] for lang in view["available_languages"]] == ["nb"]
    # the author changes b1: its translation is stale, the others stay
    version = (
        await t03_session.execute(
            sa.text(
                "SELECT b.current_version_id FROM blocks b JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id"
                " WHERE b.plan_node_id = 'b1' AND m.course_id = :c"
            ),
            {"c": str(demo["course_id"])},
        )
    ).scalar_one()
    changed = {
        **_specs()[0]["content"],
        "body": "Deployers must ensure that every report is filed.",
    }
    await t03_session.execute(
        sa.text("UPDATE block_versions SET content = CAST(:c AS jsonb) WHERE id = :v"),
        {"c": json.dumps(changed), "v": str(version)},
    )
    await t03_session.commit()
    view = await planruns.learner_course_view(
        t03_session, course_id=demo["course_id"], language="nb"
    )
    by_node = {b["plan_node_id"]: b for b in view["blocks"]}
    assert by_node["b1"]["language"] == "en" and by_node["b1"]["fallback_notice"]
    assert by_node["b1"]["content"]["body"] == changed["body"]
    assert by_node["b2"]["language"] == "nb"


# --- MEDIUM 6: the variant that produced the ok translation is reused ---------------


async def test_p3b_bad_good_good_is_ok_then_ok_for_free(t03_session: AsyncSession) -> None:
    rig = TranslatorRig()
    env = Env(rig)
    demo = await _seed(t03_session, env, "p3b", [s for s in _specs() if s["node"] == "b3"])
    rig.echo_once = {"key:blanks[0]"}
    await _translate(t03_session, env, demo)
    first = (await _rows(t03_session, demo["course_id"]))["b3"]
    assert first["status"] == "held" and "not translated" in first["reason"], first
    await _translate(t03_session, env, demo)
    second = (await _rows(t03_session, demo["course_id"]))["b3"]
    assert second["status"] == "ok", second
    rig.posts.clear()
    await _translate(t03_session, env, demo)
    third = (await _rows(t03_session, demo["course_id"]))["b3"]
    assert third["status"] == "ok", "run after the recovery re-served the variant-0 bad reply"
    assert rig.posts == [], "the recovered translation is reused at $0"
    variant = (
        await t03_session.execute(sa.text("SELECT max(variant) FROM translation_blocks"))
    ).scalar()
    assert variant == 1


# --- MEDIUM 12: the glossary is seeded from the course ---------------------------------


async def test_two_blocks_share_the_seeded_term(t03_session: AsyncSession) -> None:
    rig = TranslatorRig(TERM_MAP)
    env = Env(rig)
    demo = await _seed(t03_session, env, "gloss", _specs()[:2])
    await _translate(t03_session, env, demo)
    glossary_calls = [p for p in rig.posts if "## Terms\n" in p]
    assert glossary_calls, "no glossary pass: the product path passed an empty glossary"
    terms = json.loads(glossary_calls[0].split("## Terms\n", 1)[1].split("\n\n## ")[0])
    assert {"deployers", "ensure", "training"} <= set(terms), terms
    rows = await _rows(t03_session, demo["course_id"])
    assert rows["b1"]["status"] == rows["b2"]["status"] == "ok", rows
    assert "anvendere" in rows["b1"]["content"]["body"]
    assert "anvendere" in rows["b2"]["content"]["options"][0]
    table = (
        await t03_session.execute(sa.text("SELECT glossary FROM course_translations"))
    ).scalar_one()
    assert table["deployers"] == "anvendere"


async def test_term_drift_between_blocks_is_held(t03_session: AsyncSession) -> None:
    rig = TranslatorRig(TERM_MAP)
    rig.drift_marker = "who are here"
    rig.drift_map = {**TERM_MAP, "deployers": "tilbydere"}
    env = Env(rig)
    demo = await _seed(t03_session, env, "drift", _specs()[:2])
    await _translate(t03_session, env, demo)
    rows = await _rows(t03_session, demo["course_id"])
    assert rows["b1"]["status"] == "ok", rows
    assert rows["b2"]["status"] == "held" and "glossary term" in rows["b2"]["reason"], rows


# --- 13: a paid, unparseable reply leaves a ledger row ------------------------------------


async def test_unparseable_paid_reply_is_a_rejected_ledger_row() -> None:
    from test_llm_provider import SCHEMA, _completed, _provider, _sessions

    from app.errors import LLMError
    from app.models.llm import llm_calls

    caller, ledger = _sessions()
    prompt = f"not json {uuid.uuid4().hex}"
    async with caller() as session:
        provider, _seen = _provider(
            session, ledger, lambda request: httpx2.Response(200, json=_completed(text="not json"))
        )
        with pytest.raises(LLMError):
            await provider.generate_json(agent="author", prompt=prompt, schema=SCHEMA)
    async with ledger() as reader:
        rows = (
            (await reader.execute(sa.select(llm_calls).where(llm_calls.c.agent == "author")))
            .mappings()
            .all()
        )
    match = [r for r in rows if r["request"].get("input") == prompt]
    assert len(match) == 1, "a paid reply must leave exactly one ledger row"
    assert match[0]["status"] == "rejected"
    assert match[0]["tokens_input"] == 100 and match[0]["cost_usd"] > 0
    assert "not json" in json.dumps(match[0]["response"])
    async with caller() as session:
        provider, _seen = _provider(
            session, ledger, lambda request: httpx2.Response(200, json=_completed())
        )
        assert await provider.generate_json(agent="author", prompt=prompt, schema=SCHEMA) == {
            "ok": True
        }, "a rejected reply never enters the reply store"


# --- 13: a reader `unknown` re-asks once with variant+1, then holds -----------------------


def _first_pass_t1_reads(rig: Any) -> int:
    """Reader calls of topic t1 before any rework (the re-read carries its flag)."""
    return sum(
        1
        for head, prompt in rig.posts
        if head == "reader"
        and demo_t05._section(prompt, "Block skeleton")["topic_plan_node_id"] == "t1"  # noqa: SLF001
        and "reread_after_rework" not in prompt.split("## Block skeleton\n", 1)[1]
    )


def _unknown_rig(counts: dict[str, int]) -> Any:
    from test_t05c_product import Rig

    class UnknownRig(Rig):
        def _reader(self, prompt: str) -> dict[str, Any]:
            reply = super()._reader(prompt)
            for verdict in reply["verdicts"]:
                node = str(verdict["plan_node_id"])
                if counts.get(node, 0) > 0:
                    counts[node] -= 1
                    verdict["verdict"] = "unknown"
                    for item in verdict["items"].values():
                        item["verdict"] = "unknown"
            return reply

    return UnknownRig()


async def test_reader_unknown_reasks_once_and_accepts(t03_session: AsyncSession) -> None:
    from test_t05c_product import Env as ProductEnv
    from test_t05c_product import _blocks, _built, _status

    rig = _unknown_rig({"b3": 1})
    env = ProductEnv(rig)
    demo = await _built(t03_session, env, "unk1")
    assert await _status(t03_session, demo["gen_run"]) == "done"
    blocks = await _blocks(t03_session, demo["course_id"])
    assert blocks["b3"]["status"] == "accepted", blocks["b3"]
    assert _first_pass_t1_reads(rig) == 2, "unknown must re-ask exactly once"


async def test_reader_unknown_twice_holds_with_a_reason_and_the_run_is_done(
    t03_session: AsyncSession,
) -> None:
    from test_t05c_product import Env as ProductEnv
    from test_t05c_product import _blocks, _built, _status

    rig = _unknown_rig({"b3": 99})
    env = ProductEnv(rig)
    demo = await _built(t03_session, env, "unk2")
    assert await _status(t03_session, demo["gen_run"]) == "done", "run must not stop on unknown"
    blocks = await _blocks(t03_session, demo["course_id"])
    assert blocks["b3"]["status"] == "held", blocks["b3"]
    assert "reader could not decide" in str(blocks["b3"]["hold"])
    assert _first_pass_t1_reads(rig) == 2, "unknown twice holds; it is not asked a third time"


# --- LOW: PATCH /sources/{id}/language ownership, public privacy line ----------------------


async def test_patch_source_language_needs_the_course_owner(t03_session: AsyncSession) -> None:
    env = Env()
    demo = await _seed(t03_session, env, "patch")
    source_id = (
        await t03_session.execute(
            sa.text("SELECT source_id FROM course_sources WHERE course_id = :c"),
            {"c": str(demo["course_id"])},
        )
    ).scalar_one()
    codes = ["source.edit"]
    owner_cookies, owner_csrf = await _actor_cookie(
        t03_session, demo["user_id"], "t07c-edit", codes
    )
    other = await _other_user(t03_session, "patch-other")
    other_cookies, other_csrf = await _actor_cookie(t03_session, other, "t07c-edit", codes)
    url = f"/api/v1/sources/{source_id}/language"
    async with await _http(other_cookies) as client:
        denied = await client.patch(
            url, json={"language": "nb"}, headers={"X-CSRF-Token": other_csrf}
        )
        assert denied.status_code == 404, denied.text
    ts = (
        (
            await t03_session.execute(
                sa.text("SELECT DISTINCT ts_config FROM passages WHERE source_id = :s"),
                {"s": str(source_id)},
            )
        )
        .scalars()
        .all()
    )
    assert ts == ["english"], "a stranger changed the passage language"
    async with await _http(owner_cookies) as client:
        done = await client.patch(
            url, json={"language": "nb"}, headers={"X-CSRF-Token": owner_csrf}
        )
        assert done.status_code == 200, done.text


async def test_privacy_line_is_reachable_signed_out() -> None:
    async with await _http() as client:
        for locale, expected in (
            ("nb", "Vi bruker e-posten din bare til å logge deg inn."),
            ("uk", "Ми використовуємо вашу електронну пошту лише для входу."),
            ("en", "We use your email only to sign you in."),
        ):
            response = await client.get("/api/v1/config/public", params={"lang": locale})
            assert response.status_code == 200, response.text
            assert response.json()["privacy_line"] == expected
        header = await client.get("/api/v1/config/public", headers={"Accept-Language": "nb"})
        assert header.json()["privacy_line"].startswith("Vi bruker")
