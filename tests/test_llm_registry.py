"""Model registry: single source of models, dated prices, and limits."""

from decimal import Decimal

import pytest

from app.llm.registry import (
    UnknownModelError,
    UnpricedModelError,
    compute_cost_usd,
    get_model,
    is_allowed,
    is_priced,
    unit_cost_usd,
)


def test_default_model_is_registered() -> None:
    assert get_model("muse-spark-1.3").model_id == "muse-spark-1.3"


def test_unknown_model_rejected() -> None:
    with pytest.raises(UnknownModelError, match="gpt-"):
        get_model("gpt-4o")


def test_cost_math_uses_dated_prices() -> None:
    # Spark standard: 1.25 / 4.25 per 1M; cached input billed, not double-counted.
    cost = compute_cost_usd(
        "muse-spark-1.3", input_tokens=1_000_000, cached_tokens=0, output_tokens=1_000_000
    )
    assert cost == Decimal("5.50")
    cost = compute_cost_usd(
        "muse-spark-1.3", input_tokens=2_000_000, cached_tokens=1_000_000, output_tokens=0
    )
    assert cost == Decimal("1.40")


def test_unpriced_model_cannot_be_costed() -> None:
    with pytest.raises(UnpricedModelError, match="muse-image-1.0"):
        compute_cost_usd("muse-image-1.0", 10, 0, 10)


def test_image_model_is_priced_per_image() -> None:
    assert is_priced("muse-image-1.0")
    assert unit_cost_usd("muse-image-1.0", 3) == Decimal("0.03")
    assert get_model("muse-image-1.0").unit == "image"


def test_web_search_is_priced_per_thousand_searches() -> None:
    assert is_priced("web_search")
    assert unit_cost_usd("web_search", 1000) == Decimal("2.50")
    assert get_model("web_search").unit == "search"


def test_token_models_reject_unit_costing() -> None:
    with pytest.raises(UnpricedModelError, match="muse-spark-1.3"):
        unit_cost_usd("muse-spark-1.3", 5)


def test_contributor_cached_price_is_marked_unconfirmed() -> None:
    assert "unconfirmed" in get_model("muse-spark-1.3-contributor").price_note.lower()


def test_contributor_gated_behind_dev_mode() -> None:
    assert is_allowed("muse-spark-1.3-contributor", dev_mode=True)
    assert not is_allowed("muse-spark-1.3-contributor", dev_mode=False)
    assert is_allowed("muse-spark-1.3", dev_mode=False)
