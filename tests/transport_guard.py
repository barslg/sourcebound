"""httpx transport guard for tests: allowlist host, registry models, spend ceiling.

Every test that touches the network builds its HTTP client through
guarded_client(). The guard forbids any host other than the configured
Model API host, any model id outside the registry (contributor models
only with LLM_DEV_MODE=1, models without a dated price never), and any
further call once the per-run spend ceiling from settings is reached.
Spend is accumulated from real response usage, so a second identical
provider call served from llm_replies adds nothing.

install_session_guard() extends the same rails to every test in the
session: it patches AsyncHTTPTransport so even a client built without
guarded_client() cannot leave the allowlist, with a session-level spend
total read from settings.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from decimal import Decimal
from urllib.parse import urlparse

import httpx2

from app.config import Settings, get_settings
from app.llm.registry import compute_cost_usd, get_model, is_allowed


def _is_priced(model_id: str) -> bool:
    """True when the model has a dated price in its own billing unit."""
    spec = get_model(model_id)
    if spec.unit == "token":
        return (
            spec.input_per_1m_usd is not None
            and spec.cached_per_1m_usd is not None
            and spec.output_per_1m_usd is not None
        )
    return spec.unit_price_usd is not None


class GuardViolation(ValueError):
    """A test tried to spend a model call outside the guard rails."""


def _proxy_from_env() -> str | None:
    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        value = os.environ.get(name)
        if value:
            return value
    return None


def _cached_from_usage(usage: object) -> int:
    """Cached input tokens from a Responses usage payload (dict or list shape)."""
    details: object = None
    if isinstance(usage, dict):
        details = usage.get("input_tokens_details")
    if isinstance(details, dict):
        return int(details.get("cached_tokens", 0) or 0)
    if isinstance(details, list):
        total = 0
        for item in details:
            if isinstance(item, dict):
                total += int(item.get("cached_tokens", 0) or 0)
        return total
    return 0


def _allowed_hosts(settings: Settings) -> set[str]:
    """The Model API host, the only one a test may reach."""
    return {urlparse(settings.model_api_base_url).hostname or ""}


def live_key_name() -> str:
    """The environment variable that holds the key a live run uses."""
    return "MODEL_API_KEY"


def live_key_settings(key: str) -> dict[str, str]:
    """Settings kwargs that carry `key`."""
    return {"model_api_key": key}


def _usage_cost_usd(model: str, content: bytes) -> Decimal:
    """Cost of one Responses payload, or zero when usage is absent/unpriced."""
    try:
        payload = json.loads(content.decode("utf-8"))
        usage = payload.get("usage") or {}
        cached = _cached_from_usage(usage)
        total_input = int(usage.get("input_tokens", 0))
        return compute_cost_usd(model, total_input, cached, int(usage.get("output_tokens", 0)))
    except (ValueError, UnicodeDecodeError, TypeError, KeyError, AttributeError):
        return Decimal("0")


class GuardedTransport(httpx2.AsyncHTTPTransport):
    """Allowlist + ceiling wrapper around a real or mock inner transport."""

    def __init__(
        self,
        settings: Settings | None = None,
        inner: httpx2.AsyncHTTPTransport | None = None,
    ) -> None:
        super().__init__()
        self._settings = settings or get_settings()
        proxy = _proxy_from_env()
        self._inner = inner or httpx2.AsyncHTTPTransport(**({"proxy": proxy} if proxy else {}))
        self.calls = 0
        self.spent_usd = Decimal("0")
        self._last_model: str | None = None

    def _allowed_hosts(self) -> set[str]:
        return _allowed_hosts(self._settings)

    def _check_request(self, request: httpx2.Request) -> str | None:
        if request.url.host not in self._allowed_hosts():
            raise GuardViolation(f"host not allowlisted: {request.url.host}")
        if request.method.upper() != "POST":
            return None
        try:
            body = json.loads(request.content.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise GuardViolation("guarded call must carry a JSON body") from exc
        model = body.get("model")
        if not isinstance(model, str):
            raise GuardViolation("guarded call must name a model")
        get_model(model)  # UnknownModelError propagates as a loud failure.
        if not is_allowed(model, self._settings.llm_dev_mode):
            raise GuardViolation(f"model needs LLM_DEV_MODE=1: {model}")
        if not _is_priced(model):
            raise GuardViolation(f"model has no dated price: {model}")
        ceiling = Decimal(str(self._settings.llm_spend_ceiling_usd))
        if self.spent_usd >= ceiling:
            raise GuardViolation(f"per-run spend ceiling reached: {self.spent_usd} >= {ceiling}")
        self._last_model = model
        return model

    async def _record_spend(self, model: str, response: httpx2.Response) -> None:
        if response.status_code != 200:
            return
        try:
            await response.aread()
        except (ValueError, UnicodeDecodeError, RuntimeError):
            return
        self.spent_usd += _usage_cost_usd(model, response.content)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        model = self._check_request(request)
        self.calls += 1
        response = await self._inner.handle_async_request(request)
        charge = model
        if charge is None and request.method.upper() == "GET":
            # Background polls are GETs: charge the completed retrieve to
            # the model of the most recent POST on this transport.
            charge = self._last_model
        if charge is not None:
            await self._record_spend(charge, response)
        return response


def guarded_client(
    settings: Settings | None = None,
    inner: httpx2.AsyncHTTPTransport | None = None,
) -> httpx2.AsyncClient:
    """Build an httpx2 client whose every call passes the guard.

    trust_env is off so traffic cannot bypass the guard through an
    ambient proxy mapping; the inner transport carries the proxy itself.
    """
    resolved = settings or get_settings()
    return httpx2.AsyncClient(
        transport=GuardedTransport(resolved, inner),
        trust_env=False,
        base_url=resolved.model_api_base_url,
    )


_session_spent_usd = Decimal("0")


def session_spent_usd() -> Decimal:
    """Session-level guarded spend total (reset per test run section)."""
    return _session_spent_usd


def reset_session_spend() -> None:
    """Zero the session spend total (test isolation only)."""
    global _session_spent_usd
    _session_spent_usd = Decimal("0")


def install_session_guard(
    settings: Settings | None = None,
) -> Callable[[], None]:
    """Patch AsyncHTTPTransport for the whole session; return an uninstaller.

    Every request through any httpx2 client — guarded or not — must name
    an allowlisted host and, for POSTs, a registered, allowed, priced
    model, and must fit under the session spend ceiling from settings.
    Background-poll GETs are charged to the most recent POST's model.
    Mock transports are unaffected (they do not subclass AsyncHTTPTransport).
    """
    original = httpx2.AsyncHTTPTransport.handle_async_request

    def _active() -> Settings:
        return settings or get_settings()

    async def _checked(self: httpx2.AsyncHTTPTransport, request: httpx2.Request) -> httpx2.Response:
        global _session_spent_usd
        if isinstance(self, GuardedTransport):
            return await original(self, request)
        active = _active()
        if request.url.host not in _allowed_hosts(active):
            raise GuardViolation(f"host not allowlisted: {request.url.host}")
        model: str | None = None
        if request.method.upper() == "POST":
            try:
                body = json.loads(request.content.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise GuardViolation("guarded call must carry a JSON body") from exc
            candidate = body.get("model")
            if not isinstance(candidate, str):
                raise GuardViolation("guarded call must name a model")
            get_model(candidate)
            if not is_allowed(candidate, active.llm_dev_mode):
                raise GuardViolation(f"model needs LLM_DEV_MODE=1: {candidate}")
            if not _is_priced(candidate):
                raise GuardViolation(f"model has no dated price: {candidate}")
            ceiling = Decimal(str(active.llm_spend_ceiling_usd))
            if _session_spent_usd >= ceiling:
                raise GuardViolation(
                    f"session spend ceiling reached: {_session_spent_usd} >= {ceiling}"
                )
            model = candidate
            try:
                self._session_guard_model = model  # type: ignore[attr-defined]
            except AttributeError:
                pass
        else:
            stored = getattr(self, "_session_guard_model", None)
            model = stored if isinstance(stored, str) else None
        response = await original(self, request)
        if model is not None and response.status_code == 200:
            try:
                await response.aread()
            except (ValueError, UnicodeDecodeError, RuntimeError):
                pass
            else:
                _session_spent_usd += _usage_cost_usd(model, response.content)
        return response

    httpx2.AsyncHTTPTransport.handle_async_request = _checked  # type: ignore[method-assign]

    def uninstall() -> None:
        httpx2.AsyncHTTPTransport.handle_async_request = original  # type: ignore[method-assign]

    return uninstall
