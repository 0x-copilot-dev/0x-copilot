"""Queued approval-resolution command handling."""

from __future__ import annotations

from agent_runtime.api.ports import EventStorePort, PersistencePort
from runtime_api.schemas import RuntimeApprovalResolvedCommand


class RuntimeApprovalHandler:
    """Consume durable approval-resolution commands after the API records the decision."""

    def __init__(self, *, persistence: PersistencePort, event_store: EventStorePort) -> None:
        self.persistence = persistence
        self.event_store = event_store

    async def handle(self, command: RuntimeApprovalResolvedCommand) -> None:
        self.persistence.get_run(org_id=command.org_id, run_id=command.run_id)
