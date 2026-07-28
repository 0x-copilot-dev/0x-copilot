"""Reconcile canonical F10 attempt usage into the established usage pipeline.

The model-invocation journal is the authority for attempts, including attempts
that failed after a provider accepted a request.  Streaming remains an equally
valid source for an already-materialized terminal model call, so this adapter
uses the journal's optional ``usage_record_id`` only as a *dedupe witness*;
it never creates a second charge for that streamed row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from agent_runtime.execution.model_invocation.journal import (
    ModelAttemptUsageRecord,
    ModelInvocationPlannedRecord,
    ModelRouteEligibleRecord,
    SequencedModelInvocationRecord,
)
from agent_runtime.observability.token_usage import NormalizedTokenUsage
from agent_runtime.persistence.records import RuntimeModelCallUsageRecord
from runtime_api.schemas import RunRecord


def _sum_usage(
    left: NormalizedTokenUsage, right: NormalizedTokenUsage
) -> NormalizedTokenUsage:
    """Add independent attempts (unlike chunk-level cumulative ``merge``)."""

    return NormalizedTokenUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        cached_input_tokens=left.cached_input_tokens + right.cached_input_tokens,
        cache_creation_input_tokens=(
            left.cache_creation_input_tokens + right.cache_creation_input_tokens
        ),
        reasoning_tokens=left.reasoning_tokens + right.reasoning_tokens,
        audio_input_tokens=left.audio_input_tokens + right.audio_input_tokens,
        audio_output_tokens=left.audio_output_tokens + right.audio_output_tokens,
        provider_cache_metadata_observed=(
            left.provider_cache_metadata_observed
            or right.provider_cache_metadata_observed
        ),
    )


@dataclass(frozen=True)
class ModelInvocationUsageReconciliation:
    """Replay-safe materialization result for one run.

    ``usage`` is the journal-only delta: callers add it to their pre-existing
    streamed accumulator only after passing its streamed terminal IDs below.
    ``cost_micro_usd`` is the final provider-reported total for all finalized
    journal attempts, including a streamed terminal that was excluded from the
    usage delta.
    ``records`` always use ``attempt_id`` as their row identity.  A database
    restart can therefore replay the journal safely through the existing
    idempotent ``record_model_call_usage`` path.  ``streamed_attempt_ids`` are
    not represented as new rows; their usage was already included by the
    streaming accumulator.
    """

    records: tuple[RuntimeModelCallUsageRecord, ...]
    usage: NormalizedTokenUsage
    cost_micro_usd: int
    finalized_attempt_ids: frozenset[str]
    streamed_attempt_ids: frozenset[str]


class ModelInvocationUsageReconciler:
    """Produce exactly one usage row for every independently finalized attempt.

    The reconciler has no persistence dependency.  Its stable row IDs make the
    regular usage recorder/database idempotency boundary authoritative across a
    worker crash; ``already_materialized_ids`` permits file/in-memory callers to
    supply their durable read-back set as well.
    """

    def reconcile(
        self,
        *,
        run: RunRecord,
        records: tuple[SequencedModelInvocationRecord, ...],
        pricing_at: datetime,
        streamed_usage_ids: frozenset[str] = frozenset(),
        already_materialized_ids: frozenset[str] = frozenset(),
    ) -> ModelInvocationUsageReconciliation:
        emitted: dict[str, RuntimeModelCallUsageRecord] = {}
        total = NormalizedTokenUsage()
        cost_micro_usd = 0
        finalized: set[str] = set()
        streamed: set[str] = set()

        invocations: dict[str, ModelInvocationPlannedRecord] = {}
        routes: dict[tuple[str, str], ModelRouteEligibleRecord] = {}
        for sequenced in records:
            record = sequenced.record
            if isinstance(record, ModelInvocationPlannedRecord):
                invocations[record.invocation_id] = record
            elif isinstance(record, ModelRouteEligibleRecord):
                routes[(record.invocation_id, record.deployment_id)] = record

        for sequenced in records:
            record = sequenced.record
            if not isinstance(record, ModelAttemptUsageRecord):
                continue
            if record.run_id != run.run_id:
                raise ValueError("attempt usage record does not belong to run")
            # EventJournalModelInvocationStore has already validated replay. A
            # duplicate sequence/record here must be harmless on overlapping
            # fetches, but conflicting facts never silently win.
            if record.attempt_id in finalized:
                continue
            finalized.add(record.attempt_id)
            usage = NormalizedTokenUsage(
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
                cached_input_tokens=record.cached_input_tokens,
                cache_creation_input_tokens=record.cache_creation_input_tokens,
                reasoning_tokens=record.reasoning_tokens,
                audio_input_tokens=record.audio_input_tokens,
                audio_output_tokens=record.audio_output_tokens,
            )
            # An explicit non-reported finalizer is intentionally a zero-cost,
            # explicit fact, not an inferred missing usage charge.
            if not record.provider_reported:
                continue
            cost_micro_usd += record.cost_microusd
            if (
                record.usage_record_id is not None
                and record.usage_record_id in streamed_usage_ids
            ):
                streamed.add(record.attempt_id)
                continue
            if record.attempt_id in already_materialized_ids:
                continue
            if record.attempt_id in emitted:
                continue
            invocation = invocations.get(record.invocation_id)
            route = routes.get((record.invocation_id, record.deployment_id))
            if invocation is None or route is None:
                raise ValueError("attempt usage lacks its validated invocation route")
            task_id, subagent_id = _scope_attribution(invocation.execution_scope)
            emitted[record.attempt_id] = RuntimeModelCallUsageRecord(
                id=record.attempt_id,
                org_id=run.org_id,
                run_id=run.run_id,
                conversation_id=run.conversation_id,
                trace_id=run.trace_id,
                user_id=run.user_id,
                task_id=task_id,
                subagent_id=subagent_id,
                model_provider=route.provider,
                model_name=route.model_name,
                purpose=invocation.purpose.value,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cached_input_tokens=usage.cached_input_tokens,
                cache_creation_input_tokens=usage.cache_creation_input_tokens,
                reasoning_tokens=usage.reasoning_tokens,
                audio_input_tokens=usage.audio_input_tokens,
                audio_output_tokens=usage.audio_output_tokens,
                total_tokens=usage.total_tokens,
                duration_ms=record.duration_ms,
                # This is canonical provider-reported spend. ``price_revision``
                # is journal-bound provenance; UsageRecorder must not price it
                # again and overwrite a provider-reported final amount.
                cost_micro_usd=record.cost_microusd,
                pricing_version=route.price_revision,
                created_at=pricing_at,
            )
            total = _sum_usage(total, usage)

        return ModelInvocationUsageReconciliation(
            records=tuple(emitted.values()),
            usage=total,
            cost_micro_usd=cost_micro_usd,
            finalized_attempt_ids=frozenset(finalized),
            streamed_attempt_ids=frozenset(streamed),
        )


def _scope_attribution(execution_scope: str) -> tuple[str | None, str | None]:
    """Map the stable subagent execution-scope convention when representable."""

    prefix = "subagent:"
    if execution_scope.startswith(prefix) and len(execution_scope) > len(prefix):
        scope_id = execution_scope[len(prefix) :]
        return scope_id, scope_id
    return None, None


__all__ = (
    "ModelInvocationUsageReconciler",
    "ModelInvocationUsageReconciliation",
)
