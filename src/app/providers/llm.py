"""LLM provider: one Protocol for generation plus the Meta adapter.

Services depend on the Protocol, never on the adapter. The adapter
short-circuits through the content-addressed reply store before spending
a call, records every call (hits included) in the call log, and never
sees the API key — only clients/meta.py holds it.

Ledger autonomy: the call log row and the reply are written through a
short-lived session that commits immediately after the API returns, so
a later rollback of the caller's flow can never erase a paid call or
under-report total spend.
"""

from __future__ import annotations

import time
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.clients.meta import MetaClient, build_request_body
from app.config import Settings, get_settings
from app.db import create_session_factory
from app.errors import ForbiddenError
from app.llm import calls, prompts, replies
from app.llm.registry import is_allowed
from app.llm.schema import validate_strict_subset


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
        self._client: MetaClient | None = None

    def _get_client(self) -> MetaClient:
        if self._client is None:
            self._client = MetaClient(self._settings, self._http_client)
        return self._client

    @staticmethod
    def prompt_version(agent: str, prompt: str) -> str:
        """Day-1 prompt version: the shared content hash, so any edit re-generates."""
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
    ) -> dict[str, Any]:
        validate_strict_subset(schema, name=agent)
        resolved_model = model or self._settings.llm_default_model
        if not is_allowed(resolved_model, self._settings.llm_dev_mode):
            raise ForbiddenError(f"model {resolved_model} needs LLM_DEV_MODE=1")
        version = self.prompt_version(agent, prompt)
        body = build_request_body(
            model=resolved_model,
            prompt=prompt,
            schema=schema,
            prompt_cache_key=cache_key,
        )
        digest = replies.request_hash(resolved_model, version, body)

        cached = await replies.get_reply(self._session, digest)
        if cached is not None:
            await self._record_call(
                agent=agent,
                model=resolved_model,
                version=version,
                input_tokens=0,
                cached_tokens=0,
                output_tokens=0,
                duration_ms=0,
                body=body,
                data=cached,
                run_id=run_id,
                step_id=step_id,
                store_reply=False,
            )
            return cached

        started = time.monotonic()
        answered = await self._get_client().respond_json(
            model=resolved_model,
            prompt=prompt,
            schema=schema,
            prompt_cache_key=cache_key,
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        await self._record_call(
            agent=agent,
            model=resolved_model,
            version=version,
            input_tokens=answered.input_tokens,
            cached_tokens=answered.cached_tokens,
            output_tokens=answered.output_tokens,
            duration_ms=duration_ms,
            body=body,
            data=answered.data,
            run_id=run_id,
            step_id=step_id,
            store_reply=True,
            digest=digest,
        )
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
        duration_ms: int,
        body: dict[str, Any],
        data: dict[str, Any],
        run_id: UUID | None,
        step_id: UUID | None,
        store_reply: bool,
        digest: str = "",
    ) -> None:
        """Persist the ledger row (and fresh reply) in an autonomous session.

        Commits immediately, independent of the caller's transaction, so a
        paid call survives a later rollback of the step that made it.
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
                duration_ms=duration_ms,
                request=body,
                response=data,
                run_id=run_id,
                step_id=step_id,
            )
            if store_reply:
                await replies.put_reply(ledger, digest, model, version, data)
            await ledger.commit()
