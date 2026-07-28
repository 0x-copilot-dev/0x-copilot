"""Authoritative terminal integration for F10 journal projections and usage."""

from __future__ import annotations

from datetime import datetime

from agent_runtime.api.ports import PersistencePort
from agent_runtime.execution.model_invocation.journal import ModelInvocationStorePort
from agent_runtime.observability.model_invocation_projection import (
    ModelInvocationMetricsProjectionCoordinator,
)
from agent_runtime.observability.model_invocation_usage import (
    ModelInvocationUsageReconciler,
)
from agent_runtime.observability.usage_recorder import UsageRecorder
from runtime_api.schemas import RunRecord
from runtime_worker.run_metrics import AssistantRunMetrics


class ModelInvocationTerminalIntegration:
    """Reconcile one terminal run after its outer terminal fact is durable.

    The journal store validates scope, digests, ordering, and terminal records
    before either metrics or billing see them.  The result's usage is only the
    non-streamed journal delta; its cost is the canonical total across every
    provider-reported attempt, including a deduped streamed terminal.
    """

    def __init__(
        self,
        *,
        journal: ModelInvocationStorePort | None,
        usage_recorder: UsageRecorder,
        persistence: PersistencePort | None = None,
    ) -> None:
        self._journal = journal
        self._usage_recorder = usage_recorder
        self._persistence = persistence
        self._usage = ModelInvocationUsageReconciler()
        self._metrics = (
            ModelInvocationMetricsProjectionCoordinator(journal=journal)
            if journal is not None
            else None
        )

    async def finalize(
        self,
        *,
        run: RunRecord,
        metrics: AssistantRunMetrics,
        subject_fingerprint: str | None,
        completed_at: datetime,
    ) -> None:
        """Write stable attempt rows, merge the delta, then seal projection.

        No snapshot means F10 was never bound for this path; legacy usage stays
        byte-for-byte unchanged.  A terminal run with an empty F10 journal also
        has no projector to seal.
        """

        if self._journal is None or subject_fingerprint is None:
            return
        records = await self._journal.list_for_run(
            org_id=run.org_id,
            run_id=run.run_id,
            subject_fingerprint=subject_fingerprint,
        )
        if not records:
            return
        materialized_ids = frozenset()
        if self._persistence is not None:
            materialized_ids = frozenset(
                row.id
                for row in await self._persistence.query_model_call_usage_for_run(
                    org_id=run.org_id,
                    run_id=run.run_id,
                )
            )
        reconciliation = self._usage.reconcile(
            run=run,
            records=records,
            pricing_at=completed_at,
            streamed_usage_ids=metrics.finalized_model_call_ids(),
            already_materialized_ids=materialized_ids,
            already_materialized_usage_ids=materialized_ids,
        )
        for record in reconciliation.records:
            await self._usage_recorder.record_call(record, pricing_at=completed_at)
        metrics.record_model_invocation_usage(reconciliation.usage, cost_micro_usd=0)
        # A nonempty journal proves the provider-attempt total is authoritative
        # for this run, including the zero-cost/unreported terminal case.
        metrics.set_model_invocation_cost_micro_usd(reconciliation.cost_micro_usd)
        assert self._metrics is not None
        await self._metrics.replay(
            org_id=run.org_id,
            run_id=run.run_id,
            subject_fingerprint=subject_fingerprint,
            outer_run_terminal=True,
        )

    async def record_run_usage(
        self,
        *,
        run: RunRecord,
        metrics: AssistantRunMetrics,
        completed_at: datetime,
        status: str,
    ) -> int | None:
        """Persist the terminal run aggregate after journal reconciliation.

        The call rows are written by :meth:`finalize` first.  This ordering
        makes the run aggregate and its budget charge see the journal's
        canonical provider-reported cost instead of a partial streaming view.
        ``UsageRecorder`` owns the durable idempotency boundary for ``run_id``.
        """

        record = metrics.to_usage_record(run, completed_at=completed_at, status=status)
        result = await self._usage_recorder.record_run(
            record,
            pricing_at=completed_at,
        )
        return (
            result.cost_micro_usd
            if result.cost_micro_usd is not None
            else record.cost_micro_usd
        )


__all__ = ("ModelInvocationTerminalIntegration",)
