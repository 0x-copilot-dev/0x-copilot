"""Artifact-aware bridge into the existing runtime command queue."""

from __future__ import annotations

from datetime import datetime

from agent_runtime.persistence.records import OutboxStatus, RuntimeWorkerClaim
from agent_runtime.persistence.records.outbox import RuntimeWorkerResult
from runtime_adapters._artifact_repository import (
    ArtifactCanonicalOutboxPort,
    ArtifactQueueMirrorPort,
)
from runtime_api.schemas import (
    RuntimeApprovalResolvedCommand,
    RuntimeCancelCommand,
    RuntimeEffectCommitCommand,
    RuntimeEffectReconcileCommand,
    RuntimeRunCommand,
    RuntimeStageCommitCommand,
)


class ArtifactAwareRuntimeQueue:
    """Expose canonical artifact intents through the existing queue port.

    File and memory metadata commit the stable outbox intent first. Before a
    worker claim, this wrapper idempotently mirrors any missing intent into the
    backend's existing runtime queue. Terminal queue state is folded back into
    the canonical artifact ledger so restart or queue compaction cannot enqueue
    a completed stable event forever.
    """

    def __init__(
        self,
        queue,
        canonical_outbox: ArtifactCanonicalOutboxPort,
    ) -> None:
        if not isinstance(queue, ArtifactQueueMirrorPort):
            raise TypeError("runtime queue lacks the public artifact mirror capability")
        if not isinstance(canonical_outbox, ArtifactCanonicalOutboxPort):
            raise TypeError("artifact metadata lacks the canonical outbox capability")
        self._queue = queue
        self._mirror = queue
        self._canonical_outbox = canonical_outbox

    @property
    def canonical_outbox(self) -> ArtifactCanonicalOutboxPort:
        """The canonical artifact intent ledger behind this queue.

        Public so run termination can drain pending artifact rows into the
        ledger *before* sealing. Previously the only way those rows became
        events was :meth:`claim_next` below — which an in-process worker cannot
        reach while it is busy executing the run that produced them.
        """

        return self._canonical_outbox

    async def enqueue_run(self, command: RuntimeRunCommand) -> None:
        await self._queue.enqueue_run(command)

    async def enqueue_cancel(self, command: RuntimeCancelCommand) -> None:
        await self._queue.enqueue_cancel(command)

    async def enqueue_approval_resolved(
        self, command: RuntimeApprovalResolvedCommand
    ) -> None:
        await self._queue.enqueue_approval_resolved(command)

    async def enqueue_stage_commit(self, command: RuntimeStageCommitCommand) -> None:
        await self._queue.enqueue_stage_commit(command)

    async def enqueue_effect_commit(self, command: RuntimeEffectCommitCommand) -> None:
        await self._queue.enqueue_effect_commit(command)

    async def enqueue_effect_reconcile(
        self, command: RuntimeEffectReconcileCommand
    ) -> bool:
        # Older queue mirrors returned ``None`` before the repair executor
        # needed to distinguish a replay.  Treat that legacy successful return
        # as an insertion while preserving the explicit false result of the
        # durable idempotent implementations.
        result = await self._queue.enqueue_effect_reconcile(command)
        return result is not False

    async def claim_next(
        self,
        *,
        worker_id: str,
        lock_expires_at: datetime,
    ) -> RuntimeWorkerClaim | None:
        await self._bridge_pending()
        return await self._queue.claim_next(
            worker_id=worker_id,
            lock_expires_at=lock_expires_at,
        )

    async def mark_complete(self, *, result: RuntimeWorkerResult) -> None:
        await self._queue.mark_complete(result=result)
        await self._canonical_outbox.acknowledge_artifact_event(
            event_id=result.command_id,
            status=OutboxStatus.COMPLETED,
        )

    async def mark_retry(self, *, result: RuntimeWorkerResult) -> None:
        await self._queue.mark_retry(result=result)

    async def mark_dead_letter(self, *, result: RuntimeWorkerResult) -> None:
        await self._queue.mark_dead_letter(result=result)
        await self._canonical_outbox.acknowledge_artifact_event(
            event_id=result.command_id,
            status=OutboxStatus.DEAD_LETTER,
        )

    async def _bridge_pending(self) -> None:
        for command in await self._canonical_outbox.pending_artifact_events():
            status = await self._mirror.artifact_event_status(event_id=command.event_id)
            if status in {OutboxStatus.COMPLETED, OutboxStatus.DEAD_LETTER}:
                await self._canonical_outbox.acknowledge_artifact_event(
                    event_id=command.event_id,
                    status=status,
                )
                continue
            await self._mirror.enqueue_artifact_event(command)

    def __getattr__(self, name: str):
        return getattr(self._queue, name)


__all__ = ("ArtifactAwareRuntimeQueue",)
