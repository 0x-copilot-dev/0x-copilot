"""Queued cancel command handling."""

from __future__ import annotations

from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.api.ports import EventStorePort, PersistencePort
from runtime_api.schemas import AgentRunStatus, RuntimeApiEventType, RuntimeCancelCommand, RuntimeEventDraft


class RuntimeCancelHandler:
    """Apply a queued cancellation request."""

    def __init__(self, *, persistence: PersistencePort, event_store: EventStorePort) -> None:
        self.persistence = persistence
        self.event_store = event_store

    async def handle(self, command: RuntimeCancelCommand) -> None:
        run = self.persistence.update_run_status(
            run_id=command.run_id,
            status=AgentRunStatus.CANCELLED,
        )
        self.event_store.append_event(
            RuntimeEventDraft(
                run_id=command.run_id,
                conversation_id=run.conversation_id,
                source=StreamEventSource.SYSTEM,
                event_type=RuntimeApiEventType.RUN_CANCELLED,
                trace_id=run.trace_id,
                summary="Run cancelled",
                payload={"reason": command.reason},
            )
        )
