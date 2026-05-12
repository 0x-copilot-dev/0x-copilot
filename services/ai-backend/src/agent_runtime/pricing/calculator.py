"""Integer-only cost computation in micro-USD using banker's rounding.

Cost is stored as ``BIGINT micro_usd`` (1 USD = 1_000_000 micro-USD) so usage
rows never drift from floating-point rounding. ``compute`` never raises; malformed
arguments (negative numbers) return 0 so callers receive a sentinel rather than an
exception to swallow.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

from agent_runtime.persistence.records import ModelPricingRecord


class CostCalculator:
    """Compute cost in micro-USD given token counts and a pricing row."""

    _PER_MILLION = Decimal(1_000_000)

    @classmethod
    def compute(
        cls,
        *,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int,
        pricing: ModelPricingRecord,
    ) -> int:
        """Return cost in micro-USD as an integer.

        ``cached_input_tokens`` is billed at
        ``cached_input_per_1m_micro_usd`` when that column is present;
        otherwise it falls back to the regular input price (matches what
        most providers do when they don't expose a separate cached rate).
        """

        if (
            input_tokens < 0
            or output_tokens < 0
            or cached_input_tokens < 0
            or pricing.input_per_1m_micro_usd < 0
            or pricing.output_per_1m_micro_usd < 0
        ):
            return 0

        cached_rate = (
            pricing.cached_input_per_1m_micro_usd
            if pricing.cached_input_per_1m_micro_usd is not None
            else pricing.input_per_1m_micro_usd
        )
        # Fresh input tokens (excluding the cached fraction) are billed at
        # the full input rate. Cached fraction at the cached rate.
        fresh_input_tokens = max(0, input_tokens - cached_input_tokens)

        total_micro = (
            cls._token_cost(fresh_input_tokens, pricing.input_per_1m_micro_usd)
            + cls._token_cost(output_tokens, pricing.output_per_1m_micro_usd)
            + cls._token_cost(cached_input_tokens, cached_rate)
        )
        return total_micro

    @classmethod
    def _token_cost(cls, tokens: int, per_million_micro_usd: int) -> int:
        if tokens == 0 or per_million_micro_usd == 0:
            return 0
        # Decimal arithmetic with banker's rounding at the integer boundary.
        cost = (Decimal(tokens) * Decimal(per_million_micro_usd)) / cls._PER_MILLION
        return int(cost.quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))
