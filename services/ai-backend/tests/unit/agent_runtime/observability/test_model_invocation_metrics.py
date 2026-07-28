"""F10 operational metrics are replay-safe and label-bounded."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

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
    ModelAttemptFailedRecord,
    ModelAttemptLifecycleState,
    ModelAttemptStateRecord,
    ModelCredentialMode,
    ModelDispatchState,
    ModelFailureClass,
    ModelFallbackPolicy,
    ModelInvocationBudget,
    ModelInvocationCompletedRecord,
    ModelInvocationFailedRecord,
    ModelInvocationFailureReason,
    ModelInvocationPlannedRecord,
    ModelInvocationRecoveryRecord,
    ModelRecoveryKind,
    ModelRecoveryOutcome,
    ModelRouteEntry,
    ModelRouteExclusion,
    ModelRouteExclusionReason,
    ModelRouteExcludedRecord,
    ModelRoutePlan,
    ModelStreamState,
    SequencedModelInvocationRecord,
)
from agent_runtime.execution.model_invocation.journal import (
    ModelAttemptUsageRecord,
)
from agent_runtime.observability.attribution import Purpose
from agent_runtime.observability.model_invocation_metrics import (
    MODEL_INVOCATION_METRIC_DEFINITIONS,
    ModelInvocationMetricFact,
    ModelInvocationMetricName,
    ModelInvocationMetricsProjectionError,
    ModelInvocationMetricsProjector,
    OpenTelemetryModelInvocationMetrics,
)
from agent_runtime.observability.token_usage import NormalizedTokenUsage


_CREATED_AT = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


class _Facts:
    def __init__(self) -> None:
        self.items: list[ModelInvocationMetricFact] = []

    def record(self, fact: ModelInvocationMetricFact) -> None:
        self.items.append(fact)

    def named(self, name: ModelInvocationMetricName) -> list[ModelInvocationMetricFact]:
        return [item for item in self.items if item.name is name]


def _snapshot() -> RunControlSnapshot:
    budget = BudgetEnvelope.create(
        budget_envelope_id="budget-f10-metrics",
        revision="budget-r1",
        max_model_turns=8,
        max_tool_calls=16,
    )
    return RunControlSnapshot.create(
        run_id="run-f10-metrics",
        conversation_id="conversation-f10-metrics",
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
        snapshot_id="snapshot-f10-metrics",
        created_at=_CREATED_AT,
    )


def _route(deployment_id: str) -> ModelRouteEntry:
    return ModelRouteEntry(
        deployment_id=deployment_id,
        descriptor_revision=f"descriptor-{deployment_id}",
        endpoint_ref=f"endpoint_{'1' * 32}",
        provider="openai",
        model_name="gpt-5.4-mini",
        region="us-east",
        credential_mode=ModelCredentialMode.BYOK,
        price_revision="price-r1",
        max_input_tokens=200_000,
        max_output_tokens=16_000,
    )


def _plan(
    *,
    routes: tuple[ModelRouteEntry, ...],
    exclusions: tuple[ModelRouteExclusion, ...] = (),
    fallback_policy: ModelFallbackPolicy = ModelFallbackPolicy.NONE,
) -> ModelRoutePlan:
    return ModelRoutePlan.create(
        routes=routes,
        exclusions=exclusions,
        fallback_policy=fallback_policy,
        budget=ModelInvocationBudget(
            max_attempts=2,
            max_same_deployment_attempts=2,
            max_cost_microusd=20_000,
            max_input_tokens=20_000,
            max_output_tokens=4_000,
        ),
    )


def _invocation(
    snapshot: RunControlSnapshot,
    *,
    turn: int,
    plan: ModelRoutePlan,
    request_digest: str | None = None,
) -> ModelInvocationPlannedRecord:
    return ModelInvocationPlannedRecord.create(
        binding=RunControlBinding(
            snapshot=snapshot,
            effective_modes=snapshot.feature_modes,
            decisions=(),
        ),
        identity=RuntimeModelCallIdentity(
            run_id=snapshot.run_id,
            snapshot_id=snapshot.snapshot_id,
            execution_scope="supervisor",
            model_turn=turn,
            model_call_id=f"model-call:f10-metrics-{turn}",
        ),
        purpose=Purpose.MAIN,
        request_digest=request_digest or f"{turn}" * 64,
        requirements_digest="2" * 64,
        requirements_revision="requirements-r1",
        descriptor_set_revision="descriptor-set-r1",
        route_plan=plan,
        created_at=_CREATED_AT,
    )


def _admission(
    invocation: ModelInvocationPlannedRecord,
    *,
    ordinal: int,
    deployment_id: str,
    reason: ModelAttemptDecisionReason,
) -> ModelAttemptAdmissionRecord:
    return ModelAttemptAdmissionRecord.create(
        invocation=invocation,
        decision=ModelAttemptDecision(
            kind=ModelAttemptDecisionKind.ADMIT,
            reason=reason,
            deployment_id=deployment_id,
            ordinal=ordinal,
        ),
        admission_ordinal=ordinal,
        prior_attempt_count=ordinal - 1,
        created_at=_CREATED_AT,
    )


def _rows(*records: object) -> tuple[SequencedModelInvocationRecord, ...]:
    return tuple(
        SequencedModelInvocationRecord(sequence_no=index, record=record)
        for index, record in enumerate(records, start=1)
    )


def test_overlapping_replay_projects_each_journal_fact_exactly_once() -> None:
    snapshot = _snapshot()
    plan = _plan(
        routes=(_route("deployment-primary"), _route("deployment-fallback")),
        exclusions=(
            ModelRouteExclusion(
                deployment_id="wrong-region",
                reasons=(ModelRouteExclusionReason.REGION_MISMATCH,),
            ),
            ModelRouteExclusion(
                deployment_id="wrong-privacy",
                reasons=(ModelRouteExclusionReason.PRIVACY_INCOMPATIBLE,),
            ),
            ModelRouteExclusion(
                deployment_id="wrong-key-mode",
                reasons=(ModelRouteExclusionReason.BYOK_REQUIRED,),
            ),
        ),
        fallback_policy=ModelFallbackPolicy.SAME_MODEL,
    )
    invocation = _invocation(snapshot, turn=1, plan=plan)
    first = _admission(
        invocation,
        ordinal=1,
        deployment_id="deployment-primary",
        reason=ModelAttemptDecisionReason.FIRST_ATTEMPT,
    )
    first_usage = ModelAttemptUsageRecord.create(
        invocation=invocation,
        admission=first,
        usage=NormalizedTokenUsage(),
        provider_reported=False,
        duration_ms=400,
        created_at=_CREATED_AT,
    )
    failed = ModelAttemptFailedRecord.create(
        invocation=invocation,
        admission=first,
        failure_class=ModelFailureClass.PROVIDER_OVERLOADED,
        dispatch_state=ModelDispatchState.NOT_ACCEPTED,
        stream_state=ModelStreamState.NOT_STARTED,
        provider_failure_observed=True,
        created_at=_CREATED_AT,
    )
    recovery = ModelInvocationRecoveryRecord.create(
        invocation=invocation,
        source_attempt_id=first.attempt_id or "",
        recovery_ordinal=1,
        kind=ModelRecoveryKind.ALTERNATE_ROUTE,
        outcome=ModelRecoveryOutcome.ADMITTED,
        decision_reason=ModelAttemptDecisionReason.SAFE_ALTERNATE_ROUTE,
        target_attempt_ordinal=2,
        created_at=_CREATED_AT,
    )
    second = _admission(
        invocation,
        ordinal=2,
        deployment_id="deployment-fallback",
        reason=ModelAttemptDecisionReason.SAFE_ALTERNATE_ROUTE,
    )
    second_usage = ModelAttemptUsageRecord.create(
        invocation=invocation,
        admission=second,
        usage=NormalizedTokenUsage(
            input_tokens=1_000,
            output_tokens=200,
            cached_input_tokens=300,
            reasoning_tokens=50,
        ),
        provider_reported=True,
        cost_microusd=700,
        duration_ms=1_600,
        created_at=_CREATED_AT,
    )
    terminal = ModelInvocationCompletedRecord.create(
        invocation=invocation,
        terminal_attempt_id=second.attempt_id or "",
        attempt_count=2,
        total_input_tokens=1_000,
        total_output_tokens=200,
        total_cost_microusd=700,
        total_duration_ms=2_500,
        created_at=_CREATED_AT,
    )
    exclusions = tuple(
        ModelRouteExcludedRecord.create(
            invocation=invocation,
            exclusion=exclusion,
            created_at=_CREATED_AT,
        )
        for exclusion in plan.exclusions
    )
    replay = _rows(
        invocation,
        *exclusions,
        first,
        first_usage,
        failed,
        recovery,
        second,
        second_usage,
        terminal,
    )
    sink = _Facts()
    projector = ModelInvocationMetricsProjector(metrics=sink)

    projector.project(replay)
    first_pass = tuple(sink.items)
    projector.project(replay)

    assert tuple(sink.items) == first_pass
    assert len(sink.named(ModelInvocationMetricName.ROUTE_PLANS_TOTAL)) == 1
    assert len(sink.named(ModelInvocationMetricName.ROUTE_EXCLUSIONS_TOTAL)) == 3
    policy_dimensions = {
        fact.otel_attributes["dimension"]
        for fact in sink.named(ModelInvocationMetricName.POLICY_EXCLUSIONS_TOTAL)
    }
    assert policy_dimensions == {"region", "privacy", "byok"}
    assert [
        fact.otel_attributes["attempt_kind"]
        for fact in sink.named(ModelInvocationMetricName.ATTEMPTS_TOTAL)
    ] == ["primary", "fallback"]
    assert len(sink.named(ModelInvocationMetricName.ATTEMPT_LATENCY_SECONDS)) == 2
    assert (
        sink.named(ModelInvocationMetricName.FALLBACK_LATENCY_SECONDS)[0].value == 2.5
    )
    assert (
        sink.named(ModelInvocationMetricName.REPORTED_COST_MICROUSD_TOTAL)[0].value
        == 700
    )
    assert not sink.named(ModelInvocationMetricName.MISSING_FINALIZATION_TOTAL)


def test_missing_finalization_audit_is_repeatable_without_double_counting() -> None:
    snapshot = _snapshot()
    plan = _plan(routes=(_route("deployment-primary"),))
    invocation = _invocation(snapshot, turn=1, plan=plan)
    admission = _admission(
        invocation,
        ordinal=1,
        deployment_id="deployment-primary",
        reason=ModelAttemptDecisionReason.FIRST_ATTEMPT,
    )
    sink = _Facts()
    projector = ModelInvocationMetricsProjector(metrics=sink)

    projector.project(
        _rows(invocation, admission),
        detect_missing_finalization=True,
    )
    projector.project(
        _rows(invocation, admission),
        detect_missing_finalization=True,
    )

    missing = sink.named(ModelInvocationMetricName.MISSING_FINALIZATION_TOTAL)
    assert len(missing) == 1
    assert missing[0].otel_attributes == {"attempt_kind": "primary"}


def test_circuit_projection_requires_open_probe_and_success_facts() -> None:
    snapshot = _snapshot()
    excluded_plan = _plan(
        routes=(),
        exclusions=(
            ModelRouteExclusion(
                deployment_id="deployment-probe",
                reasons=(ModelRouteExclusionReason.OPEN_CIRCUIT,),
            ),
        ),
    )
    excluded_invocation = _invocation(snapshot, turn=1, plan=excluded_plan)
    exclusion = ModelRouteExcludedRecord.create(
        invocation=excluded_invocation,
        exclusion=excluded_plan.exclusions[0],
        created_at=_CREATED_AT,
    )
    probe_plan = _plan(routes=(_route("deployment-probe"),))
    probe_invocation = _invocation(snapshot, turn=2, plan=probe_plan)
    probe = _admission(
        probe_invocation,
        ordinal=1,
        deployment_id="deployment-probe",
        reason=ModelAttemptDecisionReason.FIRST_ATTEMPT,
    )
    usage = ModelAttemptUsageRecord.create(
        invocation=probe_invocation,
        admission=probe,
        usage=NormalizedTokenUsage(input_tokens=10, output_tokens=5),
        provider_reported=True,
        duration_ms=100,
        created_at=_CREATED_AT,
    )
    terminal = ModelInvocationCompletedRecord.create(
        invocation=probe_invocation,
        terminal_attempt_id=probe.attempt_id or "",
        attempt_count=1,
        total_input_tokens=10,
        total_output_tokens=5,
        total_duration_ms=100,
        created_at=_CREATED_AT,
    )
    sink = _Facts()

    ModelInvocationMetricsProjector(metrics=sink).project(
        _rows(excluded_invocation, exclusion, probe_invocation, probe, usage, terminal)
    )

    assert [
        fact.otel_attributes["event"]
        for fact in sink.named(ModelInvocationMetricName.CIRCUIT_EVENTS_TOTAL)
    ] == ["opened", "probed", "recovered"]


def test_retry_and_ambiguous_records_use_closed_operational_outcomes() -> None:
    snapshot = _snapshot()
    plan = _plan(routes=(_route("deployment-primary"),))
    invocation = _invocation(snapshot, turn=1, plan=plan)
    first = _admission(
        invocation,
        ordinal=1,
        deployment_id="deployment-primary",
        reason=ModelAttemptDecisionReason.FIRST_ATTEMPT,
    )
    first_usage = ModelAttemptUsageRecord.create(
        invocation=invocation,
        admission=first,
        usage=NormalizedTokenUsage(),
        provider_reported=False,
        duration_ms=100,
        created_at=_CREATED_AT,
    )
    ambiguous_state = ModelAttemptStateRecord.create(
        invocation=invocation,
        admission=first,
        state=ModelAttemptLifecycleState.AMBIGUOUS,
        dispatch_state=ModelDispatchState.UNKNOWN,
        stream_state=ModelStreamState.NOT_STARTED,
        elapsed_ms=100,
        created_at=_CREATED_AT,
    )
    recovery = ModelInvocationRecoveryRecord.create(
        invocation=invocation,
        source_attempt_id=first.attempt_id or "",
        recovery_ordinal=1,
        kind=ModelRecoveryKind.CRASH_RECONCILIATION,
        outcome=ModelRecoveryOutcome.AMBIGUOUS,
        created_at=_CREATED_AT,
    )
    terminal = ModelInvocationFailedRecord.create(
        invocation=invocation,
        attempt_count=1,
        reason=ModelInvocationFailureReason.AMBIGUOUS_RECOVERY,
        terminal_attempt_id=first.attempt_id,
        failure_class=ModelFailureClass.AMBIGUOUS_PROVIDER_STATE,
        total_duration_ms=100,
        created_at=_CREATED_AT,
    )
    retry_invocation = _invocation(snapshot, turn=2, plan=plan)
    retry_first = _admission(
        retry_invocation,
        ordinal=1,
        deployment_id="deployment-primary",
        reason=ModelAttemptDecisionReason.FIRST_ATTEMPT,
    )
    retry_first_usage = ModelAttemptUsageRecord.create(
        invocation=retry_invocation,
        admission=retry_first,
        usage=NormalizedTokenUsage(),
        provider_reported=False,
        duration_ms=50,
        created_at=_CREATED_AT,
    )
    retry_first_failure = ModelAttemptFailedRecord.create(
        invocation=retry_invocation,
        admission=retry_first,
        failure_class=ModelFailureClass.PRE_DISPATCH_TRANSIENT,
        dispatch_state=ModelDispatchState.NOT_ACCEPTED,
        stream_state=ModelStreamState.NOT_STARTED,
        provider_failure_observed=True,
        created_at=_CREATED_AT,
    )
    retry_recovery = ModelInvocationRecoveryRecord.create(
        invocation=retry_invocation,
        source_attempt_id=retry_first.attempt_id or "",
        recovery_ordinal=1,
        kind=ModelRecoveryKind.SAME_DEPLOYMENT_RETRY,
        outcome=ModelRecoveryOutcome.ADMITTED,
        decision_reason=ModelAttemptDecisionReason.SAFE_SAME_DEPLOYMENT_RETRY,
        target_attempt_ordinal=2,
        created_at=_CREATED_AT,
    )
    retry = _admission(
        retry_invocation,
        ordinal=2,
        deployment_id="deployment-primary",
        reason=ModelAttemptDecisionReason.SAFE_SAME_DEPLOYMENT_RETRY,
    )
    sink = _Facts()

    ModelInvocationMetricsProjector(metrics=sink).project(
        _rows(
            invocation,
            first,
            first_usage,
            ambiguous_state,
            recovery,
            terminal,
            retry_invocation,
            retry_first,
            retry_first_usage,
            retry_first_failure,
            retry_recovery,
            retry,
        )
    )

    assert (
        sink.named(ModelInvocationMetricName.ATTEMPTS_TOTAL)[-1].otel_attributes[
            "attempt_kind"
        ]
        == "retry"
    )
    assert sink.named(ModelInvocationMetricName.TERMINAL_TOTAL)[0].otel_attributes == {
        "outcome": "ambiguous",
        "reason": "ambiguous_recovery",
    }
    assert {
        fact.otel_attributes["source"]
        for fact in sink.named(ModelInvocationMetricName.AMBIGUOUS_TOTAL)
    } == {"attempt_state", "recovery", "terminal"}


def test_metric_registry_rejects_identifier_and_unbounded_label_values() -> None:
    with pytest.raises(ValidationError, match="fixed registry"):
        ModelInvocationMetricFact(
            name=ModelInvocationMetricName.TERMINAL_TOTAL,
            value=1,
            attributes=(
                ("outcome", "failed"),
                ("run_id", "run-private"),
            ),
        )
    with pytest.raises(ValidationError, match="outside the fixed registry"):
        ModelInvocationMetricFact(
            name=ModelInvocationMetricName.TERMINAL_TOTAL,
            value=1,
            attributes=(
                ("outcome", "customer-specific-error"),
                ("reason", "none"),
            ),
        )

    forbidden_labels = {
        "run_id",
        "user_id",
        "model_call_id",
        "invocation_id",
        "attempt_id",
        "prompt",
        "output",
        "error",
        "deployment_id",
        "provider",
        "model",
        "region",
    }
    for definition in MODEL_INVOCATION_METRIC_DEFINITIONS.values():
        names = {name for name, _values in definition.labels}
        assert names.isdisjoint(forbidden_labels)
        assert all(
            len(value) <= 64
            for _name, allowed in definition.labels
            for value in allowed
        )


def test_otel_facade_publishes_only_prevalidated_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from opentelemetry import metrics

    calls: list[tuple[str, float, dict[str, str]]] = []

    class _Instrument:
        def __init__(self, name: str) -> None:
            self._name = name

        def add(self, value: float, attributes: dict[str, str]) -> None:
            calls.append((self._name, value, attributes))

        def record(self, value: float, attributes: dict[str, str]) -> None:
            calls.append((self._name, value, attributes))

    class _Meter:
        def create_counter(self, name: str, **_kwargs: object) -> _Instrument:
            return _Instrument(name)

        def create_histogram(self, name: str, **_kwargs: object) -> _Instrument:
            return _Instrument(name)

    monkeypatch.setattr(metrics, "get_meter", lambda _name: _Meter())
    facade = OpenTelemetryModelInvocationMetrics()
    facade.record(
        ModelInvocationMetricFact(
            name=ModelInvocationMetricName.TERMINAL_TOTAL,
            value=1,
            attributes=(("outcome", "completed"), ("reason", "none")),
        )
    )
    facade.record(
        ModelInvocationMetricFact(
            name=ModelInvocationMetricName.ATTEMPT_LATENCY_SECONDS,
            value=0.25,
            attributes=(
                ("attempt_kind", "primary"),
                ("usage_source", "provider_reported"),
            ),
        )
    )

    assert calls == [
        (
            "model_invocation_terminal_total",
            1,
            {"outcome": "completed", "reason": "none"},
        ),
        (
            "model_invocation_attempt_latency_seconds",
            0.25,
            {"attempt_kind": "primary", "usage_source": "provider_reported"},
        ),
    ]


def test_projector_fails_before_eviction_or_conflicting_replay() -> None:
    snapshot = _snapshot()
    first = _invocation(
        snapshot,
        turn=1,
        plan=_plan(routes=(_route("deployment-primary"),)),
    )
    second = _invocation(
        snapshot,
        turn=2,
        plan=_plan(routes=(_route("deployment-primary"),)),
    )
    projector = ModelInvocationMetricsProjector(metrics=_Facts(), max_records=1)
    projector.project(_rows(first))

    with pytest.raises(ModelInvocationMetricsProjectionError, match="record bound"):
        projector.project(
            (SequencedModelInvocationRecord(sequence_no=2, record=second),)
        )

    conflicting = _invocation(
        snapshot,
        turn=1,
        plan=_plan(routes=(_route("deployment-primary"),)),
        request_digest="f" * 64,
    )
    with pytest.raises(
        ModelInvocationMetricsProjectionError,
        match="conflicts with prior replay",
    ):
        projector.project(
            (SequencedModelInvocationRecord(sequence_no=1, record=conflicting),)
        )


def test_terminal_seal_is_the_explicit_desktop_rotation_boundary() -> None:
    snapshot = _snapshot()
    invocation = _invocation(
        snapshot,
        turn=1,
        plan=_plan(routes=(_route("deployment-primary"),)),
    )
    projector = ModelInvocationMetricsProjector(metrics=_Facts())
    projector.project(_rows(invocation))

    checkpoint = projector.seal_terminal_replay()

    assert checkpoint.run_id == snapshot.run_id
    assert checkpoint.after_sequence == 1
    assert checkpoint.projected_records == 1
    with pytest.raises(ModelInvocationMetricsProjectionError, match="sealed"):
        projector.project(_rows(invocation))
