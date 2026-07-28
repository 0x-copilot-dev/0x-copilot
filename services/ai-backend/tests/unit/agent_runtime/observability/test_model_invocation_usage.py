"""F10 journal usage is exactly-once and shares the existing usage shapes."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from agent_runtime.control_plane import (
    BudgetEnvelope,
    FeatureMode,
    FeatureModeSet,
    RunControlBinding,
    RunControlSnapshot,
    RunPolicyRevisions,
)
from agent_runtime.execution.call_identity import RuntimeModelCallIdentity
from agent_runtime.execution.model_invocation import (
    ModelAttemptAdmissionRecord,
    ModelAttemptDecision,
    ModelAttemptDecisionKind,
    ModelAttemptDecisionReason,
    ModelAttemptUsageRecord,
    ModelCredentialMode,
    ModelFallbackPolicy,
    ModelInvocationBudget,
    ModelInvocationPlannedRecord,
    ModelRouteEligibleRecord,
    ModelRouteEntry,
    ModelRoutePlan,
    SequencedModelInvocationRecord,
)
from agent_runtime.observability.attribution import Purpose
from agent_runtime.observability.model_invocation_projection import (
    ModelInvocationMetricsProjectionCoordinator,
)
from agent_runtime.observability.model_invocation_usage import (
    ModelInvocationUsageReconciler,
)
from agent_runtime.observability.token_usage import NormalizedTokenUsage
from agent_runtime.observability.usage_recorder import (
    InMemoryUsageRecorder,
    UsageRecordingResult,
)
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import RunRecord
from runtime_worker.model_invocation_terminal import ModelInvocationTerminalIntegration
from runtime_worker.run_metrics import AssistantRunMetrics

_NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)


class _PersistentRecorder:
    """Minimal durable recorder double with normal run-row idempotency."""

    def __init__(self, store: InMemoryRuntimeApiStore) -> None:
        self.store = store
        self.calls: list[object] = []
        self.runs: list[object] = []

    async def record_call(self, record, *, pricing_at):  # type: ignore[no-untyped-def]
        del pricing_at
        self.calls.append(record)
        await self.store.record_model_call_usage(record)
        return UsageRecordingResult(cost_micro_usd=record.cost_micro_usd)

    async def record_run(self, record, *, pricing_at):  # type: ignore[no-untyped-def]
        del pricing_at
        self.runs.append(record)
        await self.store.record_run_usage(record)
        return UsageRecordingResult(cost_micro_usd=record.cost_micro_usd)


def _run() -> RunRecord:
    return RunRecord.model_construct(
        run_id="run-usage-reconciliation",
        conversation_id="conversation-usage-reconciliation",
        org_id="org-usage-reconciliation",
        user_id="user-usage-reconciliation",
        trace_id="trace-usage-reconciliation",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        runtime_context=SimpleNamespace(assistant_id=None),
    )


def _invocation(
    *, execution_scope: str = "supervisor", purpose: Purpose = Purpose.MAIN
) -> ModelInvocationPlannedRecord:
    budget = BudgetEnvelope.create(
        budget_envelope_id="budget-usage-reconciliation",
        revision="budget-r1",
        max_model_turns=8,
        max_tool_calls=8,
    )
    snapshot = RunControlSnapshot.create(
        run_id=_run().run_id,
        conversation_id=_run().conversation_id,
        subject_fingerprint="a" * 64,
        deployment_profile="single_user_desktop",
        harness_variant_ref="harness://stable/r1",
        task_policy_selection_ref="task-policy://unknown.general/r1",
        policy_revisions=RunPolicyRevisions(
            prompt="prompt-r1",
            capability="capability-r1",
            context="context-r1",
            tool_controller="tool-r1",
            concurrency="concurrency-r1",
            dataflow="dataflow-r1",
            mcp_freshness="mcp-r1",
            delegation="delegation-r1",
            model_route="model-route-r1",
            workspace_edit="workspace-r1",
            answer_verification="answer-r1",
        ),
        feature_modes=FeatureModeSet(f10=FeatureMode.ENFORCE),
        budget_envelope_ref=budget.revision_ref,
        assignment_revision="assignment-r1",
        snapshot_id="snapshot-usage-reconciliation",
        created_at=_NOW,
    )
    plan = ModelRoutePlan.create(
        routes=(
            ModelRouteEntry(
                deployment_id="deployment-primary",
                descriptor_revision="descriptor-r1",
                endpoint_ref=f"endpoint_{'1' * 32}",
                provider="openai",
                model_name="gpt-5.4-mini",
                region="us-east",
                credential_mode=ModelCredentialMode.BYOK,
                price_revision="price-r1",
                max_input_tokens=200_000,
                max_output_tokens=16_000,
            ),
            ModelRouteEntry(
                deployment_id="deployment-fallback",
                descriptor_revision="descriptor-r2",
                endpoint_ref=f"endpoint_{'2' * 32}",
                provider="anthropic",
                model_name="claude-sonnet",
                region="us-east",
                credential_mode=ModelCredentialMode.BYOK,
                price_revision="price-r2",
                max_input_tokens=200_000,
                max_output_tokens=16_000,
            ),
        ),
        fallback_policy=ModelFallbackPolicy.QUALIFIED_EQUIVALENT,
        exclusions=(),
        budget=ModelInvocationBudget(max_attempts=3, max_same_deployment_attempts=3),
    )
    return ModelInvocationPlannedRecord.create(
        binding=RunControlBinding(
            snapshot=snapshot, effective_modes=snapshot.feature_modes, decisions=()
        ),
        identity=RuntimeModelCallIdentity(
            run_id=snapshot.run_id,
            snapshot_id=snapshot.snapshot_id,
            execution_scope=execution_scope,
            model_turn=1,
            model_call_id="model-call-usage",
        ),
        purpose=purpose,
        request_digest="1" * 64,
        requirements_digest="2" * 64,
        requirements_revision="requirements-r1",
        descriptor_set_revision="descriptor-set-r1",
        route_plan=plan,
        created_at=_NOW,
    )


def _usage(
    invocation: ModelInvocationPlannedRecord,
    *,
    ordinal: int,
    stream_id: str | None = None,
    provider_reported: bool = True,
    input_tokens: int = 10,
    output_tokens: int = 4,
    cost: int = 17,
    deployment_id: str = "deployment-primary",
) -> ModelAttemptUsageRecord:
    admission = ModelAttemptAdmissionRecord.create(
        invocation=invocation,
        decision=ModelAttemptDecision(
            kind=ModelAttemptDecisionKind.ADMIT,
            reason=(
                ModelAttemptDecisionReason.FIRST_ATTEMPT
                if ordinal == 1
                else ModelAttemptDecisionReason.SAFE_SAME_DEPLOYMENT_RETRY
            ),
            deployment_id=deployment_id,
            ordinal=ordinal,
        ),
        admission_ordinal=ordinal,
        prior_attempt_count=ordinal - 1,
        created_at=_NOW,
    )
    return ModelAttemptUsageRecord.create(
        invocation=invocation,
        admission=admission,
        usage=NormalizedTokenUsage(
            input_tokens=input_tokens if provider_reported else 0,
            output_tokens=output_tokens if provider_reported else 0,
        ),
        provider_reported=provider_reported,
        usage_record_id=stream_id,
        cost_microusd=cost if provider_reported else 0,
        duration_ms=20,
        created_at=_NOW,
    )


def _rows(*records: object) -> tuple[SequencedModelInvocationRecord, ...]:
    return tuple(
        SequencedModelInvocationRecord(sequence_no=index, record=record)
        for index, record in enumerate(records, start=1)
    )


def _journal_rows(
    invocation: ModelInvocationPlannedRecord,
    *records: object,
) -> tuple[SequencedModelInvocationRecord, ...]:
    primary_route = ModelRouteEligibleRecord.create(
        invocation=invocation,
        route=ModelRouteEntry(
            deployment_id="deployment-primary",
            descriptor_revision="descriptor-r1",
            endpoint_ref=f"endpoint_{'1' * 32}",
            provider="openai",
            model_name="gpt-5.4-mini",
            region="us-east",
            credential_mode=ModelCredentialMode.BYOK,
            price_revision="price-r1",
            max_input_tokens=200_000,
            max_output_tokens=16_000,
        ),
        route_ordinal=1,
        created_at=_NOW,
    )
    fallback_route = ModelRouteEligibleRecord.create(
        invocation=invocation,
        route=ModelRouteEntry(
            deployment_id="deployment-fallback",
            descriptor_revision="descriptor-r2",
            endpoint_ref=f"endpoint_{'2' * 32}",
            provider="anthropic",
            model_name="claude-sonnet",
            region="us-east",
            credential_mode=ModelCredentialMode.BYOK,
            price_revision="price-r2",
            max_input_tokens=200_000,
            max_output_tokens=16_000,
        ),
        route_ordinal=2,
        created_at=_NOW,
    )
    return _rows(invocation, primary_route, fallback_route, *records)


def test_reconciles_success_and_retried_failed_attempts_exactly_once() -> None:
    invocation = _invocation()
    first = _usage(invocation, ordinal=1, input_tokens=3, output_tokens=2, cost=5)
    retry = _usage(invocation, ordinal=2, input_tokens=7, output_tokens=4, cost=11)
    reconciler = ModelInvocationUsageReconciler()

    result = reconciler.reconcile(
        run=_run(),
        records=_journal_rows(invocation, first, retry, first),
        pricing_at=_NOW,
    )

    assert [row.id for row in result.records] == [first.attempt_id, retry.attempt_id]
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 6
    assert result.cost_micro_usd == 16
    # Fresh process replay is safe when durable rows are read back first.
    restarted = reconciler.reconcile(
        run=_run(),
        records=_journal_rows(invocation, first, retry),
        pricing_at=_NOW,
        already_materialized_ids=frozenset(
            {first.attempt_id or "", retry.attempt_id or ""}
        ),
    )
    assert restarted.records == ()
    assert restarted.usage.total_tokens == 16
    assert restarted.cost_micro_usd == 16


def test_prior_stream_row_suppresses_reinsertion_not_restart_aggregate() -> None:
    """A prior process's stream row is not a live metrics contribution."""

    invocation = _invocation()
    streamed = _usage(
        invocation,
        ordinal=1,
        stream_id="message-from-prior-worker",
        input_tokens=10,
        output_tokens=4,
        cost=23,
    )

    restarted = ModelInvocationUsageReconciler().reconcile(
        run=_run(),
        records=_journal_rows(invocation, streamed),
        pricing_at=_NOW,
        already_materialized_usage_ids=frozenset({"message-from-prior-worker"}),
    )

    assert restarted.records == ()
    assert restarted.usage.total_tokens == 14
    assert restarted.cost_micro_usd == 23


