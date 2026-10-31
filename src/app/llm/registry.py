"""Model registry: the only place with model ids, dated prices, and limits.

Model choice per pipeline step reads from here; no model literal lives in
an agent. Token prices are Meta list prices in USD per 1M tokens; image
and search units carry their own per-unit dated prices. Unless the id
says contributor (dev replays on synthetic data only), Standard tier.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

DEFAULT_MODEL = "muse-spark-1.3"


class UnknownModelError(LookupError):
    """The model id is not in the registry."""


class UnpricedModelError(ValueError):
    """The model is registered but has no dated price to bill against."""


@dataclass(frozen=True)
class ModelSpec:
    """One servable model: id, dated USD prices, limits, billing unit.

    unit is "token" (per-1M-tokens prices below), "image" (per generated
    image), or "search" (per web_search call); unit_price_usd prices one
    unit with its own date. price_note records provenance caveats such
    as unconfirmed rates.
    """

    model_id: str
    input_per_1m_usd: Decimal | None
    cached_per_1m_usd: Decimal | None
    output_per_1m_usd: Decimal | None
    priced_on: str
    context_window: int | None
    dev_only: bool = False
    unit: str = "token"
    unit_price_usd: Decimal | None = None
    unit_priced_on: str = ""
    price_note: str = ""


MODELS: dict[str, ModelSpec] = {
    "muse-spark-1.3": ModelSpec(
        model_id="muse-spark-1.3",
        input_per_1m_usd=Decimal("1.25"),
        cached_per_1m_usd=Decimal("0.15"),
        output_per_1m_usd=Decimal("4.25"),
        priced_on="2026-10-31",
        context_window=1_048_576,
    ),
    "muse-wonder-1.0": ModelSpec(
        model_id="muse-wonder-1.0",
        input_per_1m_usd=Decimal("4.00"),
        cached_per_1m_usd=Decimal("0.20"),
        output_per_1m_usd=Decimal("20.00"),
        priced_on="2026-10-31",
        context_window=1_048_576,
    ),
    "muse-image-1.0": ModelSpec(
        model_id="muse-image-1.0",
        input_per_1m_usd=None,
        cached_per_1m_usd=None,
        output_per_1m_usd=None,
        priced_on="",
        context_window=None,
        unit="image",
        unit_price_usd=Decimal("0.01"),
        unit_priced_on="2026-10-31",
    ),
    "web_search": ModelSpec(
        model_id="web_search",
        input_per_1m_usd=None,
        cached_per_1m_usd=None,
        output_per_1m_usd=None,
        priced_on="",
        context_window=None,
        unit="search",
        unit_price_usd=Decimal("0.0025"),
        unit_priced_on="2026-10-31",
        price_note="$2.50 per 1,000 searches",
    ),
    "muse-spark-1.3-contributor": ModelSpec(
        model_id="muse-spark-1.3-contributor",
        input_per_1m_usd=Decimal("0.10"),
        cached_per_1m_usd=Decimal("0.002"),
        output_per_1m_usd=Decimal("0.20"),
        priced_on="2026-10-31",
        context_window=1_048_576,
        dev_only=True,
        price_note="cached rate unconfirmed",
    ),
}


def get_model(model_id: str) -> ModelSpec:
    """Return the spec for a registered model id or fail closed."""
    try:
        return MODELS[model_id]
    except KeyError:
        raise UnknownModelError(f"unknown model: {model_id}") from None


def is_allowed(model_id: str, dev_mode: bool) -> bool:
    """Contributor models run only with LLM_DEV_MODE=1 on synthetic data."""
    spec = get_model(model_id)
    return dev_mode or not spec.dev_only


def is_priced(model_id: str) -> bool:
    """True when the model has a dated price in its own billing unit."""
    spec = get_model(model_id)
    if spec.unit == "token":
        return (
            spec.input_per_1m_usd is not None
            and spec.cached_per_1m_usd is not None
            and spec.output_per_1m_usd is not None
        )
    return spec.unit_price_usd is not None


def compute_cost_usd(
    model_id: str, input_tokens: int, cached_tokens: int, output_tokens: int
) -> Decimal:
    """Cost in USD from the registry's dated prices; cached input is billed
    once at the cached rate, never double-counted as fresh input."""
    spec = get_model(model_id)
    prices = (spec.input_per_1m_usd, spec.cached_per_1m_usd, spec.output_per_1m_usd)
    if spec.unit != "token" or any(price is None for price in prices):
        raise UnpricedModelError(f"no dated token price for model: {model_id}")
    assert spec.input_per_1m_usd is not None
    assert spec.cached_per_1m_usd is not None
    assert spec.output_per_1m_usd is not None
    fresh_input = max(input_tokens - cached_tokens, 0)
    cost = (
        Decimal(fresh_input) * spec.input_per_1m_usd
        + Decimal(cached_tokens) * spec.cached_per_1m_usd
        + Decimal(output_tokens) * spec.output_per_1m_usd
    ) / Decimal(1_000_000)
    return cost.quantize(Decimal("0.000001"))


def unit_cost_usd(model_id: str, units: int) -> Decimal:
    """Cost in USD for a count of non-token units (images, searches)."""
    spec = get_model(model_id)
    if spec.unit == "token" or spec.unit_price_usd is None:
        raise UnpricedModelError(f"no dated unit price for model: {model_id}")
    return (Decimal(units) * spec.unit_price_usd).quantize(Decimal("0.000001"))
