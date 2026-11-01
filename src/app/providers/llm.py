"""LLM provider: one Protocol for generation plus the Meta adapter.

Services depend on the Protocol, never on the adapter. The adapter
short-circuits through the content-addressed reply store before spending
a call, records every call (hits included) in the call log, and never
sees the API key — only clients/meta.py holds it.

Ledger autonomy: the call log row and the reply are written through a
short-lived session that commits immediately after the API returns, so
a later rollback of the caller's flow can never erase a paid call or
under-report total spend.

Replay mode: with MODEL_REPLAY_LEDGER set the network client is swapped for
clients/replay.py, so every other step (reply store, checks, ledger rows)
is the live path and a request the ledger never recorded fails loudly.

Providers: MODEL_PROVIDER=meta (default, the product and the submission)
or anthropic (tests and local development only). The provider maps the
registry tier to the model that really serves it, so the request digest,
the call log row and the cost all carry the real model id, and a reply
stored for one provider is never served as the other's. The anthropic
provider has no background mode: such calls run synchronously.

Background calls: start_background() POSTs once and stores a pending
row holding the response id; resume_pending() polls that id, so a crash
between create and retrieve never re-posts a possibly paid request.
"""

from __future__ import annotations

import time
from typing import Any, Protocol
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.clients.anthropic import AnthropicClient
from app.clients.body import build_body
from app.clients.meta import MetaClient
from app.clients.replay import ReplayClient
from app.config import Settings, get_settings
from app.db import create_session_factory
from app.errors import (
    ConfigurationError,
    ForbiddenError,
    LLMError,
    UnparseableReplyError,
    ValidationError,
)
from app.llm import calls, prompts, replies
from app.llm.registry import is_allowed, resolve_step, serving_model
from app.llm.schema import validate_strict_subset
from app.models.llm import llm_calls


class LLMProvider(Protocol):
    """External text-generation capability: structured JSON in, JSON out."""

    async def generate_json(
        self,
        *,
        agent: str,
        prompt: str,
        schema: dict[str, Any],
        model: str | None = ...,
        cache_key: str | None = ...,
        run_id: UUID | None = ...,
        step_id: UUID | None = ...,
    ) -> dict[str, Any]:
        """Generate one JSON object for an agent; cached replies are free."""
        ...


