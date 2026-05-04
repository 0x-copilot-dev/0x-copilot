"""B7 — pre-run conservative estimator."""

from __future__ import annotations

from datetime import datetime, timezone

from agent_runtime.budgets.estimator import BudgetEstimator
from agent_runtime.persistence.records import ModelPricingRecord


def _pricing() -> ModelPricingRecord:
    return ModelPricingRecord(
        provider="openai",
        model_name="gpt-5.4-mini",
        effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        input_per_1m_micro_usd=1_000_000,  # $1.00 per 1M input
        output_per_1m_micro_usd=2_000_000,  # $2.00 per 1M output
        pricing_version="2026-q1",
    )


class TestBudgetEstimator:
    def test_estimate_input_includes_safety_margin(self) -> None:
        # 4_000 chars / 4 chars-per-tok = 1_000 tokens, * 1.05 = 1_050.
        result = BudgetEstimator.estimate(
            prompt_chars=4_000,
            max_output_tokens=512,
            pricing=_pricing(),
        )
        assert result.input_tokens >= 1_000
        assert result.input_tokens >= int(1_000 * 1.05) - 1

    def test_estimate_output_uses_explicit_max_when_set(self) -> None:
        result = BudgetEstimator.estimate(
            prompt_chars=4_000,
            max_output_tokens=2_048,
            pricing=_pricing(),
        )
        assert result.output_tokens == 2_048

    def test_estimate_output_falls_back_when_max_unset(self) -> None:
        result = BudgetEstimator.estimate(
            prompt_chars=4_000,
            max_output_tokens=None,
            pricing=_pricing(),
        )
        assert result.output_tokens == 4_096

    def test_cost_is_none_when_pricing_missing(self) -> None:
        result = BudgetEstimator.estimate(
            prompt_chars=4_000,
            max_output_tokens=512,
            pricing=None,
        )
        assert result.cost_micro_usd is None

    def test_cost_uses_input_and_output_rates(self) -> None:
        # 1_050 input * $1/1M + 512 output * $2/1M
        # = 1_050 + 1_024 = 2_074 micro_usd (roughly).
        result = BudgetEstimator.estimate(
            prompt_chars=4_000,
            max_output_tokens=512,
            pricing=_pricing(),
        )
        assert result.cost_micro_usd is not None
        assert result.cost_micro_usd >= 1_500
        assert result.cost_micro_usd <= 3_000

    def test_zero_prompt_chars_still_returns_at_least_one_token(self) -> None:
        # Prevents divide-by-zero / log oddities when callers pass 0.
        result = BudgetEstimator.estimate(
            prompt_chars=0,
            max_output_tokens=128,
            pricing=_pricing(),
        )
        assert result.input_tokens >= 1
