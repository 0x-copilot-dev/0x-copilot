"""Queued cancel command handling."""

from __future__ import annotations

from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_termination import (
    RunTerminationCoordinator,
    TerminationReason,
)
from agent_runtime.persistence import with_optimistic_retry
from runtime_api.schemas import (
    AgentRunStatus,
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
        self.run_termination = RunTerminationCoordinator(
            event_producer=self.event_producer,
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
        await self.run_termination.terminate(
            run=run,
            terminal_status=AgentRunStatus.CANCELLED,
            reason=TerminationReason.CANCELLED,
            summary="Run cancelled",
            extra_payload={"cancel_reason": command.reason},
        )
