"""Pre-run cost / token estimate for budget preflight.

Conservative on purpose: we'd rather over-estimate (and get a false-Deny
that the user can resolve by raising their budget) than under-estimate
and silently bust a hard cap. Heuristics:

- Input tokens: supplied by the caller already counted (the worker uses
  ``litellm.token_counter`` over the real first-call messages, falling back
  to a char/4 heuristic). The estimator adds a 5% safety margin on top to
  absorb cross-tokenizer drift; the budget compare-and-swap absorbs the
  residual. The estimator itself takes **no** litellm dependency — counting
  lives in the worker behind ``TokenCounterPort`` so this stays a pure
  cost-math boundary.
- Output tokens: ``model_profile.max_output_tokens`` when set, else a coarse
  4096-token fallback cap.
- Cost: reuses :class:`CostCalculator` from the pricing module so the unit
  price math is shared with the post-run charge path.
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
        input_tokens: int,
        max_output_tokens: int | None,
        pricing: ModelPricingRecord | None,
    ) -> BudgetEstimate:
        # ``input_tokens`` arrives already counted (litellm.token_counter over
        # the real first-call messages, or a char/4 fallback). We add the 5%
        # safety margin here: tokenizers vary across providers and the offline
        # tiktoken approximation used for non-OpenAI models drifts, so the
        # multiplier keeps the estimate biased conservative. The post-run charge
        # is keyed on observed tokens, not this estimate.
        input_tokens = max(1, int(input_tokens * _INPUT_TOKEN_SAFETY_MULT))
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
