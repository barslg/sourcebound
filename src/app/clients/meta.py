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
from app.errors import ConfigurationError, LLMError
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


def build_request_body(
    *,
    model: str,
    prompt: str,
    schema: dict[str, Any],
    background: bool = False,
    prompt_cache_key: str | None = None,
) -> dict[str, Any]:
    """The exact kwargs sent to responses.create.

    The provider hashes this same dict for the reply store, so the hash
    can never drift from what the API receives: model, input, format,
    background, and cache key are all part of the key.
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
    }
    if background:
        body["background"] = True
    if prompt_cache_key is not None:
        body["prompt_cache_key"] = prompt_cache_key
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
                    f"model call failed after {attempt} attempt(s): {redact(str(exc))}"
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
                    f"model call failed after {attempt} attempt(s): {redact(str(exc))}"
                ) from exc
            if use_bg:
                response = await self._await_background(response.id)
            return self._parse(response)

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
                raise LLMError(f"background poll failed: {redact(str(exc))}") from exc
            if response.status == "completed":
                return response
            if response.status in ("failed", "cancelled", "incomplete"):
                raise LLMError(f"background response ended as {response.status}")
            if time.monotonic() >= deadline:
                raise LLMError("background response polling timed out")
            await asyncio.sleep(max(min(2.0, deadline - time.monotonic()), 0))

    @staticmethod
    def _parse(response: Any) -> LLMResponse:
        try:
            data = json.loads(response.output_text)
        except (ValueError, AttributeError) as exc:
            raise LLMError("model did not return strict JSON") from exc
        if not isinstance(data, dict):
            raise LLMError("model did not return a JSON object")
        usage = response.usage
        if usage is None:
            raise LLMError("model response carried no usage")
        cached = 0
        details = getattr(usage, "input_tokens_details", None)
        if isinstance(details, dict):
            cached = int(details.get("cached_tokens") or 0)
        elif isinstance(details, list):
            for item in details:
                cached += int(getattr(item, "cached_tokens", None) or 0)
        return LLMResponse(
            data=data,
            input_tokens=int(usage.input_tokens),
            cached_tokens=cached,
            output_tokens=int(usage.output_tokens),
        )
