"""Single chokepoint for safely ending a run.

Every termination path flows through :meth:`RunTerminationCoordinator.terminate`,
which drains the :class:`LifecycleLedger` (synthesising a ``*_COMPLETED`` event
for every open subagent/tool/model call) and then emits the run's own terminal
event. Reconciliation is best-effort: a failure on a single synthesised event is
logged and skipped so one stuck entry cannot block its siblings or the run-level
terminal event.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.observability.lifecycle_ledger import (
    LifecycleKind,
    OpenLifecycleEntry,
)
from runtime_api.schemas import (
    AgentRunStatus,
    RunRecord,
    RuntimeApiEventType,
)


_LOGGER = logging.getLogger("agent_runtime.api.run_termination")


class TerminationReason(StrEnum):
    """Reason a run reached a terminal state, carried in the run-level event payload."""

    NORMAL_COMPLETION = "normal_completion"
    TOOL_FATAL_ERROR = "tool_fatal_error"
    EXECUTION_ERROR = "execution_error"
    CANCELLED = "cancelled"
    APPROVAL_TIMEOUT = "approval_timeout"
    BUDGET_EXCEEDED = "budget_exceeded"
    RUN_TIMEOUT = "run_timeout"


# Maps the lifecycle kind to the synthesized terminal event type used
# during reconciliation. One row per lifecycle pair — adding a new pair
# means adding a row here, nowhere else.
_TERMINAL_EVENT_TYPES: dict[LifecycleKind, RuntimeApiEventType] = {
    LifecycleKind.SUBAGENT: RuntimeApiEventType.SUBAGENT_COMPLETED,
    LifecycleKind.TOOL_CALL: RuntimeApiEventType.TOOL_CALL_COMPLETED,
    LifecycleKind.MODEL_CALL: RuntimeApiEventType.MODEL_CALL_COMPLETED,
}


# Maps the run's terminal AgentRunStatus to its run-level event type.
_RUN_EVENT_TYPES: dict[AgentRunStatus, RuntimeApiEventType] = {
    AgentRunStatus.COMPLETED: RuntimeApiEventType.RUN_COMPLETED,
    AgentRunStatus.FAILED: RuntimeApiEventType.RUN_FAILED,
    AgentRunStatus.CANCELLED: RuntimeApiEventType.RUN_CANCELLED,
    AgentRunStatus.TIMED_OUT: RuntimeApiEventType.RUN_FAILED,
}


class RunTerminationCoordinator:
    """Coordinator that closes a run cleanly by draining the lifecycle ledger before the terminal event."""

    def __init__(self, *, event_producer: RuntimeEventProducer) -> None:
        self._event_producer = event_producer

    async def terminate(
        self,
        *,
        run: RunRecord,
        terminal_status: AgentRunStatus,
        reason: TerminationReason,
        summary: str | None = None,
        cause: BaseException | None = None,
        extra_payload: Mapping[str, Any] | None = None,
        extra_metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Reconcile open lifecycles, then emit the run's terminal event.

        Idempotent: a second call after the ledger is empty is a no-op for
        reconciliation. The caller is still responsible for not emitting
        ``RUN_*`` events out-of-band — this method is the only way runs
        should reach a terminal state.
        """

        await self._reconcile_open_lifecycles(
            run=run, terminal_status=terminal_status, reason=reason
        )
        await self._emit_run_terminal(
            run=run,
            terminal_status=terminal_status,
            reason=reason,
            summary=summary,
            cause=cause,
            extra_payload=extra_payload,
            extra_metadata=extra_metadata,
        )

    async def _reconcile_open_lifecycles(
        self,
        *,
        run: RunRecord,
        terminal_status: AgentRunStatus,
        reason: TerminationReason,
    ) -> None:
        """Emit a synthesised terminal event for every still-open lifecycle entry.

        A non-zero open count on the green path indicates a producer bug (a
        ``*_started`` event with no matching ``*_completed``). The log entry
        surfaces it for debugging without blocking the run from terminating.
        """
        ledger = self._event_producer.lifecycle_ledger
        open_entries = await ledger.open_entries()
        if not open_entries:
            return
        _LOGGER.info(
            "run_termination.reconciling_open_lifecycles",
            extra={
                "metadata": {
                    "run_id": run.run_id,
                    "open_count": len(open_entries),
                    "kinds": [e.kind.value for e in open_entries],
                    "terminal_status": terminal_status.value,
                    "reason": reason.value,
                }
            },
        )
        for entry in open_entries:
            try:
                await self._emit_synthesized_terminal(
                    run=run,
                    entry=entry,
                    terminal_status=terminal_status,
                    reason=reason,
                )
            except Exception:  # noqa: BLE001 — best-effort reconciliation
                _LOGGER.warning(
                    "run_termination.synthesized_event_failed",
                    extra={
                        "metadata": {
                            "run_id": run.run_id,
                            "kind": entry.kind.value,
                            "entity_id": entry.entity_id,
                        }
                    },
                    exc_info=True,
                )

    async def _emit_synthesized_terminal(
        self,
        *,
        run: RunRecord,
        entry: OpenLifecycleEntry,
        terminal_status: AgentRunStatus,
        reason: TerminationReason,
    ) -> None:
        """Build and emit the matching ``*_COMPLETED`` event for a leaked lifecycle entry.

        Identifying fields from the original payload snapshot are carried forward;
        ``status`` is overwritten with the synthesised value so consumers can tell
        apart natural completions from forced-close ones via the ``synthesized`` flag.
        """

        event_type = _TERMINAL_EVENT_TYPES[entry.kind]
        snapshot = dict(entry.payload_snapshot)
        payload: dict[str, Any] = {
            # Preserve all identifying fields (tool_name, subagent_name, etc.)
            # but discard the original ``status`` — we overwrite it below.
            **{k: v for k, v in snapshot.items() if k not in ("status",)},
            "status": _SYNTHESIZED_STATUS_FOR_TERMINAL[terminal_status],
            "reason": reason.value,
            "synthesized": True,
        }
        # Guarantee the entity id key is present even if the snapshot was sparse.
        id_field = _LIFECYCLE_ID_FIELD[entry.kind]
        payload.setdefault(id_field, entry.entity_id)
        await self._event_producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=event_type,
            payload=payload,
            parent_task_id=entry.parent_task_id,
            subagent_id=entry.subagent_id,
            status=payload["status"],
        )

    async def _emit_run_terminal(
        self,
        *,
        run: RunRecord,
        terminal_status: AgentRunStatus,
        reason: TerminationReason,
        summary: str | None,
        cause: BaseException | None,
        extra_payload: Mapping[str, Any] | None,
        extra_metadata: Mapping[str, Any] | None,
    ) -> None:
        """Emit the run-level terminal event (``RUN_COMPLETED``, ``RUN_FAILED``, or ``RUN_CANCELLED``).

        Errors here are logged but not re-raised: the run row is already in a terminal
        state; a missing event is a gap in the SSE stream, not a data-integrity failure.
        """
        event_type = _RUN_EVENT_TYPES[terminal_status]
        payload: dict[str, Any] = {
            "status": event_type.value,
            "reason": reason.value,
        }
        # Include the exception class name so the frontend/observability layer can
        # categorise failures without receiving internal stack trace detail.
        if cause is not None:
            payload["error_class"] = type(cause).__name__
        if extra_payload:
            payload.update(extra_payload)
        try:
            await self._event_producer.append_api_event(
                run=run,
                source=StreamEventSource.SYSTEM,
                event_type=event_type,
                payload=payload,
                metadata=dict(extra_metadata) if extra_metadata else None,
                summary=summary,
            )
        except Exception:  # noqa: BLE001 — last resort
            # If even the terminal event fails to land, log loudly. The
            # run row should already be in a terminal state; the missing
            # event will be visible as a gap in the SSE stream.
            _LOGGER.error(
                "run_termination.terminal_event_failed",
                extra={
                    "metadata": {
                        "run_id": run.run_id,
                        "terminal_status": terminal_status.value,
                        "reason": reason.value,
                    }
                },
                exc_info=True,
            )


# Status mapping for synthesized lifecycle terminal events. We don't
# pretend a leaked subagent "completed" — pick the status that matches
# how the run ended so the FE / audit reflect reality.
_SYNTHESIZED_STATUS_FOR_TERMINAL: dict[AgentRunStatus, str] = {
    AgentRunStatus.COMPLETED: "completed",  # green-path drain (defense-in-depth)
    AgentRunStatus.FAILED: "failed",
    AgentRunStatus.CANCELLED: "cancelled",
    AgentRunStatus.TIMED_OUT: "timed_out",
}


# Per-lifecycle entity-id payload field, mirroring LifecycleEventInspector.
_LIFECYCLE_ID_FIELD: dict[LifecycleKind, str] = {
    LifecycleKind.SUBAGENT: "task_id",
    LifecycleKind.TOOL_CALL: "call_id",
    LifecycleKind.MODEL_CALL: "message_id",
}


__all__ = (
    "RunTerminationCoordinator",
    "TerminationReason",
)
