"""Queued approval-resolution command handling."""

from __future__ import annotations

from agent_runtime.agent.contracts import StreamEventSource
from agent_runtime.api.ports import EventStorePort, PersistencePort
from runtime_api.schemas import RuntimeApiEventType, RuntimeApprovalResolvedCommand, RuntimeEventDraft


class RuntimeApprovalHandler:
    """Record an approval-resolution event for future resume handling."""

    def __init__(self, *, persistence: PersistencePort, event_store: EventStorePort) -> None:
        self.persistence = persistence
        self.event_store = event_store

    async def handle(self, command: RuntimeApprovalResolvedCommand) -> None:
        run = self.persistence.get_run(org_id=command.org_id, run_id=command.run_id)
        if run is None:
            return
        self.event_store.append_event(
            RuntimeEventDraft(
                run_id=command.run_id,
                conversation_id=run.conversation_id,
                source=StreamEventSource.SYSTEM,
                event_type=RuntimeApiEventType.APPROVAL_RESOLVED,
                trace_id=run.trace_id,
                summary="Approval resolved",
                payload={
                    "approval_id": command.approval_id,
                    "decision": command.decision.value,
                },
            )
        )
