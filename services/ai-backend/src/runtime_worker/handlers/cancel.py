"""Queued cancel command handling."""

from __future__ import annotations

from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_termination import (
    RunTerminationCoordinator,
    TerminationReason,
)
from agent_runtime.persistence import with_optimistic_retry
from agent_runtime.surfaces_v2.config import SurfacesV2Flag
from runtime_api.schemas import (
    AgentRunStatus,
    RuntimeCancelCommand,
)
from runtime_worker.handlers.receipt_hook import emit_receipt_if_enabled


class RuntimeCancelHandler:
    """Apply a queued cancellation request."""

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        event_store: EventStorePort,
    ) -> None:
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
        """Cancel the run if it exists and the requester is the run's owner; otherwise no-ops."""
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
        # Generative Surfaces v2 (PRD-E1): a cancelled run's receipt matters
        # most. Fold + append the receipt before the terminal event, gated on
        # SURFACES_V2 (flag-off ⇒ no-op, byte-identical to today).
        await emit_receipt_if_enabled(
            enabled=SurfacesV2Flag.enabled(),
            event_producer=self.event_producer,
            event_store=self.event_store,
            run=run,
        )
        await self.run_termination.terminate(
            run=run,
            terminal_status=AgentRunStatus.CANCELLED,
            reason=TerminationReason.CANCELLED,
            summary="Run cancelled",
            extra_payload={"cancel_reason": command.reason},
        )