def test_streamed_terminal_usage_is_a_dedupe_witness_not_a_second_charge() -> None:
    invocation = _invocation()
    streamed = _usage(invocation, ordinal=1, stream_id="message-terminal", cost=23)
    result = ModelInvocationUsageReconciler().reconcile(
        run=_run(),
        records=_journal_rows(invocation, streamed),
        pricing_at=_NOW,
        streamed_usage_ids=frozenset({"message-terminal"}),
    )

    assert result.records == ()
    assert result.usage.total_tokens == 0
    assert result.cost_micro_usd == 23
    assert result.streamed_attempt_ids == frozenset({streamed.attempt_id})


def test_missing_provider_usage_is_explicit_zero_and_never_inferred() -> None:
    invocation = _invocation()
    unreported = _usage(invocation, ordinal=1, provider_reported=False)
    result = ModelInvocationUsageReconciler().reconcile(
        run=_run(), records=_journal_rows(invocation, unreported), pricing_at=_NOW
    )

    assert result.records == ()
    assert result.finalized_attempt_ids == frozenset({unreported.attempt_id})
    assert result.cost_micro_usd == 0


def test_reconciled_attempts_feed_existing_run_metrics_and_cost() -> None:
    invocation = _invocation()
    usage = _usage(invocation, ordinal=1, input_tokens=9, output_tokens=3, cost=31)
    result = ModelInvocationUsageReconciler().reconcile(
        run=_run(), records=_journal_rows(invocation, usage), pricing_at=_NOW
    )
    metrics = AssistantRunMetrics(started_at=_NOW, provider="openai")
    metrics.record_model_invocation_usage(result.usage, cost_micro_usd=0)
    metrics.set_model_invocation_cost_micro_usd(result.cost_micro_usd)
    run_usage = metrics.to_usage_record(_run(), completed_at=_NOW, status="completed")

    assert run_usage.total_tokens == 12
    assert run_usage.cost_micro_usd == 31