class MetaProvider:
    """LLMProvider over the Meta Model API with reply-store short-circuit."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
        http_client: Any | None = None,
        ledger_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._http_client = http_client
        self._ledger_factory = ledger_factory or create_session_factory(self._settings.database_url)
        self._client: MetaClient | AnthropicClient | ReplayClient | None = None

    def _get_client(self) -> MetaClient | AnthropicClient | ReplayClient:
        """The MODEL_PROVIDER client, or the free replay client when MODEL_REPLAY_LEDGER is set."""
        if self._client is None:
            if self._settings.model_replay_ledger:
                self._client = ReplayClient(self._settings.model_replay_ledger)
            elif self._settings.model_provider == "anthropic":
                self._client = AnthropicClient(self._settings, self._http_client)
            else:
                self._client = MetaClient(self._settings, self._http_client)
        return self._client

    @staticmethod
    def prompt_version(agent: str, prompt: str) -> str:
        """Version of one agent's prompt: hash of its system file.

        The dynamic prompt text never versions the call: only an edit
        to prompts/<agent>/system.md re-generates cached replies.
        Agents whose system file has not landed yet (future pipeline
        steps) keep the legacy agent:prompt hash instead of failing.
        """
        from app.agents.base import prompt_version as system_version

        try:
            return system_version(agent)
        except ConfigurationError:
            return prompts.version_of(f"{agent}:{prompt}")

    async def generate_json(
        self,
        *,
        agent: str,
        prompt: str,
        schema: dict[str, Any],
        model: str | None = None,
        cache_key: str | None = None,
        run_id: UUID | None = None,
        step_id: UUID | None = None,
        effort: str | None = None,
        max_output_tokens: int | None = None,
        variant: int = 0,
        subject_user_id: UUID | None = None,
        background: bool = False,
        check: Any = None,
    ) -> dict[str, Any]:
        """Generate one JSON object; store the reply only when it checks out.

        `check` is the executor's code validator for this agent's
        output. A reply the checks reject is still recorded as a paid
        call in the ledger, but it never reaches the reply store — so
        a resume (or a fresh plan with the same inputs) asks the model
        again instead of re-serving the same bad reply at $0.
        """
        validate_strict_subset(schema, name=agent)
        if background and self._settings.model_provider != "meta":
            background = False
        if background:
            call_id = await self.start_background(
                agent=agent,
                prompt=prompt,
                schema=schema,
                model=model,
                cache_key=cache_key,
                run_id=run_id,
                step_id=step_id,
                effort=effort,
                max_output_tokens=max_output_tokens,
                variant=variant,
                subject_user_id=subject_user_id,
            )
            return await self.resume_pending(call_id)
        resolved_model, resolved_effort, resolved_tokens = resolve_step(
            agent, model, effort, max_output_tokens
        )
        resolved_model = serving_model(resolved_model, self._settings.model_provider)
        if not is_allowed(resolved_model, self._settings.llm_dev_mode):
            raise ForbiddenError(f"model {resolved_model} needs LLM_DEV_MODE=1")
        version = self.prompt_version(agent, prompt)
        body = build_body(
            model=resolved_model,
            prompt=prompt,
            schema=schema,
            prompt_cache_key=cache_key,
            effort=resolved_effort,
            max_output_tokens=resolved_tokens,
        )
        digest = replies.request_hash(resolved_model, version, body, variant)

        cached = await replies.get_reply(self._session, digest)
        if cached is not None and check is not None and list(check(cached)):
            # a reply the checks reject is never replayed: evict and re-ask
            async with self._ledger_factory() as ledger:
                await replies.delete_reply(ledger, digest)
                await ledger.commit()
            cached = None
        if cached is not None:
            await self._record_call(
                agent=agent,
                model=resolved_model,
                version=version,
                input_tokens=0,
                cached_tokens=0,
                output_tokens=0,
                reasoning_tokens=0,
                duration_ms=0,
                body=body,
                data=cached,
                run_id=run_id,
                step_id=step_id,
                variant=variant,
                subject_user_id=subject_user_id,
                store_reply=False,
            )
            return cached

        started = time.monotonic()
        budget = resolved_tokens
        for ask in (1, 2):
            try:
                answered = await self._get_client().respond_json(
                    model=resolved_model,
                    prompt=prompt,
                    schema=schema,
                    prompt_cache_key=cache_key,
                    effort=resolved_effort if ask == 1 else "medium",
                    max_output_tokens=budget,
                )
                break
            except UnparseableReplyError as bad:
                # The call completed and was billed: log it before anything else.
                await self._record_call(
                    agent=agent,
                    model=resolved_model,
                    version=version,
                    input_tokens=bad.input_tokens,
                    cached_tokens=bad.cached_tokens,
                    output_tokens=bad.output_tokens,
                    reasoning_tokens=bad.reasoning_tokens,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    body=body,
                    data={"raw_text": bad.raw_text},
                    run_id=run_id,
                    step_id=step_id,
                    variant=variant,
                    subject_user_id=subject_user_id,
                    store_reply=False,
                    status="rejected",
                )
                # The whole budget spent is a truncation — an empty text (reasoning ate the
                # output) or a JSON cut mid-way (the outline of 400 sections at 6k tokens);
                # ask once more with twice the budget and less reasoning, then give up.
                truncated = bad.output_tokens >= budget
                if ask == 1 and truncated:
                    budget = budget * 2
                    started = time.monotonic()
                    continue
                raise
        duration_ms = int((time.monotonic() - started) * 1000)
        errors: list[str] = list(check(answered.data)) if check is not None else []
        await self._record_call(
            agent=agent,
            model=resolved_model,
            version=version,
            input_tokens=answered.input_tokens,
            cached_tokens=answered.cached_tokens,
            output_tokens=answered.output_tokens,
            reasoning_tokens=answered.reasoning_tokens,
            duration_ms=duration_ms,
            body=body,
            data=answered.data,
            run_id=run_id,
            step_id=step_id,
            variant=variant,
            subject_user_id=subject_user_id,
            store_reply=subject_user_id is None and not errors,
            digest=digest,
        )
        if errors:
            raise ValidationError(f"{agent} output rejected: {errors[0]}")
        return answered.data

    async def start_background(
        self,
        *,
        agent: str,
        prompt: str,
        schema: dict[str, Any],
        model: str | None = None,
        cache_key: str | None = None,
        run_id: UUID | None = None,
        step_id: UUID | None = None,
        effort: str | None = None,
        max_output_tokens: int | None = None,
        variant: int = 0,
        subject_user_id: UUID | None = None,
    ) -> UUID:
        """POST one background call and store a pending row with its id."""
        validate_strict_subset(schema, name=agent)
        resolved_model, resolved_effort, resolved_tokens = resolve_step(
            agent, model, effort, max_output_tokens
        )
        resolved_model = serving_model(resolved_model, self._settings.model_provider)
        if not is_allowed(resolved_model, self._settings.llm_dev_mode):
            raise ForbiddenError(f"model {resolved_model} needs LLM_DEV_MODE=1")
        version = self.prompt_version(agent, prompt)
        body = build_body(
            model=resolved_model,
            prompt=prompt,
            schema=schema,
            background=True,
            prompt_cache_key=cache_key,
            effort=resolved_effort,
            max_output_tokens=resolved_tokens,
        )
        response_id = await self._get_client().start_background(
            model=resolved_model,
            prompt=prompt,
            schema=schema,
            prompt_cache_key=cache_key,
            effort=resolved_effort,
            max_output_tokens=resolved_tokens,
        )
        digest = replies.request_hash(resolved_model, version, body, variant)
        async with self._ledger_factory() as ledger:
            call_id = await calls.log_call(
                ledger,
                agent=agent,
                model=resolved_model,
                prompt_version=version,
                input_tokens=0,
                cached_tokens=0,
                output_tokens=0,
                duration_ms=0,
                request={**body, "request_hash": digest},
                response=None,
                run_id=run_id,
                step_id=step_id,
                response_id=response_id,
                status="pending",
                variant=variant,
                subject_user_id=subject_user_id,
            )
            await ledger.commit()
        return call_id

    async def resume_pending(self, call_id: UUID) -> dict[str, Any]:
        """Poll a pending row's stored response id; never re-posts the call."""
        async with self._ledger_factory() as ledger:
            row = (
                (await ledger.execute(sa.select(llm_calls).where(llm_calls.c.id == call_id)))
                .mappings()
                .one()
            )
            if row["status"] == "completed":
                assert isinstance(row["response"], dict)
                return row["response"]
            if row["status"] != "pending" or not row["response_id"]:
                raise LLMError(f"call {call_id} is not resumable")
            started = time.monotonic()
            try:
                answered = await self._get_client().retrieve_background(str(row["response_id"]))
            except UnparseableReplyError as bad:
                await calls.complete_background_call(
                    ledger,
                    call_id=call_id,
                    input_tokens=bad.input_tokens,
                    cached_tokens=bad.cached_tokens,
                    output_tokens=bad.output_tokens,
                    reasoning_tokens=bad.reasoning_tokens,
                    model=str(row["model"]),
                    duration_ms=int((time.monotonic() - started) * 1000),
                    response={"raw_text": bad.raw_text},
                    status="rejected",
                )
                await ledger.commit()
                raise
            duration_ms = int((time.monotonic() - started) * 1000)
            await calls.complete_background_call(
                ledger,
                call_id=call_id,
                input_tokens=answered.input_tokens,
                cached_tokens=answered.cached_tokens,
                output_tokens=answered.output_tokens,
                reasoning_tokens=answered.reasoning_tokens,
                model=str(row["model"]),
                duration_ms=duration_ms,
                response=answered.data,
            )
            if row["subject_user_id"] is None:
                request = dict(row["request"])
                digest = str(request.pop("request_hash", ""))
                if digest:
                    await replies.put_reply(
                        ledger,
                        digest,
                        str(row["model"]),
                        str(row["prompt_version"]),
                        answered.data,
                    )
            await ledger.commit()
            return answered.data

    async def _record_call(
        self,
        *,
        agent: str,
        model: str,
        version: str,
        input_tokens: int,
        cached_tokens: int,
        output_tokens: int,
        reasoning_tokens: int,
        duration_ms: int,
        body: dict[str, Any],
        data: dict[str, Any],
        run_id: UUID | None,
        step_id: UUID | None,
        variant: int,
        subject_user_id: UUID | None,
        store_reply: bool,
        digest: str = "",
        status: str = "completed",
    ) -> None:
        """Persist the ledger row (and fresh reply) in an autonomous session.

        Commits immediately, independent of the caller's transaction, so a
        paid call survives a later rollback of the step that made it.
        Learner-subject calls are never written to the shared reply store.
        """
        async with self._ledger_factory() as ledger:
            await calls.log_call(
                ledger,
                agent=agent,
                model=model,
                prompt_version=version,
                input_tokens=input_tokens,
                cached_tokens=cached_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                duration_ms=duration_ms,
                request=body,
                response=data,
                run_id=run_id,
                step_id=step_id,
                variant=variant,
                subject_user_id=subject_user_id,
                status=status,
            )
            if store_reply:
                await replies.put_reply(ledger, digest, model, version, data)
            await ledger.commit()
