"""Token budget metrics and threshold decisions for context compression."""

from __future__ import annotations

from pydantic import Field

from agent_runtime.agent.contracts import RuntimeContract
from agent_runtime.memory.contracts import TokenBudgetPolicy


class TokenBudgetSnapshot(RuntimeContract):
    """Derived token budget metrics for a single runtime decision point."""

    current_tokens: int = Field(ge=0)
    max_input_tokens: int = Field(gt=0)
    summary_threshold_tokens: int = Field(gt=0)
    recent_context_tokens: int = Field(gt=0)
    should_summarize: bool
    remaining_tokens: int = Field(ge=0)


class TokenBudgetEvaluator:
    """Compute deterministic budget metadata without calling a tokenizer service."""

    CHARS_PER_TOKEN_ESTIMATE = 4

    @classmethod
    def snapshot(
        cls,
        *,
        policy: TokenBudgetPolicy,
        current_tokens: int,
    ) -> TokenBudgetSnapshot:
        """Return threshold metrics for the current context size."""

        threshold_tokens = int(policy.max_input_tokens * policy.summary_threshold_ratio)
        recent_context_tokens = int(policy.max_input_tokens * policy.recent_context_ratio)
        remaining_tokens = max(policy.max_input_tokens - current_tokens, 0)
        return TokenBudgetSnapshot(
            current_tokens=current_tokens,
            max_input_tokens=policy.max_input_tokens,
            summary_threshold_tokens=max(threshold_tokens, 1),
            recent_context_tokens=max(recent_context_tokens, 1),
            should_summarize=current_tokens >= threshold_tokens,
            remaining_tokens=remaining_tokens,
        )

    @classmethod
    def estimate_tokens(cls, text: str) -> int:
        """Estimate token count for deterministic tests and policy thresholds."""

        if not text:
            return 0
        return max((len(text) + cls.CHARS_PER_TOKEN_ESTIMATE - 1) // cls.CHARS_PER_TOKEN_ESTIMATE, 1)
