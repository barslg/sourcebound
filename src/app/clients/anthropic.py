"""Anthropic Messages API client: a second provider for tests and local development.

The product and the hackathon submission run on Meta models only
(clients/meta.py); this client exists so the live pipeline can be
exercised while the Meta key is unusable. It is selected by
MODEL_PROVIDER=anthropic, refused outside APP_ENV local/test, and reads
ANTHROPIC_API_KEY from app.config only; the key is never logged.

Same contract as MetaClient.respond_json: one strict-JSON object plus
usage. Strict JSON uses the structured-output mechanism (output_config
format json_schema); forced tool_choice is not available on the 5.5
models, so a tool definition is not used. There is no background mode:
calls are synchronous, and a dropped connection is never re-POSTed (the
request may already be billed).
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import httpx2

from app.clients.meta import LLMResponse
from app.config import Settings, get_settings
from app.errors import ConfigurationError, LLMError, UnparseableReplyError
from app.llm.retry import backoff_ms, retry_after_ms, should_retry
from app.llm.schema import validate_strict_subset
from app.redact import redact

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic


def to_anthropic_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Our strict-subset schema as the structured-output schema the API accepts.

    The SDK helper folds constraints the API does not enforce (such as
    minLength) into descriptions and keeps additionalProperties false.
    """
    from anthropic import transform_schema

    return dict(transform_schema(schema))


def build_anthropic_body(
    *,
    model: str,
    prompt: str,
    schema: dict[str, Any],
    background: bool = False,
    prompt_cache_key: str | None = None,
    effort: str = "medium",
    max_output_tokens: int = 4096,
) -> dict[str, Any]:
    """The exact kwargs sent to messages.create; the provider hashes this same dict.

    `prompt_cache_key` has no keyed equivalent here: a set key marks the
    prompt block cacheable (prefix caching), an unset key sends no marker.
    `background` is accepted for signature parity and ignored: calls are
    always synchronous.
    """
    block: dict[str, Any] = {"type": "text", "text": prompt}
    if prompt_cache_key is not None:
        block["cache_control"] = {"type": "ephemeral"}
    return {
        "model": model,
        "max_tokens": max_output_tokens,
        "messages": [{"role": "user", "content": [block]}],
        "output_config": {
            "effort": effort,
            "format": {"type": "json_schema", "schema": to_anthropic_schema(schema)},
        },
    }


class AnthropicClient:
    """Thin Messages-API wrapper with the shared retry policy."""

    def __init__(
        self,
        settings: Settings | None = None,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        from anthropic import AsyncAnthropic

        resolved = settings or get_settings()
        secret = resolved.anthropic_api_key
        if secret is None or not secret.get_secret_value():
            raise ConfigurationError("ANTHROPIC_API_KEY is not set")
        self._settings = resolved
        self._client: AsyncAnthropic = AsyncAnthropic(
            base_url=resolved.anthropic_api_base_url,
            api_key=secret.get_secret_value(),
            timeout=httpx2.Timeout(
                connect=resolved.llm_connect_timeout_s,
                read=resolved.llm_read_timeout_s,
                write=resolved.llm_read_timeout_s,
                pool=resolved.llm_connect_timeout_s,
            ),
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
    ) -> LLMResponse:
        """Generate one strict-JSON object; retries per policy, then LLMError."""
        from anthropic import APIConnectionError, APIStatusError

        validate_strict_subset(schema, name="response")
        kwargs = build_anthropic_body(
            model=model,
            prompt=prompt,
            schema=schema,
            prompt_cache_key=prompt_cache_key,
            effort=effort,
            max_output_tokens=max_output_tokens,
        )
        attempt = 0
        while True:
            attempt += 1
            try:
                message = await self._client.messages.create(**kwargs)
            except APIConnectionError as exc:
                # The POST may have reached the server and been billed: no blind re-POST.
                raise LLMError(f"model call failed: {self._redact(str(exc))}") from exc
            except APIStatusError as exc:
                if should_retry(exc.status_code, attempt):
                    await asyncio.sleep(backoff_ms(attempt, retry_after_ms(exc)) / 1000)
                    continue
                raise LLMError(
                    f"model call failed after {attempt} attempt(s): {self._redact(str(exc))}"
                ) from exc
            return self._parse(message)

    async def start_background(self, **_: Any) -> str:
        """There is no background mode on this provider."""
        raise ConfigurationError("provider anthropic has no background mode")

    async def retrieve_background(self, response_id: str) -> LLMResponse:
        """There is no background mode on this provider."""
        raise ConfigurationError("provider anthropic has no background mode")

    def _redact(self, text: str) -> str:
        """Redact with key patterns plus every secret from settings."""
        from app.redact import settings_secrets

        try:
            secrets = settings_secrets(self._settings)
        except Exception:
            secrets = []
        return redact(text, secrets=secrets)

    @staticmethod
    def _parse(message: Any) -> LLMResponse:
        """Strict JSON object plus usage; a paid unparseable reply keeps both.

        Anthropic reports fresh input apart from cache reads and cache
        writes; the shared contract wants total input and the cached part,
        so reads and writes are added back into input_tokens.
        """
        usage = message.usage
        cached = int(getattr(usage, "cache_read_input_tokens", None) or 0)
        written = int(getattr(usage, "cache_creation_input_tokens", None) or 0)
        counts = {
            "input_tokens": int(usage.input_tokens) + cached + written,
            "cached_tokens": cached,
            "output_tokens": int(usage.output_tokens),
        }
        if message.stop_reason == "refusal":
            raise LLMError("model declined the request (refusal)")
        raw = "".join(block.text for block in message.content if block.type == "text")
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
        return LLMResponse(data=data, **counts)
