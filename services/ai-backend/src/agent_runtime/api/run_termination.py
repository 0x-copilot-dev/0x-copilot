"""Single chokepoint for safely ending a run — and the ledger's seal authority.

Every termination path flows through :meth:`RunTerminationCoordinator.terminate`,
which drains the :class:`LifecycleLedger` (synthesising a ``*_COMPLETED`` event
for every open subagent/tool/model call), then drains every registered
:class:`RunProjectionDrainPort`, and only then emits the run's own terminal
event. That terminal event seals the run's causal prefix: see
:mod:`agent_runtime.api.ledger_seal` for what the seal promises and why the
promise is only keepable from here.

Draining is best-effort: a failure on a single synthesised event or projection
source is logged and skipped so one stuck entry cannot block its siblings or the
run-level terminal event.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

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
    RuntimeEventEnvelope,
)


_LOGGER = logging.getLogger("agent_runtime.api.run_termination")


class TerminalRunObserverPort(Protocol):
    """Observe a durably emitted terminal run without owning its lifecycle.

    Implementations are post-terminal projectors only. They cannot affect the
    already-persisted run status or terminal event, and failures are isolated by
    :class:`RunTerminationCoordinator`.
    """

    async def observe_terminal_run(
        self,
        *,
        run: RunRecord,
        terminal_status: AgentRunStatus,
        reason: "TerminationReason",
        terminal_event: RuntimeEventEnvelope,
    ) -> None: ...


class TerminationReason(StrEnum):
    """Reason a run reached a terminal state, carried in the run-level event payload."""

    NORMAL_COMPLETION = "normal_completion"
    TOOL_FATAL_ERROR = "tool_fatal_error"
    EXECUTION_ERROR = "execution_error"
    CANCELLED = "cancelled"
    APPROVAL_TIMEOUT = "approval_timeout"
    BUDGET_EXCEEDED = "budget_exceeded"
    #: A single invocation exceeded ``ModelConfig.timeout_seconds``.
    RUN_TIMEOUT = "run_timeout"
    #: The run exceeded its wall-clock budget
    #: (``RuntimeExecutionSettings.run_deadline_seconds``). Distinct from
    #: ``RUN_TIMEOUT`` on purpose: one says "this call was slow", the other says
    #: "the whole loop ran too long", and they point at different fixes.
    RUN_DEADLINE_EXCEEDED = "run_deadline_exceeded"


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


@runtime_checkable
class RunProjectionDrainPort(Protocol):
    """A source of run-scoped facts that must land before the seal.

    Registering here is how a producer of *causal* events states that its work
    belongs inside the run's sealed prefix. The alternative — emitting whenever
    the producer happens to be scheduled — is what put ``artifact.created``
    after ``run_completed``, where no live client could ever see it.
    """

    async def drain_for_run(self, *, run: RunRecord) -> None:
        """Flush anything still pending for this run. Must be idempotent."""


class RunTerminationCoordinator:
    """Coordinator that closes a run cleanly by draining every pending projection before the terminal event.

    The seal authority. ``terminate`` is the only way a run reaches a terminal
    state, which makes it the only place that can honestly promise "everything
    this run caused is already in the ledger". It keeps that promise by
    draining, in order:

    1. the :class:`LifecycleLedger` — synthesising a terminal event for every
       leaked subagent/tool/model call;
    2. every registered :class:`RunProjectionDrainPort` — the artifact outbox
       today, whatever comes next tomorrow.

    Only then is the terminal event appended, sealing the causal prefix. The
    ordering rule used to live as prose in each producer; producers that never
    read the prose (or reached the ledger through a queue, where they could not
    obey it) broke it silently. Registration replaces the prose.
    """

    def __init__(
        self,
        *,
        event_producer: RuntimeEventProducer,
        projection_drains: Sequence[RunProjectionDrainPort] = (),
        terminal_observer: TerminalRunObserverPort | None = None,
    ) -> None:
        self._event_producer = event_producer
        self._projection_drains = tuple(projection_drains)
        self._terminal_observer = terminal_observer

    def register_projection_drain(self, drain: RunProjectionDrainPort) -> None:
        """Add a pending-projection source to flush before sealing.

        Late registration exists because worker composition builds this
        coordinator before the stores whose pending work it must drain.
        """

        self._projection_drains = (*self._projection_drains, drain)

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
        await self._drain_projections(run=run)
        terminal_event = await self._emit_run_terminal(
            run=run,
            terminal_status=terminal_status,
            reason=reason,
            summary=summary,
            cause=cause,
            extra_payload=extra_payload,
            extra_metadata=extra_metadata,
        )
        if terminal_event is not None:
            await self._observe_terminal_run(
                run=run,
                terminal_status=terminal_status,
                reason=reason,
                terminal_event=terminal_event,
            )

    async def _observe_terminal_run(
        self,
        *,
        run: RunRecord,
        terminal_status: AgentRunStatus,
        reason: TerminationReason,
        terminal_event: RuntimeEventEnvelope,
    ) -> None:
        observer = self._terminal_observer
        if observer is None:
            return
        try:
            await observer.observe_terminal_run(
                run=run,
                terminal_status=terminal_status,
                reason=reason,
                terminal_event=terminal_event,
            )
        except Exception:  # noqa: BLE001 - terminal state is already durable
            _LOGGER.warning(
                "run_termination.terminal_observer_failed",
                extra={
                    "metadata": {
                        "run_id": run.run_id,
                        "terminal_status": terminal_status.value,
                        "reason": reason.value,
                    }
                },
                exc_info=True,
            )

    async def _drain_projections(self, *, run: RunRecord) -> None:
        """Flush every registered projection source before the seal.

        Best-effort per drain, matching lifecycle reconciliation: one stuck
        source must not block the terminal event, because a run stuck open is
        worse for the user than a run missing one projection. A failure here is
        still recoverable — the outbox row survives and the queue bridge
        republishes it as a ``LATE_CAUSAL_RECOVERY`` amendment — so the loud
        log is the signal, not the lost data.
        """

        for drain in self._projection_drains:
            try:
                await drain.drain_for_run(run=run)
            except Exception:  # noqa: BLE001 — best-effort, mirrors lifecycle drain
                _LOGGER.warning(
                    "run_termination.projection_drain_failed",
                    extra={
                        "metadata": {
                            "run_id": run.run_id,
                            "drain": type(drain).__name__,
                        }
                    },
                    exc_info=True,
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
    ) -> RuntimeEventEnvelope | None:
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
            return await self._event_producer.append_api_event(
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
            return None


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
    "RunProjectionDrainPort",
    "RunTerminationCoordinator",
    "TerminalRunObserverPort",
    "TerminationReason",
)
