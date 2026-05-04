"""Typed terminal outcomes for tool calls.

Every started `tool_call_started` event must converge on exactly one
terminal `tool_result` event. The outcome describes which terminus path
got there first (success, exception, per-tool timeout, run-level timeout,
worker abandonment, cancellation). Status values are the public strings
that flow through `tool_result.status` and `runtime_tool_invocations.status`.
"""

from __future__ import annotations

from enum import StrEnum


class ToolOutcome(StrEnum):
    """Terminal outcome for a tool call lifecycle.

    The string value is the public ``tool_result.status`` value clients see
    and the value persisted to ``runtime_tool_invocations.status``.
    """

    SUCCEEDED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"

    @property
    def is_terminal(self) -> bool:
        return True

    @property
    def is_success(self) -> bool:
        return self is ToolOutcome.SUCCEEDED


class ToolErrorCode(StrEnum):
    """Typed error classes a tool call can settle with.

    Distinct from ``RuntimeErrorCode`` because tool failures are scoped to
    a single ``call_id`` rather than an entire run.
    """

    TOOL_EXCEPTION = "tool_exception"
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_RUN_TIMEOUT = "tool_run_timeout"
    TOOL_RUN_ABANDONED = "tool_run_abandoned"
    TOOL_CANCELLED = "tool_cancelled"


# Statuses that indicate a tool call did not complete successfully.
TOOL_FAILURE_STATUSES = frozenset(
    {
        ToolOutcome.FAILED.value,
        ToolOutcome.TIMED_OUT.value,
        ToolOutcome.ABANDONED.value,
        ToolOutcome.CANCELLED.value,
    }
)
