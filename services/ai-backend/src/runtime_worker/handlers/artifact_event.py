"""Publish canonical artifact mutations through the existing run ledger."""

from __future__ import annotations

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.ledger_seal import LedgerAmendment, LedgerAmendmentReason
from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.execution.contracts import RuntimeErrorCode, StreamEventSource
from agent_runtime.execution.errors import AgentRuntimeError
from runtime_api.schemas import (
    RuntimeApiEventType,
    RuntimeArtifactEventCommand,
)


class RuntimeArtifactEventHandler:
    """Idempotently project one artifact outbox command into runtime events."""

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        event_store: EventStorePort,
    ) -> None:
        self._persistence = persistence
        self._producer = RuntimeEventProducer(
            persistence=persistence,
            event_store=event_store,
        )

    async def handle(self, command: RuntimeArtifactEventCommand) -> None:
        """Append the reference-only event after re-validating run ownership."""

        run = await self._persistence.get_run(
            org_id=command.org_id,
            run_id=command.run_id,
        )
        if run is None:
            raise AgentRuntimeError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Artifact event run is unavailable.",
                retryable=False,
            )
        if (
            run.user_id != command.user_id
            or run.conversation_id != command.conversation_id
            or run.trace_id != command.trace_id
        ):
            raise AgentRuntimeError(
                RuntimeErrorCode.PERMISSION_DENIED,
                "Artifact event scope does not match its run.",
                retryable=False,
            )

        await self._producer.append_api_event(
            run=run,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType(command.event_type.value),
            payload=command.payload,
            event_id=command.event_id,
            created_at=command.created_at,
            # The recovery lane. On the normal path these events are published
            # inline at commit time and this command is an idempotent replay
            # that never reaches the ledger. It only does real work when the
            # inline publish and the pre-seal drain both failed — a crash — by
            # which point the run has sealed. Declaring the amendment keeps the
            # sealed prefix's completeness claim truthful (the event is not
            # pretending it arrived in time) while still surfacing the artifact
            # instead of dropping it. Without the declaration, fail-closed
            # enforcement would raise here and the artifact would be lost.
            amendment=LedgerAmendment(
                reason=LedgerAmendmentReason.LATE_CAUSAL_RECOVERY
            ),
        )


__all__ = ("RuntimeArtifactEventHandler",)
