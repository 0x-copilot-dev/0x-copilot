"""Reasoning depth → runtime budget mapping (single source of truth).

The composer ships a Fast / Balanced / Deep selector. This module is the
**only** place that knows how that selection translates into runtime
parameters (timeout, max_output_tokens, per-tool call budget). The
:class:`ModelConfigResolver` invokes :meth:`DepthBudgetTable.apply` exactly
once when materialising a :class:`ModelConfig`, so the worker and every
downstream consumer reads the post-mapped values straight off
``model_profile`` — there is no second application point.

Multipliers (not absolute numbers) so a deployment that bumps its baseline
timeout or budget keeps the same Fast/Balanced/Deep ratio without
touching this table.

The picked ratios are staff-engineering judgment (chat1.md does not pin
numbers): Fast = roughly half the patience for snappy iteration, Deep =
roughly 2× to accommodate longer chain-of-thought and tool sequences.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — type-only import
    from agent_runtime.execution.contracts import ModelConfig


class ReasoningDepth(StrEnum):
    """User-facing reasoning depth, mirrored from the composer's picker.

    The string values are also the wire contract — the api-types
    ``ReasoningDepth`` literal union must stay in lockstep.
    """

    FAST = "fast"
    BALANCED = "balanced"
    DEEP = "deep"


class _DepthMultipliers:
    """Per-axis multipliers keyed by :class:`ReasoningDepth`.

    Inner class so the table cannot be mutated from outside the module
    and the public surface stays tight (only :class:`DepthBudgetTable`).

    Why these specific numbers:
      * ``balanced`` is the baseline; multipliers are 1.0 so an absent
        depth and ``balanced`` produce identical ``ModelConfig`` values.
      * ``fast`` halves timeout and tool-call budget (snappy answers,
        fewer follow-ups) and trims max-output-tokens to ~0.6× so the
        model commits to a tighter answer.
      * ``deep`` doubles timeout and tool-call budget (give the model
        room to chain searches / refinements) and stretches
        max-output-tokens to 1.5× so it can write a longer answer.
    """

    # (timeout_multiplier, max_output_tokens_multiplier, tool_call_budget_multiplier)
    _TABLE: dict[ReasoningDepth, tuple[float, float, float]] = {
        ReasoningDepth.FAST: (0.5, 0.6, 0.5),
        ReasoningDepth.BALANCED: (1.0, 1.0, 1.0),
        ReasoningDepth.DEEP: (2.0, 1.5, 2.0),
    }

    @classmethod
    def for_depth(cls, depth: ReasoningDepth) -> tuple[float, float, float]:
        """Return ``(timeout, max_output_tokens, tool_call_budget)`` multipliers."""

        return cls._TABLE[depth]


class DepthBudgetTable:
    """Apply a :class:`ReasoningDepth` selection to a :class:`ModelConfig`.

    Single application point. Callers must NOT re-apply the mapping later
    (e.g. in the worker); they read the already-scaled values straight off
    ``model_profile.timeout_seconds`` / ``model_profile.max_output_tokens``
    / ``model_profile.tool_call_budget``.
    """

    # Minimum floor for tool_call_budget — Fast at a baseline of 2 would
    # round to 1 which collapses Deep Agents' multi-step loop entirely.
    _TOOL_CALL_BUDGET_FLOOR: int = 1
    # Hard ceiling matches the upper bound on ``ModelConfig.timeout_seconds``
    # (Pydantic ``le=600``). ``deep`` against a 400s baseline would otherwise
    # clip silently at the contract level; clamp here so the table owns
    # the boundary and the contract error is unreachable in practice.
    _TIMEOUT_SECONDS_CEILING: float = 600.0
    # Same idea for input/output token caps — Pydantic ``le=2_000_000``.
    _MAX_OUTPUT_TOKENS_CEILING: int = 2_000_000

    @classmethod
    def apply(
        cls,
        model_config: "ModelConfig",
        depth: ReasoningDepth | None,
    ) -> "ModelConfig":
        """Return a copy of ``model_config`` with depth-scaled budgets.

        ``depth=None`` is the no-op path: identical instance returned.
        ``depth=balanced`` is a no-op by construction (all multipliers
        are 1.0) but we still attach the field so downstream consumers
        can read which depth was selected.
        """

        if depth is None:
            return model_config

        timeout_mul, output_mul, tool_mul = _DepthMultipliers.for_depth(depth)

        scaled_timeout = min(
            model_config.timeout_seconds * timeout_mul,
            cls._TIMEOUT_SECONDS_CEILING,
        )
        # ``max_output_tokens`` is optional on the baseline ModelConfig.
        # When the baseline is None we leave it None — depth cannot
        # invent a number out of thin air; provider defaults apply.
        scaled_output: int | None = None
        if model_config.max_output_tokens is not None:
            scaled_output = max(
                1,
                min(
                    int(round(model_config.max_output_tokens * output_mul)),
                    cls._MAX_OUTPUT_TOKENS_CEILING,
                ),
            )
        scaled_tool_budget = max(
            cls._TOOL_CALL_BUDGET_FLOOR,
            int(round(model_config.tool_call_budget * tool_mul)),
        )

        return model_config.model_copy(
            update={
                "timeout_seconds": scaled_timeout,
                "max_output_tokens": scaled_output,
                "tool_call_budget": scaled_tool_budget,
                "reasoning_depth": depth,
            }
        )