def test_streamed_success_and_failed_billed_retry_are_combined_once() -> None:
    invocation = _invocation()
    streamed_success = _usage(
        invocation,
        ordinal=1,
        stream_id="message-success",
        input_tokens=10,
        output_tokens=4,
        cost=23,
    )
    billed_failure = _usage(
        invocation,
        ordinal=2,
        input_tokens=7,
        output_tokens=3,
        cost=19,
    )
    result = ModelInvocationUsageReconciler().reconcile(
        run=_run(),
        records=_journal_rows(invocation, streamed_success, billed_failure),
        pricing_at=_NOW,
        streamed_usage_ids=frozenset({"message-success"}),
    )
    metrics = AssistantRunMetrics(started_at=_NOW, provider="openai")
    # The regular streaming accumulator already owns the success.
    metrics.record_model_invocation_usage(
        NormalizedTokenUsage(input_tokens=10, output_tokens=4), cost_micro_usd=0
    )
    # The journal-only delta owns the failed accepted retry.
    metrics.record_model_invocation_usage(result.usage, cost_micro_usd=0)
    metrics.set_model_invocation_cost_micro_usd(result.cost_micro_usd)
    rollup = metrics.to_usage_record(_run(), completed_at=_NOW, status="completed")

    assert result.usage.total_tokens == 10
    assert rollup.total_tokens == 24
    assert rollup.cost_micro_usd == 42


