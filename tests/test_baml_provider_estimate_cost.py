"""Behavior 5b: BamlLLMProvider prices token usage with Decimals."""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given, strategies as st

from activegraph.llm import LLMProvider
from activegraph.llm.baml_provider import BamlLLMProvider


def test_estimate_cost_returns_exact_decimal_from_pricing_table() -> None:
    provider = BamlLLMProvider(vendor="anthropic")

    cost = provider.estimate_cost(
        input_tokens=1_000,
        output_tokens=500,
        model="claude-sonnet-4-5",
    )

    assert isinstance(cost, Decimal)
    assert cost == Decimal("0.0105")


def test_estimate_cost_uses_longest_matching_model_prefix() -> None:
    provider = BamlLLMProvider(
        vendor="anthropic",
        pricing={
            "claude": {"input": "1", "output": "2"},
            "claude-sonnet": {"input": "3", "output": "4"},
        },
    )

    cost = provider.estimate_cost(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        model="claude-sonnet-4-5",
    )

    assert cost == Decimal("7")


def test_estimate_cost_unknown_model_uses_documented_vendor_fallback() -> None:
    provider = BamlLLMProvider(vendor="anthropic")

    cost = provider.estimate_cost(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        model="totally-unknown-model-xyz",
    )

    assert cost == Decimal("18")


def test_estimate_cost_zero_usage_is_zero() -> None:
    provider = BamlLLMProvider(vendor="openai")

    assert provider.estimate_cost(
        input_tokens=0,
        output_tokens=0,
        model="gpt-4o-mini",
    ) == Decimal("0")


def test_baml_provider_has_runtime_checkable_protocol_shape() -> None:
    assert isinstance(BamlLLMProvider(vendor="anthropic"), LLMProvider)


@given(
    input_tokens=st.integers(min_value=0, max_value=1_000_000),
    output_tokens=st.integers(min_value=0, max_value=1_000_000),
    input_delta=st.integers(min_value=0, max_value=10_000),
    output_delta=st.integers(min_value=0, max_value=10_000),
)
def test_estimate_cost_property_non_negative_and_monotonic(
    input_tokens: int,
    output_tokens: int,
    input_delta: int,
    output_delta: int,
) -> None:
    provider = BamlLLMProvider(vendor="anthropic")
    base = provider.estimate_cost(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model="claude-sonnet-4-5",
    )
    increased = provider.estimate_cost(
        input_tokens=input_tokens + input_delta,
        output_tokens=output_tokens + output_delta,
        model="claude-sonnet-4-5",
    )

    assert base >= Decimal("0")
    assert increased >= base
