"""Queued cancel command handling."""

from __future__ import annotations

from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.persistence import with_optimistic_retry
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
        persistence: PersistencePort,
        event_store: EventStorePort,
    ) -> None:
        # Always async on the inside (Phase D).
        self.persistence: PersistencePort = persistence
        self.event_store: EventStorePort = event_store
        self.event_producer = RuntimeEventProducer(
            persistence=self.persistence,
            event_store=self.event_store,
        )

    async def handle(self, command: RuntimeCancelCommand) -> None:
        run = await self.persistence.get_run(
            org_id=command.org_id, run_id=command.run_id
        )
        if run is None:
            return
        if run.user_id != command.requested_by_user_id:
            return
        run = await with_optimistic_retry(
            lambda: self.persistence.update_run_status(
                run_id=command.run_id,
                status=AgentRunStatus.CANCELLED,
            )
        )
        await self.event_producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.RUN_CANCELLED,
            summary="Run cancelled",
            payload={"reason": command.reason},
        )
