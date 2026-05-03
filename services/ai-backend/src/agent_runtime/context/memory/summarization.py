"""Summarization and offloading helpers around Deep Agents context compression."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import TypeAlias

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.context.memory.constants import Messages, Values
from agent_runtime.context.memory.contracts import (
    ContextCompressionEvent,
    ContextCompressionStrategy,
    ContextSummary,
    ManagedContextPayload,
    TokenBudgetPolicy,
)
from agent_runtime.context.memory.token_budget import TokenBudgetEvaluator

SummaryFactory: TypeAlias = Callable[[], ContextSummary | Mapping[str, object] | str]
OffloadWriter: TypeAlias = Callable[[str], str]


class SummarizationResult(RuntimeContract):
    """Structured summary result plus safe compression metrics."""

    summary: ContextSummary
    event: ContextCompressionEvent
    fallback_used: bool = False


class ContextSummarizationManager:
    """Wrap SDK summarization with deterministic fallback behavior."""

    FALLBACK_DECISION = (
        "Previous detailed context was compressed after summarization failed."
    )
    FALLBACK_ARTIFACT = "Compressed conversation context"
    FALLBACK_NEXT_STEP = (
        "Continue from the preserved objective and recent user request."
    )

    @classmethod
    def summarize_or_fallback(
        cls,
        *,
        objective: str,
        decisions: tuple[str, ...] = (),
        artifacts: tuple[str, ...] = (),
        next_steps: tuple[str, ...] = (),
        summarizer: SummaryFactory | None = None,
        trace_id: str,
        before_tokens: int,
    ) -> SummarizationResult:
        """Return a valid continuity summary even when SDK summarization fails."""

        try:
            if summarizer is None:
                raise ValueError(Messages.Errors.INVALID_CONTEXT_SUMMARY)
            summary = cls._coerce_summary(summarizer())
            fallback_used = False
        except Exception:
            logging.getLogger(__name__).warning(
                "Context summarization failed, using fallback", exc_info=True
            )
            summary = cls._fallback_summary(
                objective=objective,
                decisions=decisions,
                artifacts=artifacts,
                next_steps=next_steps,
            )
            fallback_used = True

        after_tokens = TokenBudgetEvaluator.estimate_tokens(summary.model_dump_json())
        event = ContextCompressionEvent(
            before_tokens=before_tokens,
            after_tokens=min(after_tokens, before_tokens),
            strategy=(
                ContextCompressionStrategy.FALLBACK_SUMMARY
                if fallback_used
                else ContextCompressionStrategy.SUMMARIZE
            ),
            trace_id=trace_id,
            metadata={"fallback_used": fallback_used},
        )
        return SummarizationResult(
            summary=summary,
            event=event,
            fallback_used=fallback_used,
        )

    @classmethod
    def _coerce_summary(
        cls, value: ContextSummary | Mapping[str, object] | str
    ) -> ContextSummary:
        if isinstance(value, ContextSummary):
            return value
        if isinstance(value, Mapping):
            return ContextSummary.model_validate(value)
        return ContextSummary(
            objective=value,
            decisions=(),
            artifacts=(),
            next_steps=(),
        )

    @classmethod
    def _fallback_summary(
        cls,
        *,
        objective: str,
        decisions: tuple[str, ...],
        artifacts: tuple[str, ...],
        next_steps: tuple[str, ...],
    ) -> ContextSummary:
        return ContextSummary(
            objective=objective,
            decisions=decisions or (cls.FALLBACK_DECISION,),
            artifacts=artifacts or (cls.FALLBACK_ARTIFACT,),
            next_steps=next_steps or (cls.FALLBACK_NEXT_STEP,),
        )


class ContextPayloadManager:
    """Keep connector and tool output out of the prompt when it is too large."""

    PREVIEW_LINE_LIMIT = 10

    @classmethod
    def prepare_tool_output(
        cls,
        *,
        content: str,
        policy: TokenBudgetPolicy,
        trace_id: str,
        offload_writer: OffloadWriter | None = None,
        summarizer: SummaryFactory | None = None,
    ) -> ManagedContextPayload:
        """Return inline, offloaded, or summarized payload for model context."""

        before_tokens = TokenBudgetEvaluator.estimate_tokens(content)
        budget = TokenBudgetEvaluator.snapshot(
            policy=policy,
            current_tokens=before_tokens,
        )
        if before_tokens <= budget.recent_context_tokens:
            return cls._inline_payload(
                content=content,
                trace_id=trace_id,
                before_tokens=before_tokens,
            )

        if offload_writer is not None:
            reference = offload_writer(content)
            preview = cls._preview(content)
            event = ContextCompressionEvent(
                before_tokens=before_tokens,
                after_tokens=TokenBudgetEvaluator.estimate_tokens(preview),
                strategy=ContextCompressionStrategy.OFFLOAD,
                files_written=(reference,),
                trace_id=trace_id,
                metadata={"mode": Values.CompressionStrategy.OFFLOAD},
            )
            return ManagedContextPayload(
                strategy=ContextCompressionStrategy.OFFLOAD,
                reference=reference,
                preview=preview,
                event=event,
            )

        summary_result = ContextSummarizationManager.summarize_or_fallback(
            objective="Summarize oversized tool output for safe model context.",
            artifacts=("Oversized tool output",),
            next_steps=("Use the summarized output and retain source references.",),
            summarizer=summarizer,
            trace_id=trace_id,
            before_tokens=before_tokens,
        )
        return ManagedContextPayload(
            strategy=summary_result.event.strategy,
            content=summary_result.summary.model_dump_json(),
            event=summary_result.event,
        )

    @classmethod
    def _inline_payload(
        cls,
        *,
        content: str,
        trace_id: str,
        before_tokens: int,
    ) -> ManagedContextPayload:
        event = ContextCompressionEvent(
            before_tokens=before_tokens,
            after_tokens=before_tokens,
            strategy=ContextCompressionStrategy.INLINE,
            trace_id=trace_id,
            metadata={"mode": Values.CompressionStrategy.INLINE},
        )
        return ManagedContextPayload(
            strategy=ContextCompressionStrategy.INLINE,
            content=content,
            event=event,
        )

    @classmethod
    def _preview(cls, content: str) -> str:
        return "\n".join(content.splitlines()[: cls.PREVIEW_LINE_LIMIT])
