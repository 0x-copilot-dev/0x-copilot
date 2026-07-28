"""Inline projection of committed artifact facts onto the run ledger.

The timely half of artifact publication. ``ArtifactService`` commits metadata
and its outbox rows in one durable write; this adapter turns those rows into
run-ledger events *immediately*, while the run is still open, so the events land
inside the run's sealed causal prefix and reach live SSE clients.

It is deliberately a peer of :class:`~runtime_worker.handlers.artifact_event
.RuntimeArtifactEventHandler`, not a replacement. That handler is the recovery
lane, reached through the work queue when a crash orphaned an outbox row; by
then the run has sealed, so it publishes as a ``LATE_CAUSAL_RECOVERY``
amendment. Both write the same event under the same ``event_id``, so whichever
runs first wins and the second is an idempotent replay.
"""

from __future__ import annotations

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.ports import PersistencePort
from agent_runtime.artifacts.contracts import ArtifactLedgerEvent
from agent_runtime.execution.contracts import StreamEventSource
from runtime_api.schemas import RunRecord, RuntimeApiEventType


class RuntimeArtifactLedgerPublisher:
    """Append one committed artifact fact to its own run's ledger.

    Scope is re-proved against the persisted run rather than trusted from the
    event: the payload is derived from model input, and an artifact must never
    be able to write into a run it does not belong to.
    """

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        event_producer: RuntimeEventProducer,
    ) -> None:
        self._persistence = persistence
        self._event_producer = event_producer

    async def publish(self, event: ArtifactLedgerEvent) -> None:
        """Append the reference-only artifact event to its run."""

        scope = event.scope
        run = await self._persistence.get_run(
            org_id=scope.org_id,
            run_id=scope.run_id,
        )
        if run is None or run.user_id != scope.user_id:
            # Silent no-op rather than a raise: the caller treats inline
            # publication as best-effort, and a scope that cannot be proved
            # here is not one this adapter may repair.
            return
        await self._event_producer.append_api_event(
            run=run,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType(event.event_type.value),
            payload=dict(event.payload),
            event_id=event.event_id,
            created_at=event.created_at,
        )


class ArtifactOutboxProjectionDrain:
    """Flush artifact outbox rows into the ledger before a run seals.

    The safety net behind inline publication. Registered with
    ``RunTerminationCoordinator`` so a row whose inline publish failed still
    lands *inside* the sealed prefix, where live clients and the canvas fold
    can see it, rather than after the seal where only a replay would.

    Idempotent through ``event_id``: rows already published inline re-append as
    replays and cost nothing.
    """

    def __init__(
        self,
        *,
        canonical_outbox: object,
        event_producer: RuntimeEventProducer,
    ) -> None:
        self._canonical_outbox = canonical_outbox
        self._event_producer = event_producer

    async def drain_for_run(self, *, run: RunRecord) -> None:
        """Publish this run's still-pending artifact events, causally.

        Scoped to the terminating run: pending rows belonging to *other* runs
        are none of this seal's business, and republishing them here would
        append into ledgers this coordinator has no authority over.
        """

        pending = getattr(self._canonical_outbox, "pending_artifact_events", None)
        if not callable(pending):
            return
        for command in await pending():
            if command.run_id != run.run_id or command.org_id != run.org_id:
                continue
            await self._event_producer.append_api_event(
                run=run,
                source=StreamEventSource.RUNTIME,
                event_type=RuntimeApiEventType(command.event_type.value),
                payload=dict(command.payload),
                event_id=command.event_id,
                created_at=command.created_at,
            )


__all__ = (
    "ArtifactOutboxProjectionDrain",
    "RuntimeArtifactLedgerPublisher",
)
