"""Model registry: the only place with model ids, dated prices, and limits.

Model choice per pipeline step reads from here; no model literal lives in
an agent. Token prices are Meta list prices in USD per 1M tokens; image
and search units carry their own per-unit dated prices. Unless the id
says contributor (dev replays on synthetic data only), Standard tier.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.errors import ConfigurationError

DEFAULT_MODEL = "muse-spark-1.3"

EFFORTS = ("low", "medium", "high")

DEFAULT_EFFORT = "medium"
DEFAULT_MAX_OUTPUT_TOKENS = 4096


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
    provider: str = "meta"


TEST_PROVIDER_NOTE = (
    "tests and local development only (MODEL_PROVIDER=anthropic); cache writes are billed"
    " at 1.25x input by the provider and are counted here as plain input"
)

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
    "muse-voice-transcribe-1.0": ModelSpec(
        model_id="muse-voice-transcribe-1.0",
        input_per_1m_usd=None,
        cached_per_1m_usd=None,
        output_per_1m_usd=None,
        priced_on="",
        context_window=None,
        unit="audio_hour",
        unit_price_usd=Decimal("0.18"),
        unit_priced_on="2026-10-31",
        price_note="$0.18 per audio hour",
    ),
    "claude-sonnet-5-5": ModelSpec(
        model_id="claude-sonnet-5-5",
        input_per_1m_usd=Decimal("2.00"),
        cached_per_1m_usd=Decimal("0.20"),
        output_per_1m_usd=Decimal("10.00"),
        priced_on="2026-11-01",
        context_window=1_000_000,
        provider="anthropic",
        price_note=TEST_PROVIDER_NOTE,
    ),
    "claude-opus-5-5": ModelSpec(
        model_id="claude-opus-5-5",
        input_per_1m_usd=Decimal("4.00"),
        cached_per_1m_usd=Decimal("0.20"),
        output_per_1m_usd=Decimal("20.00"),
        priced_on="2026-11-01",
        context_window=1_000_000,
        provider="anthropic",
        price_note=TEST_PROVIDER_NOTE,
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


# Tier model -> the model that serves it on a test provider. A tier absent
# from the map (image, search, voice, contributor) has no equivalent there.
PROVIDER_TIERS: dict[str, dict[str, str]] = {
    "anthropic": {
        "muse-spark-1.3": "claude-sonnet-5-5",
        "muse-wonder-1.0": "claude-opus-5-5",
    },
}


def serving_model(model_id: str, provider: str) -> str:
    """The model id that really serves a registry model on this provider.

    The product runs on Meta models, so provider "meta" returns the id
    unchanged (and refuses another provider's id). A test provider maps
    the two text tiers; every other tier fails closed with a plain message.
    """
    spec = get_model(model_id)
    if provider == "meta":
        if spec.provider != "meta":
            raise ConfigurationError(f"model {model_id} is not served by provider meta")
        return model_id
    if spec.provider == provider:
        return model_id
    mapped = PROVIDER_TIERS.get(provider, {}).get(model_id)
    if mapped is None:
        raise ConfigurationError(f"model {model_id} has no equivalent on provider {provider}")
    return mapped


@dataclass(frozen=True)
class StepSpec:
    """One pipeline step: model plus required reasoning effort and token limit.

    Both fields are required (no defaults): a step without them fails
    validate_step_registry() at startup instead of silently running
    with an arbitrary limit.
    """

    model: str
    effort: str
    max_output_tokens: int


STEPS: dict[str, StepSpec] = {
    "librarian": StepSpec(model="muse-spark-1.3", effort="medium", max_output_tokens=2000),
    "researcher": StepSpec(model="muse-spark-1.3", effort="low", max_output_tokens=4000),
    "web_search": StepSpec(model="muse-spark-1.3", effort="low", max_output_tokens=1500),
    "outline": StepSpec(model="muse-wonder-1.0", effort="high", max_output_tokens=16000),
    "architect": StepSpec(model="muse-spark-1.3", effort="high", max_output_tokens=20000),
    "block_teach": StepSpec(model="muse-spark-1.3", effort="medium", max_output_tokens=12000),
    "block_example": StepSpec(model="muse-spark-1.3", effort="medium", max_output_tokens=12000),
    "block_exercise": StepSpec(model="muse-spark-1.3", effort="high", max_output_tokens=12000),
    "reader": StepSpec(model="muse-spark-1.3", effort="high", max_output_tokens=30000),
    "saboteur": StepSpec(model="muse-spark-1.3", effort="medium", max_output_tokens=4000),
    "translator": StepSpec(model="muse-spark-1.3", effort="low", max_output_tokens=8000),
    "tutor": StepSpec(model="muse-spark-1.3", effort="low", max_output_tokens=2000),
}


def validate_step_registry() -> None:
    """Fail startup when a step entry is incomplete or names a bad model."""
    for step, spec in STEPS.items():
        if spec.effort not in EFFORTS:
            raise ConfigurationError(f"step {step}: effort must be one of {EFFORTS}")
        if spec.max_output_tokens <= 0:
            raise ConfigurationError(f"step {step}: max_output_tokens must be positive")
        get_model(spec.model)


def resolve_step(
    agent: str,
    model: str | None,
    effort: str | None,
    max_output_tokens: int | None,
) -> tuple[str, str, int]:
    """Fill unset call options from the step registry; explicit values win."""
    step = STEPS.get(agent)
    resolved_model = model or (step.model if step else DEFAULT_MODEL)
    resolved_effort = effort or (step.effort if step else DEFAULT_EFFORT)
    resolved_tokens = (
        max_output_tokens
        if max_output_tokens is not None
        else (step.max_output_tokens if step else DEFAULT_MAX_OUTPUT_TOKENS)
    )
    if resolved_effort not in EFFORTS:
        raise ConfigurationError(f"step {agent}: effort must be one of {EFFORTS}")
    if resolved_tokens <= 0:
        raise ConfigurationError(f"step {agent}: max_output_tokens must be positive")
    get_model(resolved_model)
    return resolved_model, resolved_effort, resolved_tokens


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


def compute_cost_usd(
    model_id: str,
    input_tokens: int,
    cached_tokens: int,
    output_tokens: int,
    reasoning_tokens: int = 0,
) -> Decimal:
    """Cost in USD from the registry's dated prices; cached input is billed
    once at the cached rate, never double-counted as fresh input.
    Reasoning tokens are billed at the output rate."""
    spec = get_model(model_id)
    prices = (spec.input_per_1m_usd, spec.cached_per_1m_usd, spec.output_per_1m_usd)
    if spec.unit != "token" or any(price is None for price in prices):
        raise UnpricedModelError(f"no dated token price for model: {model_id}")
    assert spec.input_per_1m_usd is not None
    assert spec.cached_per_1m_usd is not None
    assert spec.output_per_1m_usd is not None
    fresh_input = max(input_tokens - cached_tokens, 0)
    billed_output = max(output_tokens, 0) + max(reasoning_tokens, 0)
    cost = (
        Decimal(fresh_input) * spec.input_per_1m_usd
        + Decimal(cached_tokens) * spec.cached_per_1m_usd
        + Decimal(billed_output) * spec.output_per_1m_usd
    ) / Decimal(1_000_000)
    return cost.quantize(Decimal("0.000001"))


def unit_cost_usd(model_id: str, units: int) -> Decimal:
    """Cost of `units` of a per-unit model (a web search, an image) from its dated price."""
    spec = get_model(model_id)
    if spec.unit == "token" or spec.unit_price_usd is None:
        raise UnpricedModelError(f"no dated unit price for model: {model_id}")
    return (Decimal(max(units, 0)) * spec.unit_price_usd).quantize(Decimal("0.000001"))