def test_cross_model_subagent_fallback_uses_journal_route_and_scope_attribution() -> (
    None
):
    invocation = _invocation(
        execution_scope="subagent:task-17", purpose=Purpose.SUBAGENT_WORK
    )
    fallback = _usage(
        invocation, ordinal=1, deployment_id="deployment-fallback", cost=29
    )
    result = ModelInvocationUsageReconciler().reconcile(
        run=_run(), records=_journal_rows(invocation, fallback), pricing_at=_NOW
    )

    row = result.records[0]
    assert (row.model_provider, row.model_name) == ("anthropic", "claude-sonnet")
    assert row.purpose == Purpose.SUBAGENT_WORK.value
    assert (row.task_id, row.subagent_id) == ("task-17", "task-17")


async def test_projection_coordinator_replays_once_then_seals_at_outer_terminal() -> (
    None
):
    invocation = _invocation()
    usage = _usage(invocation, ordinal=1)
    rows = _journal_rows(invocation, usage)

    class _Journal:
        async def list_for_run(
            self, **kwargs: object
        ) -> tuple[SequencedModelInvocationRecord, ...]:
            return rows if kwargs["after_sequence"] == 0 else ()

    coordinator = ModelInvocationMetricsProjectionCoordinator(
        journal=_Journal()  # type: ignore[arg-type]
    )
    assert (
        await coordinator.replay(
            org_id=_run().org_id,
            run_id=_run().run_id,
            subject_fingerprint="a" * 64,
        )
        is None
    )
    checkpoint = await coordinator.replay(
        org_id=_run().org_id,
        run_id=_run().run_id,
        subject_fingerprint="a" * 64,
        outer_run_terminal=True,
    )

    assert checkpoint is not None
    assert checkpoint.after_sequence == rows[-1].sequence_no
    assert checkpoint.projected_records == len(rows)


