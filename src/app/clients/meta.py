"""Meta Model API client over the openai SDK (Responses API).

This is the only module that imports `openai` at runtime. The key and
base URL come from app.config only; the key is never logged, printed,
or stored. Structured calls request strict JSON against the agent's
schema, so the model fills plain text inside structure owned by code.

Exactly-once spending: a dropped connection or a 504 never triggers a
blind re-POST (the request may already be paid for) — the call continues
in the background instead, and background polls retry on their own
budget without ever issuing a second paid request.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx2

from app.config import Settings, get_settings
from app.errors import ConfigurationError, LLMError, UnparseableReplyError
from app.llm.retry import RETRYABLE, backoff_ms, retry_after_ms, should_retry, use_background
from app.llm.schema import validate_strict_subset
from app.redact import redact

if TYPE_CHECKING:
    from openai import AsyncOpenAI


@dataclass(frozen=True)
class LLMResponse:
    """One answered structured call with its token usage."""

    data: dict[str, Any]
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    reasoning_tokens: int = 0


@dataclass(frozen=True)
class WebSearchReply:
    """The results of one web_search tool call: every source the search returned, plus usage."""

    results: list[dict[str, str]]
    searches: int
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    reasoning_tokens: int = 0


def build_search_body(
    *,
    model: str,
    query: str,
    effort: str = "low",
    max_output_tokens: int = 1500,
) -> dict[str, Any]:
    """The exact kwargs of one grounded search: the web_search tool with raw results included."""
    return {
        "model": model,
        "input": query,
        "tools": [{"type": "web_search", "search_context_size": "low"}],
        "include": ["web_search_call.results"],
        "reasoning": {"effort": effort},
        "max_output_tokens": max_output_tokens,
    }


def parse_search_results(response: Any) -> tuple[list[dict[str, str]], int]:
    """(results, searches) from a response's web_search_call items; code reads the URLs."""
    results: list[dict[str, str]] = []
    searches = 0
    for item in getattr(response, "output", None) or []:
        kind = getattr(item, "type", None) or (item.get("type") if isinstance(item, dict) else None)
        if kind != "web_search_call":
            continue
        searches += 1
        raw = getattr(item, "results", None)
        if raw is None and isinstance(item, dict):
            raw = item.get("results")
        for hit in raw or []:
            url = str(_field(hit, "url") or "")
            if not url.startswith(("http://", "https://")):
                continue
            results.append(
                {
                    "url": url,
                    "title": str(_field(hit, "title") or ""),
                    "snippet": str(_field(hit, "snippet") or ""),
                }
            )
    return results, searches


def _field(item: Any, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def build_request_body(
    *,
    model: str,
    prompt: str,
    schema: dict[str, Any],
    background: bool = False,
    prompt_cache_key: str | None = None,
    effort: str = "medium",
    max_output_tokens: int = 4096,
    instructions: str | None = None,
    safety_identifier: str | None = None,
) -> dict[str, Any]:
    """The exact kwargs sent to responses.create.

    The provider hashes this same dict for the reply store, so the hash
    can never drift from what the API receives: model, instructions,
    input, format, background, cache key, reasoning effort, and the
    output limit are all part of the key. The system prompt travels as
    `instructions` (the API's developer-level prefix), the typed task as
    `input`; a cache key also asks for 24h retention, since a course run
    reuses one prefix for hours.
    """
    body: dict[str, Any] = {
        "model": model,
        "input": prompt,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "reply",
                "schema": schema,
                "strict": True,
            }
        },
        "reasoning": {"effort": effort},
        "max_output_tokens": max_output_tokens,
    }
    if background:
        body["background"] = True
    if instructions:
        body["instructions"] = instructions
    if prompt_cache_key is not None:
        body["prompt_cache_key"] = prompt_cache_key
        body["prompt_cache_retention"] = "24h"
    if safety_identifier:
        body["safety_identifier"] = safety_identifier
    return body


