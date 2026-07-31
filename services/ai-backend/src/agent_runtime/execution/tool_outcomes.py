"""Typed terminal outcomes for tool calls.

Every started `tool_call_started` event must converge on exactly one
terminal `tool_result` event. The outcome describes which terminus path
got there first (success, exception, per-tool timeout, run-level timeout,
worker abandonment, cancellation). Status values are the public strings
that flow through `tool_result.status` and `runtime_tool_invocations.status`.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, ClassVar

from agent_runtime.observability.redactor import ToolArgumentRedactor
from agent_runtime.persistence.records.common import ToolInvocationStatus


class ToolOutcome(StrEnum):
    """Terminal outcome for a tool call lifecycle.

    The string value is the public ``tool_result.status`` value clients see.

    It is NOT directly persistable: ``runtime_tool_invocations.status`` is
    constrained (migration 0001) to the six :class:`ToolInvocationStatus`
    values, which do not include ``timed_out``, ``abandoned`` or ``rejected``.
    Use :class:`ToolInvocationOutcome` to project an outcome onto the ledger —
    it folds those three onto ``failed`` and keeps the precise class in
    ``safe_error_code``, where nothing is lost.
    """

    SUCCEEDED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"
    # Rejected by the per-tool budget middleware before invocation;
    # no side effects, model receives a safe error.
    REJECTED = "rejected"

    @property
    def is_terminal(self) -> bool:
        """Always ``True`` — every ``ToolOutcome`` value is a terminal state."""
        return True

    @property
    def is_success(self) -> bool:
        """Return ``True`` only for the ``SUCCEEDED`` outcome."""
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
    # Per-tool budget rejection enforced at the gate. The model sees the
    # safe error string and can decide to continue without this tool.
    TOOL_BUDGET_EXCEEDED = "tool_budget_exceeded"


class ToolInvocationOutcome:
    """Project a terminal tool outcome onto the ledger's closing fields.

    One place decides what ``status`` / ``safe_error_code`` /
    ``safe_error_message`` a finished tool call is recorded with, so the
    natural ``tool_result`` seam and the run handler's failure/cancellation
    reconciliation cannot disagree about it.

    The ledger's ``status`` column accepts only the six
    :class:`ToolInvocationStatus` values, so ``timed_out`` / ``abandoned`` /
    ``rejected`` fold onto ``failed`` and survive intact as the error code.
    """

    _STATUS_BY_OUTCOME: ClassVar[dict[str, ToolInvocationStatus]] = {
        ToolOutcome.SUCCEEDED.value: ToolInvocationStatus.COMPLETED,
        ToolOutcome.FAILED.value: ToolInvocationStatus.FAILED,
        ToolOutcome.TIMED_OUT.value: ToolInvocationStatus.FAILED,
        ToolOutcome.ABANDONED.value: ToolInvocationStatus.FAILED,
        ToolOutcome.REJECTED.value: ToolInvocationStatus.FAILED,
        ToolOutcome.CANCELLED.value: ToolInvocationStatus.CANCELLED,
    }
    # A status string the map does not know is recorded as a FAILURE, not as a
    # success. An unrecognised terminal state is precisely the case where
    # claiming "completed" would be a lie, and the ledger exists to not lie.
    _UNKNOWN_STATUS: ClassVar[ToolInvocationStatus] = ToolInvocationStatus.FAILED
    _MAX_ERROR_MESSAGE_LENGTH: ClassVar[int] = 400

    class Keys:
        """Payload keys read off a ``tool_result`` event."""

        STATUS = "status"
        OUTPUT = "output"
        ERROR_CODE = "error_code"
        ERROR_MESSAGE = "error_message"

    @classmethod
    def status_for(cls, status: str | None) -> ToolInvocationStatus:
        """Return the persistable ledger status for a wire status string."""
        if status is None:
            return cls._UNKNOWN_STATUS
        return cls._STATUS_BY_OUTCOME.get(status, cls._UNKNOWN_STATUS)

    @classmethod
    def from_result_payload(cls, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Return the ``close_tool_invocation`` keyword arguments for a tool_result payload.

        Success closes with no error fields. Failure closes with an error code
        — the payload's own when the reconciler supplied one, else a typed
        fallback derived from the status — and a bounded message, so a failed
        invocation never lands with ``safe_error_code=None`` the way every
        invocation used to.
        """

        raw_status = payload.get(cls.Keys.STATUS)
        status_text = raw_status if isinstance(raw_status, str) else None
        status = cls.status_for(status_text)
        summary = ToolArgumentRedactor.redact(payload.get(cls.Keys.OUTPUT))
        if status is ToolInvocationStatus.COMPLETED:
            return {"status": status, "result_summary": summary}
        return {
            "status": status,
            "result_summary": summary,
            "safe_error_code": cls._error_code(payload, status_text),
            "safe_error_message": cls._error_message(payload, summary),
        }

    @classmethod
    def _error_code(cls, payload: Mapping[str, Any], status_text: str | None) -> str:
        """Prefer the payload's typed code; else derive one from the status."""
        code = payload.get(cls.Keys.ERROR_CODE)
        if isinstance(code, str) and code:
            return code
        if status_text == ToolOutcome.TIMED_OUT.value:
            return ToolErrorCode.TOOL_TIMEOUT.value
        if status_text == ToolOutcome.CANCELLED.value:
            return ToolErrorCode.TOOL_CANCELLED.value
        if status_text == ToolOutcome.ABANDONED.value:
            return ToolErrorCode.TOOL_RUN_ABANDONED.value
        if status_text == ToolOutcome.REJECTED.value:
            return ToolErrorCode.TOOL_BUDGET_EXCEEDED.value
        return ToolErrorCode.TOOL_EXCEPTION.value

    @classmethod
    def _error_message(
        cls, payload: Mapping[str, Any], summary: Mapping[str, Any]
    ) -> str:
        """Prefer the payload's message; else render the redacted output as evidence."""
        message = payload.get(cls.Keys.ERROR_MESSAGE)
        if isinstance(message, str) and message.strip():
            return message[: cls._MAX_ERROR_MESSAGE_LENGTH]
        if summary:
            return str(summary)[: cls._MAX_ERROR_MESSAGE_LENGTH]
        return _UNREPORTED_TOOL_FAILURE


# Used when a tool settles as failed but carries no message anywhere. Says so
# plainly rather than leaving the column null, which is indistinguishable from
# "never closed" — the exact ambiguity that made the old ledger unreadable.
_UNREPORTED_TOOL_FAILURE = "The tool failed without reporting a reason."


# Non-success terminal statuses used for filtering / metrics queries.
# ``REJECTED`` is intentionally excluded: it is a pre-invocation gate,
# not an in-flight failure, and has its own counter path.
TOOL_FAILURE_STATUSES = frozenset(
    {
        ToolOutcome.FAILED.value,
        ToolOutcome.TIMED_OUT.value,
        ToolOutcome.ABANDONED.value,
        ToolOutcome.CANCELLED.value,
    }
)