async def test_terminal_integration_merges_stream_dedupe_and_billed_retry_before_run_usage() -> (
    None
):
    """The terminal worker seam owns only the journal delta and canonical cost."""

    invocation = _invocation()
    streamed = _usage(
        invocation,
        ordinal=1,
        stream_id="message-success",
        input_tokens=10,
        output_tokens=4,
        cost=23,
    )
    failed_retry = _usage(
        invocation,
        ordinal=2,
        input_tokens=7,
        output_tokens=3,
        cost=19,
    )
    rows = _journal_rows(invocation, streamed, failed_retry)

    class _Journal:
        async def list_for_run(
            self, **kwargs: object
        ) -> tuple[SequencedModelInvocationRecord, ...]:
            after = kwargs.get("after_sequence", 0)
            return tuple(row for row in rows if row.sequence_no > after)

    recorder = InMemoryUsageRecorder()
    metrics = AssistantRunMetrics(started_at=_NOW, provider="openai")
    metrics.per_call.observe(
        NormalizedTokenUsage(input_tokens=10, output_tokens=4),
        message_id="message-success",
    )
    metrics.per_call.mark_completed("message-success", completed_at=_NOW)
    metrics.usage = NormalizedTokenUsage(input_tokens=10, output_tokens=4)
    integration = ModelInvocationTerminalIntegration(
        journal=_Journal(),  # type: ignore[arg-type]
        usage_recorder=recorder,
    )

    await integration.finalize(
        run=_run(),
        metrics=metrics,
        subject_fingerprint="a" * 64,
        completed_at=_NOW,
    )
    cost = await integration.record_run_usage(
        run=_run(),
        metrics=metrics,
        completed_at=_NOW,
        status="completed",
    )

    assert [row.id for row in recorder.calls] == [failed_retry.attempt_id]
    assert recorder.runs[0].total_tokens == 24
    assert recorder.runs[0].cost_micro_usd == 42
    assert cost == 42


async def test_restart_rebuilds_missing_run_row_without_reemitting_attempt_row() -> (
    None
):
    """A durable F10 call row cannot erase the journal aggregate on restart."""

    invocation = _invocation()
    attempt = _usage(invocation, ordinal=1, input_tokens=7, output_tokens=3, cost=19)
    rows = _journal_rows(invocation, attempt)

    class _Journal:
        async def list_for_run(
            self, **kwargs: object
        ) -> tuple[SequencedModelInvocationRecord, ...]:
            after = kwargs.get("after_sequence", 0)
            return tuple(row for row in rows if row.sequence_no > after)

    store = InMemoryRuntimeApiStore()
    existing = (
        ModelInvocationUsageReconciler()
        .reconcile(run=_run(), records=rows, pricing_at=_NOW)
        .records[0]
    )
    await store.record_model_call_usage(existing)
    recorder = _PersistentRecorder(store)
    metrics = AssistantRunMetrics(started_at=_NOW, provider="openai")
    integration = ModelInvocationTerminalIntegration(
        journal=_Journal(),  # type: ignore[arg-type]
        usage_recorder=recorder,  # type: ignore[arg-type]
        persistence=store,
    )

    await integration.finalize(
        run=_run(),
        metrics=metrics,
        subject_fingerprint="a" * 64,
        completed_at=_NOW,
    )
    await integration.record_run_usage(
        run=_run(), metrics=metrics, completed_at=_NOW, status="completed"
    )

    assert recorder.calls == []
    assert metrics.usage.total_tokens == 10
    assert metrics.model_invocation_cost_micro_usd == 19
    assert store.run_usage[_run().run_id].total_tokens == 10
    assert store.run_usage[_run().run_id].cost_micro_usd == 19


