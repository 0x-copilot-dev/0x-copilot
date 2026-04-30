"""Queued cancel command handling."""

from __future__ import annotations

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.api.ports import EventStorePort, PersistencePort
from runtime_api.schemas import AgentRunStatus, RuntimeApiEventType, RuntimeCancelCommand


class RuntimeCancelHandler:
    """Apply a queued cancellation request."""

    def __init__(self, *, persistence: PersistencePort, event_store: EventStorePort) -> None:
        self.persistence = persistence
        self.event_store = event_store
        self.event_producer = RuntimeEventProducer(
            persistence=self.persistence,
            event_store=self.event_store,
        )

    async def handle(self, command: RuntimeCancelCommand) -> None:
        run = self.persistence.update_run_status(
            run_id=command.run_id,
            status=AgentRunStatus.CANCELLED,
        )
        self.event_producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.RUN_CANCELLED,
            summary="Run cancelled",
            payload={"reason": command.reason},
        )
