"""Queued cancel command handling."""

from __future__ import annotations

from agent_runtime.api.async_ports import AsyncEventStorePort, AsyncPersistencePort
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.execution.contracts import StreamEventSource
from runtime_api.schemas import (
    AgentRunStatus,
    RuntimeApiEventType,
    RuntimeCancelCommand,
)


class RuntimeCancelHandler:
    """Apply a queued cancellation request."""

    def __init__(
        self,
        *,
        persistence: PersistencePort | AsyncPersistencePort,
        event_store: EventStorePort | AsyncEventStorePort,
    ) -> None:
        self.persistence = persistence
        self.event_store = event_store
        # Producer self-normalizes sync/async ports.
        self.event_producer = RuntimeEventProducer(
            persistence=persistence,
            event_store=event_store,
        )

    async def handle(self, command: RuntimeCancelCommand) -> None:
        run = self.persistence.update_run_status(
            run_id=command.run_id,
            status=AgentRunStatus.CANCELLED,
        )
        await self.event_producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.RUN_CANCELLED,
            summary="Run cancelled",
            payload={"reason": command.reason},
        )
