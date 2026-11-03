"""T06a test world: a published course with all eight block types and enrolled learners.

Everything is built through the production paths: write_tree and
publish_course for the course, create_invite / accept_invite for
enrolment (the student role the invite seeds is the one under test),
create_session for the cookie. Callers: the T06a test modules.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import demo_t05
import httpx
import sqlalchemy as sa
from conftest import test_database_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import Settings

SENTINELS = (
    "SENTINEL_READING_ANSWER_KEY_do_not_render",
    "SENTINEL_FIGURE_ANSWER_KEY_do_not_render",
    "SENTINEL_CHOICE_ANSWER_KEY_do_not_render",
    "SENTINEL_MATCHING_ANSWER_KEY_do_not_render",
    "SENTINEL_CLOZE_ANSWER_KEY_do_not_render",
    "SENTINEL_ORDERING_ANSWER_KEY_do_not_render",
    "SENTINEL_SCENARIO_PRACTICE_ANSWER_KEY_do_not_render",
    "SENTINEL_SPOT_THE_ERROR_ANSWER_KEY_do_not_render",
)


CHOICE_OPTION = "SENTINEL_CHOICE_CORRECT_OPTION"
MATCH_RIGHTS = ("SENTINEL_MATCH_RIGHT_0", "SENTINEL_MATCH_RIGHT_1")
CLOZE_BLANKS = ("SENTINEL_CLOZE_BLANK_0", "SENTINEL_CLOZE_BLANK_1")
TIERS = ("SENTINEL_TIER_HIGH", "SENTINEL_TIER_LOW")
SPOT_TRUE = "SENTINEL_SPOT_TRUE_"
SERVED_VALUES = (CHOICE_OPTION, *MATCH_RIGHTS)
KEY_ONLY_VALUES = (*CLOZE_BLANKS, *TIERS)


def app_settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url=test_database_url(),
        session_secret_key="t06a-session-secret",  # type: ignore[arg-type]
        session_secure_cookie=False,
        model_api_key="synthetic-test-key",  # type: ignore[arg-type]
        model_api_base_url="https://api.meta.ai/v1",
        llm_spend_ceiling_usd=50.0,
        source_store_path="/tmp/muse-t06a-sources",
    )


def factory() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(test_database_url(), poolclass=NullPool)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def http(
    cookies: dict[str, str] | None = None, provider_factory: Any = None
) -> AsyncIterator[httpx.AsyncClient]:
    """An ASGI client on the real app; the engine is disposed on exit."""
    from app import db as db_module
    from app.main import create_app

    app = create_app(app_settings(), sync_on_startup=False)
    maker = factory()

    async def _session() -> Any:
        sess = maker()
        try:
            yield sess
            await sess.commit()
        except Exception:
            await sess.rollback()
            raise
        finally:
            await sess.close()

    app.dependency_overrides[db_module.get_session] = _session
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
        cookies=cookies or {},
    )
    try:
        yield client
    finally:
        await client.aclose()
        bind = maker.kw.get("bind")
        if bind is not None:
            await bind.dispose()


async def cookie_for(session: AsyncSession, user_id: UUID) -> tuple[dict[str, str], str]:
    from app.auth.session import cookie_name_for, cookie_value_for, create_session

    token, csrf = await create_session(session, user_id)
    await session.commit()
    settings = app_settings()
    name = cookie_name_for(settings.app_env, settings.session_cookie_name)
    secret = settings.session_secret_key.get_secret_value() if settings.session_secret_key else None
    return {name: cookie_value_for(token, secret)}, csrf


async def make_user(session: AsyncSession, prefix: str) -> UUID:
    row = (
        await session.execute(
            sa.text("INSERT INTO users (email, display_name) VALUES (:e, :n) RETURNING id"),
            {"e": demo_t05.fresh_email(prefix), "n": prefix},
        )
    ).one()
    await session.commit()
    return UUID(str(row[0]))


async def enrol(session: AsyncSession, *, owner_id: UUID, user_id: UUID, tag: str) -> None:
    """Enrol through the real invite path (student role scoped to `tag`)."""
    from app.rbac.catalogue import sync_catalogue
    from app.rbac.principal import Principal
    from app.services.invites import accept_invite, create_invite, hash_invite_token

    await sync_catalogue(session)
    await session.commit()
    admin = Principal(
        user_id=owner_id,
        email="owner@example.com",
        roles=("author",),
        permissions=frozenset({"invite.create"}),
        scope_tags=(),
        is_superadmin=False,
    )
    token = await create_invite(session, actor=admin, course_tags=[tag])
    await accept_invite(session, user_id=user_id, token_hash=hash_invite_token(token))
    await session.commit()


def _quote(passage: dict[str, str]) -> str:
    return passage["text"][:60]


def block_specs(passages: list[dict[str, str]], prefix: str) -> list[dict[str, Any]]:
    """The seven types from their fixtures, with real handles and verbatim quotes."""
    from importlib import import_module

    content_passages = [p for p in passages if ">Article" in p["address"]]
    p1, p2 = content_passages[0], content_passages[1]
    chips = [
        {"handle": "P1", "quote": _quote(p1), "_pid": p1["id"]},
        {"handle": "P2", "quote": _quote(p2), "_pid": p2["id"]},
    ]

    def fx(name: str) -> tuple[dict[str, Any], dict[str, Any]]:
        return import_module(f"block_fixtures.{name}").fixture()  # type: ignore[no-any-return]

    def public(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{k: v for k, v in c.items() if not k.startswith("_")} for c in items]

    specs: list[dict[str, Any]] = []

    def add(
        name: str,
        role: str,
        topic: str,
        content: dict[str, Any],
        key: dict[str, Any],
        cites: list[dict[str, Any]],
        node: str | None = None,
    ) -> None:
        specs.append(
            {
                "node": node or f"{prefix}-{name}",
                "type": name,
                "role": role,
                "topic": topic,
                "content": content,
                "key": key,
                "cites": cites,
            }
        )

    content, key = fx("reading")
    content["citations"] = public(chips[:1])
    add("reading", "teach", "t1", content, key, chips[:1])
    content, key = fx("figure")
    content["citation"] = public(chips[1:2])[0]
    add("figure", "example", "t1", content, key, chips[1:2])
    content, key = fx("choice")
    content["citations"] = public(chips[:1])
    content["options"][0] = CHOICE_OPTION
    key["correct"] = [0]
    add("choice", "check", "t1", content, key, chips[:1])
    content, key = fx("matching")
    content["citations"] = public(chips[:1])
    for pair, right in zip(content["pairs"], MATCH_RIGHTS, strict=True):
        pair["right"] = right
    key["mapping"] = {"Provider": MATCH_RIGHTS[0], "Deployer": MATCH_RIGHTS[1]}
    key["mapping_by_index"] = {"0": 0, "1": 1}
    add("matching", "practice", "t1", content, key, chips[:1])
    content, key = fx("cloze")
    key["blanks"] = list(CLOZE_BLANKS)
    add("cloze", "practice", "t1", content, key, chips[:1])
    content, key = fx("ordering")
    content["citations"] = public(chips[:1])
    add("ordering", "practice", "t1", content, key, chips[:1])
    content, key = fx("scenario_practice")
    content["items"] = [
        {"text": "CV screening", "handle": "P1"},
        {"text": "Spell check", "handle": "P2"},
    ]
    content["citations"] = public(chips)
    key["labels"] = {"CV screening": TIERS[0], "Spell check": TIERS[1]}
    key["labels_by_index"] = {"0": TIERS[0], "1": TIERS[1]}
    key["passages_by_index"] = {"0": "P1", "1": "P2"}
    add("scenario_practice", "practice", "t1", content, key, chips)
    content, key = fx("spot_the_error")
    key["passage"] = "P1"
    key["true_sentence"] = f"{SPOT_TRUE}LESSON"
    add("spot_the_error", "practice", "t1", content, key, chips[:1])
    add(
        "spot_the_error",
        "practice",
        "t1",
        {"sentences": ["Deployers must train staff.", "Providers must keep records."]},
        {
            "planted_sentence": "",
            "planted_index": -1,
            "kind": "negation",
            "passage": "P1",
            "true_sentence": f"{SPOT_TRUE}CLEAN",
            "fully_correct": True,
            "note": SENTINELS[6],
        },
        chips[:1],
        node=f"{prefix}-spot-clean",
    )
    # the module test topic: choice + matching + spot
    content, key = fx("choice")
    content["citations"] = public(chips[:1])
    content["options"][0] = CHOICE_OPTION
    key["correct"] = [0]
    add("choice", "test", "t2", content, key, chips[:1], node=f"{prefix}-mt-choice")
    content, key = fx("matching")
    content["citations"] = public(chips[:1])
    for pair, right in zip(content["pairs"], MATCH_RIGHTS, strict=True):
        pair["right"] = right
    key["mapping"] = {"Provider": MATCH_RIGHTS[0], "Deployer": MATCH_RIGHTS[1]}
    key["mapping_by_index"] = {"0": 0, "1": 1}
    add("matching", "test", "t2", content, key, chips[:1], node=f"{prefix}-mt-matching")
    content, key = fx("spot_the_error")
    key["passage"] = "P1"
    key["true_sentence"] = f"{SPOT_TRUE}TEST"
    add("spot_the_error", "test", "t2", content, key, chips[:1], node=f"{prefix}-mt-spot")
    return specs


async def seed_world(
    session: AsyncSession, *, prefix: str, publish: bool = True, language: str = "en"
) -> dict[str, Any]:
    """Course, approved passages, 10 blocks in 2 topics, citations rows, published."""
    from app.course.tree import BlockSkeleton, CourseTree, ModuleNode, SourceRef, TopicNode
    from app.services import courses

    settings = app_settings()
    demo = await demo_t05.setup_demo(
        session, settings=settings, email=demo_t05.fresh_email(f"{prefix}-owner")
    )
    if language != "en":
        await session.execute(
            sa.text("UPDATE courses SET language = :l WHERE id = :c"),
            {"l": language, "c": str(demo["course_id"])},
        )
    specs = block_specs(demo["passages"], f"{prefix}-{uuid.uuid4().hex[:6]}")
    topics: dict[str, list[dict[str, Any]]] = {"t1": [], "t2": []}
    for spec in specs:
        topics[spec["topic"]].append(spec)

    def skeleton(spec: dict[str, Any]) -> BlockSkeleton:
        return BlockSkeleton(
            plan_node_id=spec["node"],
            role=spec["role"],
            block_type=spec["type"],
            brief="brief",
            source_refs=[SourceRef(handle="P1")],
        )

    suffix = uuid.uuid4().hex[:8]
    tree = CourseTree(
        modules=[
            ModuleNode(
                plan_node_id=f"m-{suffix}",
                title="Duties",
                purpose="p",
                entry_knowledge="e",
                exit_knowledge="x",
                topics=[
                    TopicNode(
                        plan_node_id=f"t1-{suffix}",
                        kind="lesson",
                        title="Who must act",
                        objectives=["name the duty"],
                        through_line_note="c",
                        blocks=[skeleton(s) for s in topics["t1"]],
                    ),
                    TopicNode(
                        plan_node_id=f"t2-{suffix}",
                        kind="module_test",
                        title="Module test",
                        objectives=["show it"],
                        through_line_note="c",
                        blocks=[skeleton(s) for s in topics["t2"]],
                    ),
                ],
            )
        ]
    )
    version_map = await courses.write_tree(
        session, course_id=demo["course_id"], tree=tree, plan_digest="t06a"
    )
    blocks: dict[str, dict[str, Any]] = {}
    for spec in specs:
        ids = version_map[spec["node"]]
        await session.execute(
            sa.text(
                "UPDATE block_versions SET content = CAST(:c AS jsonb),"
                " answer_key = CAST(:k AS jsonb) WHERE id = :v"
            ),
            {
                "v": ids["version_id"],
                "c": json.dumps(spec["content"]),
                "k": json.dumps(spec["key"]),
            },
        )
        for cite in spec["cites"]:
            await session.execute(
                sa.text(
                    "INSERT INTO citations (block_version_id, passage_id, claim_path, quote,"
                    " quote_start, quote_end, verified_by) VALUES (:v, :p, 'c', :q, 0, :e, 'code')"
                ),
                {
                    "v": ids["version_id"],
                    "p": cite["_pid"],
                    "q": cite["quote"],
                    "e": len(cite["quote"]),
                },
            )
        blocks[spec["node"]] = {**ids, **spec}
    topic_ids = {
        str(row[0]): str(row[1])
        for row in (
            await session.execute(
                sa.text(
                    "SELECT t.plan_node_id, t.id FROM topics t JOIN modules m ON m.id = t.module_id"
                    " WHERE m.course_id = :c"
                ),
                {"c": str(demo["course_id"])},
            )
        ).all()
    }
    await session.commit()
    if publish:
        await courses.publish_course(session, course_id=demo["course_id"])
        await session.commit()
    by_type: dict[str, dict[str, Any]] = {}
    for block in blocks.values():
        by_type.setdefault(block["type"], block)
    clean_spot = next(b for b in blocks.values() if b["node"].endswith("-spot-clean"))
    return {
        **demo,
        "lesson_topic": topic_ids[f"t1-{suffix}"],
        "test_topic": topic_ids[f"t2-{suffix}"],
        "blocks": blocks,
        "by_type": by_type,
        "clean_spot": clean_spot,
        "test_blocks": [b for b in blocks.values() if b["topic"] == "t2"],
    }


class World:
    async def build(self, session: AsyncSession) -> World:
        from app.rbac.catalogue import sync_catalogue
        from app.rbac.roles import assign_role, create_role

        self.s = session
        self.w = await seed_world(session, prefix="t06g")
        self.other = await seed_world(session, prefix="t06h")
        await session.execute(
            sa.text("DELETE FROM course_sources WHERE course_id = :c"),
            {"c": str(self.other["course_id"])},
        )
        self.course = str(self.w["course_id"])
        self.student = await make_user(session, "g-student")
        await enrol(session, owner_id=self.w["user_id"], user_id=self.student, tag=self.course)
        self.other_student = await make_user(session, "g-other")
        await enrol(
            session,
            owner_id=self.other["user_id"],
            user_id=self.other_student,
            tag=str(self.other["course_id"]),
        )
        await sync_catalogue(session)
        if (
            await session.execute(sa.text("SELECT 1 FROM roles WHERE name = 't06i-author'"))
        ).first() is None:
            await create_role(
                session, name="t06i-author", permission_codes=["course.edit", "run.start"]
            )
        await session.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission_code)"
                " SELECT r.id, c FROM roles r, unnest(CAST(:codes AS text[])) c"
                " WHERE r.name = 't06i-author' ON CONFLICT DO NOTHING"
            ),
            {"codes": ["invite.create", "invite.revoke"]},
        )
        for owner in (self.w["user_id"], self.other["user_id"]):
            await assign_role(session, owner, "t06i-author")
        await session.commit()
        self.cookies: dict[str, tuple[dict[str, str], str] | None] = {
            "anon": None,
            "student": await cookie_for(session, self.student),
            "other_student": await cookie_for(session, self.other_student),
            "owner": await cookie_for(session, self.w["user_id"]),
            "other_owner": await cookie_for(session, self.other["user_id"]),
        }
        return self
