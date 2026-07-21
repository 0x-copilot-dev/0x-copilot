"""Unit tests for the integer micro-USD cost calculator (banker's rounding)."""

from __future__ import annotations

from datetime import datetime, timezone

from agent_runtime.persistence.records import ModelPricingRecord
from agent_runtime.pricing.calculator import CostCalculator


def _pricing(
    *,
    input_per_1m_micro_usd: int = 15_000_000,
    output_per_1m_micro_usd: int = 75_000_000,
    cached_input_per_1m_micro_usd: int | None = 1_500_000,
) -> ModelPricingRecord:
    return ModelPricingRecord(
        provider="anthropic",
        model_name="claude-opus-4-8",
        effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        input_per_1m_micro_usd=input_per_1m_micro_usd,
        output_per_1m_micro_usd=output_per_1m_micro_usd,
        cached_input_per_1m_micro_usd=cached_input_per_1m_micro_usd,
        pricing_version="litellm:1.93.0",
    )


class TestCostCalculator:
    def test_zero_tokens_returns_zero(self) -> None:
        cost = CostCalculator.compute(
            input_tokens=0,
            output_tokens=0,
            cached_input_tokens=0,
            pricing=_pricing(),
        )
        assert cost == 0

    def test_input_only_at_full_rate(self) -> None:
        # 1M input tokens @ $15.00 / 1M = 15_000_000 micro-USD.
        cost = CostCalculator.compute(
            input_tokens=1_000_000,
            output_tokens=0,
            cached_input_tokens=0,
            pricing=_pricing(),
        )
        assert cost == 15_000_000

    def test_output_only(self) -> None:
        # 1M output tokens @ $75.00 / 1M.
        cost = CostCalculator.compute(
            input_tokens=0,
            output_tokens=1_000_000,
            cached_input_tokens=0,
            pricing=_pricing(),
        )
        assert cost == 75_000_000

    def test_cached_input_billed_at_cached_rate(self) -> None:
        # 1M cached @ $1.50 / 1M.
        cost = CostCalculator.compute(
            input_tokens=1_000_000,
            output_tokens=0,
            cached_input_tokens=1_000_000,
            pricing=_pricing(),
        )
        assert cost == 1_500_000

    def test_cached_falls_back_to_input_when_rate_missing(self) -> None:
        # No cached rate -> charged at input rate.
        cost = CostCalculator.compute(
            input_tokens=1_000_000,
            output_tokens=0,
            cached_input_tokens=1_000_000,
            pricing=_pricing(cached_input_per_1m_micro_usd=None),
        )
        assert cost == 15_000_000

    def test_negative_tokens_return_zero(self) -> None:
        # Calculator never raises; bad input returns 0.
        cost = CostCalculator.compute(
            input_tokens=-1,
            output_tokens=100,
            cached_input_tokens=0,
            pricing=_pricing(),
        )
        assert cost == 0

    def test_round_half_to_even(self) -> None:
        # 1 token at $1.5 / 1M = 1.5 micro -> rounds to 2 (even).
        cost = CostCalculator.compute(
            input_tokens=1,
            output_tokens=0,
            cached_input_tokens=0,
            pricing=_pricing(
                input_per_1m_micro_usd=1_500_000,
                output_per_1m_micro_usd=0,
                cached_input_per_1m_micro_usd=None,
            ),
        )
        assert cost == 2  # banker's rounding: .5 rounds to nearest even
        # 3 tokens at $1.5 / 1M = 4.5 micro -> rounds to 4 (even, not 5).
        cost = CostCalculator.compute(
            input_tokens=3,
            output_tokens=0,
            cached_input_tokens=0,
            pricing=_pricing(
                input_per_1m_micro_usd=1_500_000,
                output_per_1m_micro_usd=0,
                cached_input_per_1m_micro_usd=None,
            ),
        )
        assert cost == 4

    def test_monotonic_in_input_tokens(self) -> None:
        previous = 0
        pricing = _pricing()
        for input_tokens in (0, 100, 10_000, 1_000_000, 5_000_000):
            cost = CostCalculator.compute(
                input_tokens=input_tokens,
                output_tokens=0,
                cached_input_tokens=0,
                pricing=pricing,
            )
            assert cost >= previous
            previous = cost
