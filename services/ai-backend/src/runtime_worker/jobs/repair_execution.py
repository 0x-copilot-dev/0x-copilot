"""Fail-closed D12 execution for already-planned effect reconciliation.

The repair planner is intentionally broad: it can describe several lifecycle
families without granting any of them a mutation capability.  This module owns
the first executable lane only: a previously claimed or indeterminate effect
may be *reconciled*, never applied or resent.  Artifact physical cleanup stays
with ``ArtifactLifecycleJobs`` and its hold/reference locks; unknown repair
actions stay unsupported until they have an equivalent safe owner.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
import hashlib
import os
from typing import Protocol, runtime_checkable

from agent_runtime.effects.claims import EffectClaim
from agent_runtime.observability.lifecycle_metrics import (
    LifecycleOperationalMetrics,
    get_lifecycle_operational_metrics,
)
from agent_runtime.surfaces_v2.repair_reconciliation import (
    RepairAction,
    RepairDecision,
    RepairDecisionState,
    RepairPlanner,
)
from runtime_api.schemas import RuntimeEffectReconcileCommand
from runtime_worker.jobs.repair_planning import (
    EffectClaimRepairSnapshotCollector,
    RepairCandidateExecutor,
    RepairExecutionResult,
)


class RepairExecutionEnv:
    """Explicit rollout switch for the optional D12 execution seam."""

    ENABLED = "REPAIR_EXECUTION_ENABLED"

    @classmethod
    def enabled(cls) -> bool:
        raw = os.environ.get(cls.ENABLED)
        if raw is None or raw.strip() == "":
            return False
        return raw.strip().lower() in {"1", "true", "yes", "on"}


@runtime_checkable
class EffectReconcileQueuePort(Protocol):
    """The only durable mutation capability granted to this executor."""

    async def enqueue_effect_reconcile(
        self, command: RuntimeEffectReconcileCommand
    ) -> bool:
        """Persist a command, returning false only for an identical replay."""


class RepairReconciliationExecutor(RepairCandidateExecutor):
    """Revalidate and enqueue body-free reconciliation work exactly once.

    The executor has no artifact store, deletion method, approval method, or
    effect executor.  A worker can only receive a durable command naming an
    already-existing claim.  The eventual handler and coordinator preserve the
    stronger invariant that reconciliation never calls ``apply``.
    """

    _ACTION = RepairAction.EFFECT_RECONCILE_CANDIDATE

    def __init__(
        self,
        *,
        collector: EffectClaimRepairSnapshotCollector,
        queue: EffectReconcileQueuePort,
        metrics: LifecycleOperationalMetrics | None = None,
    ) -> None:
        self._collector = collector
        self._queue = queue
        self._metrics = (
            metrics if metrics is not None else get_lifecycle_operational_metrics()
        )

    async def execute(
        self,
        *,
        tenant_id: str,
        decisions: Sequence[RepairDecision],
        now: datetime,
    ) -> RepairExecutionResult:
        """Freshly validate each persisted candidate before enqueueing it.

        Every result is aggregate-only.  A changed hold, graph, reference,
        tenant, claim state, or timestamp produces a safe no-op rather than an
        action based on the earlier snapshot.  A queue failure is retryable by
        the caller because no source cursor advances for that cycle.
        """

        queued = 0
        already_queued = 0
        withheld = 0
        unsupported = 0
        failed = 0
        for decision in decisions:
            if (
                decision.state is not RepairDecisionState.CANDIDATE
                or decision.action is None
            ):
                continue
            if decision.action is not self._ACTION:
                unsupported += 1
                self._record_metric(outcome="unsupported")
                continue
            revalidated = await self._collector.revalidate_effect_claim(
                tenant_id=tenant_id,
                claim_id=decision.candidate_id,
                now=now,
            )
            if revalidated is None:
                withheld += 1
                self._record_metric(outcome="withheld")
                continue
            if (
                revalidated.claim.org_id != tenant_id
                or revalidated.claim.claim_id != decision.candidate_id
                or revalidated.record.tenant_id != tenant_id
                or revalidated.record.candidate_id != decision.candidate_id
            ):
                # The collector already enforces this binding, but retaining a
                # second check at the mutation boundary makes an accidental
                # cross-tenant or mismatched test/durable adapter fail closed.
                withheld += 1
                self._record_metric(outcome="withheld")
                continue
            fresh = RepairPlanner.decide_record(revalidated.record)
            if (
                fresh.state is not RepairDecisionState.CANDIDATE
                or fresh.action is not self._ACTION
                or fresh.candidate_id != decision.candidate_id
            ):
                withheld += 1
                self._record_metric(outcome="withheld")
                continue
            command = build_reconcile_command(revalidated.claim)
            if command is None:
                withheld += 1
                self._record_metric(outcome="withheld")
                continue
            try:
                inserted = await self._queue.enqueue_effect_reconcile(command)
            except Exception:
                # Do not surface a backend message, reference, or claim id in
                # logs.  The caller retains its scan cursor and retries this
                # exact deterministic command later.
                failed += 1
                self._record_metric(outcome="failed")
                continue
            if inserted:
                queued += 1
                self._record_metric(outcome="queued")
            else:
                already_queued += 1
                self._record_metric(outcome="already_queued")
        return RepairExecutionResult(
            queued=queued,
            already_queued=already_queued,
            withheld=withheld,
            unsupported=unsupported,
            failed=failed,
        )

    def _record_metric(self, *, outcome: str) -> None:
        try:
            self._metrics.record_repair_execution(
                action="effect_reconcile",
                outcome=outcome,
            )
        except Exception:
            # Metrics must not decide whether a safe recovery command exists.
            return


def build_reconcile_command(
    claim: EffectClaim,
) -> RuntimeEffectReconcileCommand | None:
    """Build a deterministic, body-free recovery command for ``claim``.

    ``updated_at`` is part of the command identity.  A durable claim transition
    therefore produces a new command identity only when its safe state changes;
    a crash between queue insertion and cursor advancement repeats byte-for-byte
    and is absorbed by the durable queue's idempotency rule.
    """

    updated_at = _parse_utc_timestamp(claim.updated_at)
    if updated_at is None:
        return None
    digest = hashlib.sha256(
        "\0".join(
            (
                "repair-reconcile-v1",
                claim.org_id,
                claim.run_id,
                claim.claim_id,
                claim.state.value,
                claim.executor.value,
                claim.updated_at,
            )
        ).encode("utf-8")
    ).hexdigest()
    return RuntimeEffectReconcileCommand(
        command_id=f"repair_reconcile_{digest[:48]}",
        org_id=claim.org_id,
        run_id=claim.run_id,
        claim_id=claim.claim_id,
        created_at=updated_at,
    )


def _parse_utc_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


__all__ = (
    "EffectReconcileQueuePort",
    "RepairExecutionEnv",
    "RepairReconciliationExecutor",
    "build_reconcile_command",
)
