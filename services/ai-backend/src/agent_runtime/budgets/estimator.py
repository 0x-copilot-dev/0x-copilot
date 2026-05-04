"""Pre-run cost / token estimate for budget preflight.

Conservative on purpose: we'd rather over-estimate (and get a false-Deny
that the user can resolve by raising their budget) than under-estimate
and silently bust a hard cap. Heuristics:

- Input tokens: prompt characters / 4 plus a 5% safety margin. We don't
  pull tiktoken into the base image — the estimator runs in the request
  hot path and the budget compare-and-swap absorbs ±10% drift.
- Output tokens: ``model_profile.max_output_tokens`` when set, else
  ``RuntimeSettings.default_max_input_tokens // 2`` as a coarse cap.
- Cost: ``BudgetEstimator.cost_micro_usd_for(tokens, pricing)`` reuses
  :class:`CostCalculator` from the pricing module so the unit price math
  is shared with the post-run charge path.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_runtime.persistence.records import ModelPricingRecord
from agent_runtime.pricing import CostCalculator


_INPUT_TOKEN_SAFETY_MULT = 1.05  # 5% margin to absorb tokenizer drift
_FALLBACK_OUTPUT_TOKENS = 4_096  # used when no max is configured


@dataclass(frozen=True)
class BudgetEstimate:
    """Conservative pre-run estimate."""

    input_tokens: int
    output_tokens: int
    cost_micro_usd: int | None  # None when pricing missing for the model


class BudgetEstimator:
    """Pre-run cost + token estimate for the preflight check."""

    @classmethod
    def estimate(
        cls,
        *,
        prompt_chars: int,
        max_output_tokens: int | None,
        pricing: ModelPricingRecord | None,
    ) -> BudgetEstimate:
        # Tokenizer-free estimate: 1 token ≈ 4 chars for English prose.
        # Real prompts are mixed-language and have JSON/code, so the
        # safety multiplier covers the common cases. Tokenizers vary
        # ±15% across providers; we eat that variance via the multiplier
        # plus the post-run charge being keyed on observed tokens, not
        # the estimate.
        input_tokens = max(1, int((prompt_chars / 4) * _INPUT_TOKEN_SAFETY_MULT))
        output_tokens = (
            max_output_tokens
            if max_output_tokens is not None and max_output_tokens > 0
            else _FALLBACK_OUTPUT_TOKENS
        )
        cost_micro_usd: int | None = None
        if pricing is not None:
            cost_micro_usd = CostCalculator.compute(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=0,
                pricing=pricing,
            )
        return BudgetEstimate(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_micro_usd=cost_micro_usd,
        )