class MetaClient:
    """Thin Responses-API wrapper with the HARNESS retry policy."""

    def __init__(
        self,
        settings: Settings | None = None,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        from openai import AsyncOpenAI

        resolved = settings or get_settings()
        secret = resolved.model_api_key
        if secret is None or not secret.get_secret_value():
            raise ConfigurationError("MODEL_API_KEY is not set")
        timeout = httpx2.Timeout(
            connect=resolved.llm_connect_timeout_s,
            read=resolved.llm_read_timeout_s,
            write=resolved.llm_read_timeout_s,
            pool=resolved.llm_connect_timeout_s,
        )
        self._poll_timeout_s = resolved.llm_poll_timeout_s
        self._settings = resolved
        self._client: AsyncOpenAI = AsyncOpenAI(
            base_url=resolved.model_api_base_url,
            api_key=secret.get_secret_value(),
            timeout=timeout,
            # Retries are governed by app.llm.retry, not the SDK.
            max_retries=0,
            http_client=http_client,
        )

    async def respond_json(
        self,
        *,
        model: str,
        prompt: str,
        schema: dict[str, Any],
        background: bool = False,
        prompt_cache_key: str | None = None,
        effort: str = "medium",
        max_output_tokens: int = 4096,
        instructions: str | None = None,
        safety_identifier: str | None = None,
    ) -> LLMResponse:
        """Generate one strict-JSON object; retries per policy, then LLMError."""
        from openai import APIConnectionError, APIStatusError

        validate_strict_subset(schema, name="response")
        attempt = 0
        use_bg = background
        while True:
            attempt += 1
            kwargs = build_request_body(
                model=model,
                prompt=prompt,
                schema=schema,
                background=use_bg,
                prompt_cache_key=prompt_cache_key,
                effort=effort,
                max_output_tokens=max_output_tokens,
                instructions=instructions,
                safety_identifier=safety_identifier,
            )
            try:
                response = await self._client.responses.create(**kwargs)
            except APIConnectionError as exc:
                # The POST may have reached the server, so a blind re-POST
                # could pay twice: continue in the background instead.
                if not use_bg:
                    use_bg = True
                    continue
                raise LLMError(
                    f"model call failed after {attempt} attempt(s): {self._redact(str(exc))}"
                ) from exc
            except APIStatusError as exc:
                status = exc.status_code
                if use_background(status) and not use_bg:
                    use_bg = True
                    continue
                if should_retry(status, attempt):
                    await asyncio.sleep(backoff_ms(attempt, retry_after_ms(exc)) / 1000)
                    continue
                raise LLMError(
                    f"model call failed after {attempt} attempt(s): {self._redact(str(exc))}"
                ) from exc
            if use_bg:
                response = await self._await_background(response.id)
            return self._parse(response)

    def _redact(self, text: str) -> str:
        """Redact with key patterns plus every secret from settings."""
        from app.redact import settings_secrets

        try:
            secrets = settings_secrets(self._settings)
        except Exception:
            secrets = []
        return redact(text, secrets=secrets)

    async def start_background(
        self,
        *,
        model: str,
        prompt: str,
        schema: dict[str, Any],
        prompt_cache_key: str | None = None,
        effort: str = "medium",
        max_output_tokens: int = 4096,
        instructions: str | None = None,
        safety_identifier: str | None = None,
    ) -> str:
        """POST one background call and return its response id for polling.

        The caller persists the id (pending ledger row) before the first
        poll, so a crash between create and retrieve resumes from the id
        instead of re-posting a possibly paid request.
        """
        from openai import APIStatusError

        validate_strict_subset(schema, name="response")
        kwargs = build_request_body(
            model=model,
            prompt=prompt,
            schema=schema,
            background=True,
            prompt_cache_key=prompt_cache_key,
            effort=effort,
            max_output_tokens=max_output_tokens,
            instructions=instructions,
            safety_identifier=safety_identifier,
        )
        try:
            response = await self._client.responses.create(**kwargs)
        except APIStatusError as exc:
            raise LLMError(f"background call was not accepted: {self._redact(str(exc))}") from exc
        return str(response.id)

    async def retrieve_background(self, response_id: str) -> LLMResponse:
        """Poll one previously created background response to completion."""
        return self._parse(await self._await_background(response_id))

    async def search_web(
        self, *, model: str, query: str, effort: str = "low", max_output_tokens: int = 1500
    ) -> WebSearchReply:
        """One grounded search: the model runs web_search, code keeps the result list."""
        from openai import APIConnectionError, APIStatusError

        attempt = 0
        while True:
            attempt += 1
            try:
                response = await self._client.responses.create(
                    **build_search_body(
                        model=model, query=query, effort=effort, max_output_tokens=max_output_tokens
                    )
                )
            except APIConnectionError as exc:
                raise LLMError(f"web search failed: {self._redact(str(exc))}") from exc
            except APIStatusError as exc:
                if should_retry(exc.status_code, attempt):
                    await asyncio.sleep(backoff_ms(attempt, retry_after_ms(exc)) / 1000)
                    continue
                raise LLMError(
                    f"web search failed after {attempt} attempt(s): {self._redact(str(exc))}"
                ) from exc
            results, searches = parse_search_results(response)
            usage = getattr(response, "usage", None)
            if usage is None:
                raise LLMError("web search response carried no usage")
            return WebSearchReply(
                results=results,
                searches=searches,
                input_tokens=int(usage.input_tokens),
                cached_tokens=_tokens_details(usage, "input_tokens_details", "cached_tokens"),
                output_tokens=int(usage.output_tokens),
                reasoning_tokens=_tokens_details(
                    usage, "output_tokens_details", "reasoning_tokens"
                ),
            )

    async def _await_background(self, response_id: str) -> Any:
        """Poll a background response until it completes or polling times out.

        Polls carry their own retry budget for transient poll failures and
        never issue a second paid create.
        """
        from openai import APIConnectionError, APIStatusError

        deadline = time.monotonic() + self._poll_timeout_s
        attempt = 0
        while True:
            try:
                response = await self._client.responses.retrieve(response_id)
            except APIConnectionError as exc:
                if time.monotonic() >= deadline:
                    raise LLMError("background response polling timed out") from exc
                await asyncio.sleep(max(min(2.0, deadline - time.monotonic()), 0))
                continue
            except APIStatusError as exc:
                if exc.status_code in RETRYABLE and time.monotonic() < deadline:
                    attempt += 1
                    wait = backoff_ms(attempt, retry_after_ms(exc)) / 1000
                    await asyncio.sleep(max(min(wait, deadline - time.monotonic()), 0))
                    continue
                raise LLMError(f"background poll failed: {self._redact(str(exc))}") from exc
            if response.status == "completed":
                return response
            if response.status in ("failed", "cancelled", "incomplete"):
                raise LLMError(f"background response ended as {response.status}")
            if time.monotonic() >= deadline:
                raise LLMError("background response polling timed out")
            await asyncio.sleep(max(min(2.0, deadline - time.monotonic()), 0))

    @staticmethod
    def _parse(response: Any) -> LLMResponse:
        """Strict JSON object plus usage; a paid unparseable reply keeps both."""
        usage = getattr(response, "usage", None)
        counts: dict[str, int] = {}
        if usage is not None:
            counts = {
                "input_tokens": int(usage.input_tokens),
                "cached_tokens": _tokens_details(usage, "input_tokens_details", "cached_tokens"),
                "output_tokens": int(usage.output_tokens),
                "reasoning_tokens": _tokens_details(
                    usage, "output_tokens_details", "reasoning_tokens"
                ),
            }
        raw = str(getattr(response, "output_text", "") or "")
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise UnparseableReplyError(
                "model did not return strict JSON", raw_text=raw, **counts
            ) from exc
        if not isinstance(data, dict):
            raise UnparseableReplyError(
                "model did not return a JSON object", raw_text=raw, **counts
            )
        if usage is None:
            raise LLMError("model response carried no usage")
        return LLMResponse(data=data, **counts)


def _tokens_details(usage: Any, field: str, key: str) -> int:
    """Sum one usage detail that may arrive as object, dict, or list of either."""
    details = getattr(usage, field, None)
    if details is None and isinstance(usage, dict):
        details = usage.get(field)
    if details is None:
        return 0
    if isinstance(details, list):
        return sum(_cached_value(item, key) for item in details)
    return _cached_value(details, key)


def _cached_value(item: Any, key: str) -> int:
    if isinstance(item, dict):
        return int(item.get(key) or 0)
    return int(getattr(item, key, None) or 0)