async def test_restart_rebuilds_missing_run_row_from_prior_stream_row() -> None:
    """A persisted stream message ID suppresses only duplicate call insertion."""

    invocation = _invocation()
    streamed = _usage(
        invocation,
        ordinal=1,
        stream_id="message-prior-stream",
        input_tokens=10,
        output_tokens=4,
        cost=23,
    )
    rows = _journal_rows(invocation, streamed)

    class _Journal:
        async def list_for_run(
            self, **kwargs: object
        ) -> tuple[SequencedModelInvocationRecord, ...]:
            after = kwargs.get("after_sequence", 0)
            return tuple(row for row in rows if row.sequence_no > after)

    store = InMemoryRuntimeApiStore()
    prior = (
        ModelInvocationUsageReconciler()
        .reconcile(run=_run(), records=rows, pricing_at=_NOW)
        .records[0]
        .model_copy(update={"id": "message-prior-stream"})
    )
    await store.record_model_call_usage(prior)
    recorder = _PersistentRecorder(store)
    metrics = AssistantRunMetrics(started_at=_NOW, provider="openai")
    integration = ModelInvocationTerminalIntegration(
        journal=_Journal(),  # type: ignore[arg-type]
        usage_recorder=recorder,  # type: ignore[arg-type]
        persistence=store,
    )

    await integration.finalize(
        run=_run(),
        metrics=metrics,
        subject_fingerprint="a" * 64,
        completed_at=_NOW,
    )
    await integration.record_run_usage(
        run=_run(), metrics=metrics, completed_at=_NOW, status="completed"
    )

    assert recorder.calls == []
    assert metrics.usage.total_tokens == 14
    assert store.run_usage[_run().run_id].total_tokens == 14


async def test_existing_run_row_stays_idempotent_after_restart_reconciliation() -> None:
    """Replaying the journal cannot replace a durable aggregate or emit calls."""

    invocation = _invocation()
    attempt = _usage(invocation, ordinal=1, input_tokens=7, output_tokens=3, cost=19)
    rows = _journal_rows(invocation, attempt)

    class _Journal:
        async def list_for_run(
            self, **kwargs: object
        ) -> tuple[SequencedModelInvocationRecord, ...]:
            return rows

    store = InMemoryRuntimeApiStore()
    call = (
        ModelInvocationUsageReconciler()
        .reconcile(run=_run(), records=rows, pricing_at=_NOW)
        .records[0]
    )
    await store.record_model_call_usage(call)
    original_metrics = AssistantRunMetrics(started_at=_NOW, provider="openai")
    original_metrics.record_model_invocation_usage(
        NormalizedTokenUsage(input_tokens=7, output_tokens=3), cost_micro_usd=0
    )
    original_metrics.set_model_invocation_cost_micro_usd(19)
    await store.record_run_usage(
        original_metrics.to_usage_record(_run(), completed_at=_NOW, status="completed")
    )
    recorder = _PersistentRecorder(store)
    metrics = AssistantRunMetrics(started_at=_NOW, provider="openai")
    integration = ModelInvocationTerminalIntegration(
        journal=_Journal(),  # type: ignore[arg-type]
        usage_recorder=recorder,  # type: ignore[arg-type]
        persistence=store,
    )

    await integration.finalize(
        run=_run(),
        metrics=metrics,
        subject_fingerprint="a" * 64,
        completed_at=_NOW,
    )
    await integration.record_run_usage(
        run=_run(), metrics=metrics, completed_at=_NOW, status="completed"
    )

    assert recorder.calls == []
    assert len(store.run_usage) == 1
    assert store.run_usage[_run().run_id].total_tokens == 10


async def test_terminal_integration_without_f10_journal_preserves_legacy_metrics() -> (
    None
):
    """F10 off does not add a call row or alter the existing run aggregate."""

    recorder = InMemoryUsageRecorder()
    metrics = AssistantRunMetrics(started_at=_NOW, provider="openai")
    metrics.record_model_invocation_usage(
        NormalizedTokenUsage(input_tokens=3, output_tokens=2), cost_micro_usd=7
    )
    integration = ModelInvocationTerminalIntegration(
        journal=None,
        usage_recorder=recorder,
    )

    await integration.finalize(
        run=_run(),
        metrics=metrics,
        subject_fingerprint=None,
        completed_at=_NOW,
    )
    await integration.record_run_usage(
        run=_run(),
        metrics=metrics,
        completed_at=_NOW,
        status="completed",
    )

    assert recorder.calls == []
    assert recorder.runs[0].total_tokens == 5
    assert recorder.runs[0].cost_micro_usd == 7
