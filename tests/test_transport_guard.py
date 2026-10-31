"""Guard unit tests: host, model, price, and ceiling enforcement."""

from __future__ import annotations

import json
from decimal import Decimal

import httpx2
import pytest
from transport_guard import GuardedTransport, GuardViolation

from app.config import Settings

BASE = "https://api.meta.ai/v1"


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def _post(url: str = f"{BASE}/responses", model: str = "muse-spark-1.3") -> httpx2.Request:
    return httpx2.Request("POST", url, content=json.dumps({"model": model}).encode())


def _usage_response(
    input_tokens: int = 1000, cached: int = 100, output_tokens: int = 500
) -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "usage": {
                "input_tokens": input_tokens,
                "input_tokens_details": {"cached_tokens": cached},
                "output_tokens": output_tokens,
            }
        },
    )


def _inner_seen(response: httpx2.Response | None = None) -> tuple[httpx2.MockTransport, dict]:
    seen: dict = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["hit"] = True
        return response or httpx2.Response(200, json={"usage": {}})

    return httpx2.MockTransport(handler), seen


async def test_blocks_other_hosts() -> None:
    inner, _ = _inner_seen()
    guard = GuardedTransport(_settings(), inner)
    with pytest.raises(GuardViolation, match="allowlisted"):
        await guard.handle_async_request(_post("https://example.com/v1/x"))


async def test_blocks_unknown_models_without_touching_network() -> None:
    inner, seen = _inner_seen()
    guard = GuardedTransport(_settings(), inner)
    with pytest.raises(Exception, match="gpt-"):
        await guard.handle_async_request(_post(model="gpt-4o"))
    assert "hit" not in seen


async def test_contributor_needs_dev_mode() -> None:
    inner, _ = _inner_seen()
    with pytest.raises(GuardViolation, match="LLM_DEV_MODE"):
        await GuardedTransport(_settings(), inner).handle_async_request(
            _post(model="muse-spark-1.3-contributor")
        )
    allowed = GuardedTransport(_settings(llm_dev_mode=True), inner)
    await allowed.handle_async_request(_post(model="muse-spark-1.3-contributor"))
    assert allowed.calls == 1


def test_image_model_has_no_token_price() -> None:
    from app.llm.registry import UnpricedModelError, compute_cost_usd

    with pytest.raises(UnpricedModelError, match="muse-image-1.0"):
        compute_cost_usd("muse-image-1.0", 10, 0, 10)


async def test_spend_accumulates_and_ceiling_refuses() -> None:
    inner, _ = _inner_seen(_usage_response())
    guard = GuardedTransport(_settings(llm_spend_ceiling_usd=0.01), inner)
    await guard.handle_async_request(_post())
    # fresh 900 * 1.25 + cached 100 * 0.15 + out 500 * 4.25, per 1M.
    assert guard.spent_usd == Decimal("0.003265")
    guard._settings = _settings(llm_spend_ceiling_usd=0.001)
    with pytest.raises(GuardViolation, match="ceiling"):
        await guard.handle_async_request(_post())
    assert guard.calls == 1


async def test_spend_recorded_for_streaming_response() -> None:
    # Real transports hand back an unread stream; the guard must buffer
    # before parsing usage (regression: ResponseNotRead on live calls).
    payload = json.dumps(
        {
            "usage": {
                "input_tokens": 1000,
                "input_tokens_details": {"cached_tokens": 100},
                "output_tokens": 500,
            }
        }
    ).encode()

    class _Chunks(httpx2.AsyncByteStream):
        async def __aiter__(self):  # type: ignore[no-untyped-def]
            yield payload

    class _Streaming(httpx2.AsyncHTTPTransport):
        async def handle_async_request(  # type: ignore[no-untyped-def]
            self, request
        ):
            return httpx2.Response(200, stream=_Chunks())

    guard = GuardedTransport(_settings(), _Streaming())
    await guard.handle_async_request(_post())
    assert guard.spent_usd == Decimal("0.003265")


async def test_get_without_model_passes_host_check_only() -> None:
    inner, seen = _inner_seen()
    guard = GuardedTransport(_settings(), inner)
    request = httpx2.Request("GET", f"{BASE}/responses/r1")
    await guard.handle_async_request(request)
    assert seen["hit"]


async def test_ceiling_boundary_allows_exact_fit_then_refuses() -> None:
    inner, _ = _inner_seen(_usage_response())
    guard = GuardedTransport(_settings(llm_spend_ceiling_usd=0.003265), inner)
    await guard.handle_async_request(_post())
    assert guard.spent_usd == Decimal("0.003265")
    assert guard.calls == 1
    with pytest.raises(GuardViolation, match="ceiling"):
        await guard.handle_async_request(_post())
    assert guard.calls == 1


async def test_background_poll_get_is_charged_to_the_post_model() -> None:
    inner, _ = _inner_seen(_usage_response())
    guard = GuardedTransport(_settings(), inner)
    await guard.handle_async_request(_post())
    await guard.handle_async_request(httpx2.Request("GET", f"{BASE}/responses/r1"))
    assert guard.calls == 2
    assert guard.spent_usd == Decimal("0.003265") * 2


async def test_list_shaped_cached_tokens_are_counted() -> None:
    payload = _usage_response()
    data = json.loads(payload.content.decode())
    data["usage"]["input_tokens_details"] = [{"cached_tokens": 100}]
    response = httpx2.Response(200, json=data)
    inner, _ = _inner_seen(response)
    guard = GuardedTransport(_settings(), inner)
    await guard.handle_async_request(_post())
    assert guard.spent_usd == Decimal("0.003265")


async def test_image_model_passes_the_unit_price_check() -> None:
    inner, seen = _inner_seen()
    guard = GuardedTransport(_settings(), inner)
    await guard.handle_async_request(_post(model="muse-image-1.0"))
    assert seen["hit"]
    assert guard.calls == 1
    assert guard.spent_usd == Decimal("0")


async def test_session_guard_blocks_unguarded_clients() -> None:
    from transport_guard import GuardViolation as SessionViolation

    raw = httpx2.AsyncClient()
    try:
        with pytest.raises(SessionViolation, match="allowlisted"):
            await raw.post("https://example.com/v1/responses", json={"model": "muse-spark-1.3"})
        with pytest.raises(Exception, match="gpt-"):
            await raw.post(f"{BASE}/responses", json={"model": "gpt-4o"})
    finally:
        await raw.aclose()


async def test_session_guard_enforces_its_own_ceiling() -> None:
    from transport_guard import GuardViolation as SessionViolation
    from transport_guard import install_session_guard, reset_session_spend

    reset_session_spend()
    uninstall = install_session_guard(_settings(llm_spend_ceiling_usd=0.0))
    raw = httpx2.AsyncClient()
    try:
        with pytest.raises(SessionViolation, match="ceiling"):
            await raw.post(f"{BASE}/responses", json={"model": "muse-spark-1.3"})
    finally:
        await raw.aclose()
        uninstall()
        reset_session_spend()
