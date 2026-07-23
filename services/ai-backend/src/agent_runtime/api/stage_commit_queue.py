"""Transport adapter binding the pure :class:`WriteStager` to the durable queue.

The stager (``agent_runtime.surfaces_v2.staging``) is pure domain and never
imports ``runtime_api``; it enqueues the commit command through the injected
:class:`~agent_runtime.surfaces_v2.staging.StageCommitQueuePort` using primitives
only. This adapter is the one place that binds that port to the transport: it
builds the :class:`RuntimeStageCommitCommand` (+ the W3C trace-propagation carrier
so the worker continues the API's trace tree) and appends it via
``RuntimeQueuePort.enqueue_stage_commit``.

Mirrors how ``RuntimeStageLedger`` keeps the emission logic out of the pure
stager while the transport concerns live in the ``runtime_api``-aware layer.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_runtime.api.ports import RuntimeQueuePort
from agent_runtime.observability.queue_propagation import QueueTracePropagator
from runtime_api.schemas import RuntimeStageCommitCommand


@dataclass(frozen=True)
class RuntimeStageCommitQueue:
    """Concrete ``StageCommitQueuePort`` over ``RuntimeQueuePort`` (PRD-D2)."""

    queue: RuntimeQueuePort

    async def enqueue_stage_commit(
        self,
        *,
        stage_id: str,
        run_id: str,
        org_id: str,
        user_id: str,
        conversation_id: str,
        rev: int,
        decision_seq: int,
        row_keys: tuple[str, ...] | None = None,
    ) -> None:
        """Enqueue one durable commit command for an approved ``(stage_id, rev)``.

        ``row_keys`` (PRD-D3) is the approved row set for a bulk apply, or ``None``
        for a single-artifact (D1/D2) commit.
        """

        await self.queue.enqueue_stage_commit(
            RuntimeStageCommitCommand(
                stage_id=stage_id,
                run_id=run_id,
                org_id=org_id,
                user_id=user_id,
                conversation_id=conversation_id,
                rev=rev,
                decision_seq=decision_seq,
                row_keys=row_keys,
                trace_propagation=QueueTracePropagator.inject(),
            )
        )


__all__ = ["RuntimeStageCommitQueue"]
