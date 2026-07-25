"""Adversarial and golden coverage for E2 D2's shadow-only comparator."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from agent_runtime.capabilities.operations.context import OperationContext
from agent_runtime.capabilities.operations.probes import OperationShadowProbe
from agent_runtime.rollout import (
    E2RolloutResolution,
    E2RolloutSettings,
    LegacyRolloutInputs,
    RolloutCapability,
    RolloutMode,
)
from agent_runtime.rollout_shadow import (
    ProtectedShadowDiagnostic,
    ShadowComparisonContext,
    ShadowComparisonKind,
    ShadowComparisonOutcome,
    ShadowComparisonService,
)
from agent_runtime.rollout_shadow_adapters import (
    ShadowProjectionComparators,
    ShadowRunProjectionObserver,
)
from agent_runtime.persistence.records import RuntimeModelCallUsageRecord
from agent_runtime.surfaces_v2.canonical_json import canonical_json_bytes, sha256_hex
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType
from copilot_service_contracts.work_ledger import load_ledger_golden_events
from tests.unit.agent_runtime.capabilities.operations.helpers import (
    BoundContextMixin,
    RecordingEmitter,
)


_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "e2_shadow_comparison_parity_vectors.json"
)


class RecordingShadowMetrics:
    def __init__(self) -> None:
        self.comparisons: list[tuple[str, str, str]] = []
        self.diagnostics: list[tuple[str, str]] = []

    def comparison(self, *, kind: object, capability: object, outcome: object) -> None:
        self.comparisons.append(
            (
                str(getattr(kind, "value", kind)),
                str(getattr(capability, "value", capability)),
                str(getattr(outcome, "value", outcome)),
            )
        )

    def diagnostic_sampled(self, *, kind: object, capability: object) -> None:
        self.diagnostics.append(
            (
                str(getattr(kind, "value", kind)),
                str(getattr(capability, "value", capability)),
            )
        )


class RecordingDiagnosticSink:
    def __init__(self) -> None:
        self.items: list[ProtectedShadowDiagnostic] = []

    def record(self, diagnostic: ProtectedShadowDiagnostic) -> None:
        self.items.append(diagnostic)


class FailingShadowMetrics:
    """Adversarial telemetry port: D2 must contain every failure from it."""

    def comparison(self, **_kwargs: object) -> None:
        raise RuntimeError("metrics unavailable")

    def diagnostic_sampled(self, **_kwargs: object) -> None:
        raise RuntimeError("metrics unavailable")


class FailingDiagnosticSink:
    """Adversarial protected sink: it must not affect comparison callers."""

    def record(self, _diagnostic: ProtectedShadowDiagnostic) -> None:
        raise RuntimeError("diagnostic storage unavailable")


class ReadOnlyEventStore:
    """A trap store: only the observer's two declared read methods work."""

    def __init__(self, events: Sequence[object], *, latest_sequence: int) -> None:
        self._events = tuple(events)
        self._latest_sequence = latest_sequence
        self.read_calls: list[str] = []
        self.side_effect_calls: list[str] = []

    async def get_latest_sequence(self, *, run_id: str) -> int:
        del run_id
        self.read_calls.append("get_latest_sequence")
        return self._latest_sequence

    async def list_events_after(
        self, *, org_id: str, run_id: str, after_sequence: int
    ) -> Sequence[object]:
        del org_id, run_id, after_sequence
        self.read_calls.append("list_events_after")
        return self._events

    async def append_event(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.side_effect_calls.append("append_event")
        raise AssertionError("shadow observer must not append events")

    async def enqueue_stage_commit(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.side_effect_calls.append("enqueue_stage_commit")
        raise AssertionError("shadow observer must not enqueue")

    async def apply(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.side_effect_calls.append("apply")
        raise AssertionError("shadow observer must not apply")


def _resolution(
    *shadowed: RolloutCapability,
    enforced: tuple[RolloutCapability, ...] = (),
) -> E2RolloutResolution:
    environment: dict[str, str] = {}
    for capability in shadowed:
        environment[E2RolloutSettings.environment_name(capability)] = "shadow"
    for capability in enforced:
        environment[E2RolloutSettings.environment_name(capability)] = "enforce"
    return E2RolloutResolution.resolve(
        environment=environment,
        legacy=LegacyRolloutInputs(surfaces_v2=False),
    )


def _service(
    *shadowed: RolloutCapability,
    metrics: RecordingShadowMetrics | None = None,
    diagnostics: RecordingDiagnosticSink | None = None,
    enforced: tuple[RolloutCapability, ...] = (),
) -> ShadowComparisonService:
    return ShadowComparisonService(
        resolution=_resolution(*shadowed, enforced=enforced),
        metrics_port=metrics or RecordingShadowMetrics(),
        diagnostic_sink=diagnostics or RecordingDiagnosticSink(),
        diagnostic_sampler=lambda _digest: True,
    )


def _golden_events() -> tuple[str, tuple[dict[str, object], ...]]:
    fixture = load_ledger_golden_events()
    run_id = fixture["run_id"]
    events = fixture["events"]
    assert isinstance(run_id, str)
    assert isinstance(events, list)
    return run_id, tuple(dict(event) for event in events if isinstance(event, Mapping))


class TestShadowComparisonService:
    def test_defaults_off_and_enforce_are_inert(self) -> None:
        metrics = RecordingShadowMetrics()
        enforce_only = E2RolloutResolution(
            modes=E2RolloutSettings(
                operation_gateway=RolloutMode.ENFORCE,
            )
        )
        for service in (
            _service(metrics=metrics),
            ShadowComparisonService(
                resolution=enforce_only,
                metrics_port=metrics,
                diagnostic_sink=RecordingDiagnosticSink(),
            ),
        ):
            result = service.compare(
                kind=ShadowComparisonKind.CLASSIFICATION,
                legacy={"effect_class": "none"},
                canonical={"effect_class": "external_destructive"},
                sample_key="run-private",
            )
            assert result.outcome is ShadowComparisonOutcome.DISABLED
        assert metrics.comparisons == []

    def test_shadow_metrics_are_closed_and_diagnostics_are_protected(self) -> None:
        metrics = RecordingShadowMetrics()
        diagnostics = RecordingDiagnosticSink()
        service = _service(
            RolloutCapability.OPERATION_GATEWAY,
            metrics=metrics,
            diagnostics=diagnostics,
        )
        secret = "/Users/alice/Top Secret/plan.md"

        result = service.compare(
            kind=ShadowComparisonKind.CLASSIFICATION,
            legacy={"effect_class": "none", "path": secret},
            canonical={"effect_class": "external_destructive", "path": secret},
            sample_key="run-alice-private",
        )

        assert result.outcome is ShadowComparisonOutcome.MISMATCH
        assert metrics.comparisons == [
            ("classification", "operation_gateway", "mismatch")
        ]
        assert metrics.diagnostics == [("classification", "operation_gateway")]
        assert len(diagnostics.items) == 1
        rendered = diagnostics.items[0].model_dump_json()
        assert secret not in rendered
        assert "alice" not in rendered
        assert diagnostics.items[0].sample_key_digest != "run-alice-private"

    def test_failing_shadow_telemetry_never_escapes_the_observer(self) -> None:
        service = ShadowComparisonService(
            resolution=_resolution(RolloutCapability.OPERATION_GATEWAY),
            metrics_port=FailingShadowMetrics(),
            diagnostic_sink=FailingDiagnosticSink(),
            diagnostic_sampler=lambda _digest: True,
        )

        result = service.compare(
            kind=ShadowComparisonKind.CLASSIFICATION,
            legacy={"effect_class": "none"},
            canonical={"effect_class": "external_destructive"},
            sample_key="run-private",
        )

        assert result.outcome is ShadowComparisonOutcome.MISMATCH

    def test_unbounded_or_unsupported_values_are_never_compared(self) -> None:
        service = _service(RolloutCapability.OPERATION_GATEWAY)
        deep: object = {"value": "x"}
        for _ in range(16):
            deep = {"nested": deep}

        result = service.compare(
            kind=ShadowComparisonKind.PROPOSAL_CANONICALIZATION,
            legacy=deep,
            canonical=deep,
            sample_key="run-1",
        )
        assert result.outcome in {
            ShadowComparisonOutcome.MATCH,
            ShadowComparisonOutcome.UNCOMPARABLE,
        }

    def test_checked_in_parity_vectors_have_expected_outcomes(self) -> None:
        fixture = json.loads(_FIXTURE_PATH.read_text())
        service = _service(*tuple(RolloutCapability))
        for vector in fixture["vectors"]:
            result = service.compare(
                kind=ShadowComparisonKind(vector["kind"]),
                legacy=vector["legacy"],
                canonical=vector["canonical"],
                sample_key=str(vector["id"]),
            )
            assert result.outcome.value == vector["expected_outcome"], vector["id"]


class TestShadowProjectionComparators:
    def test_existing_projectors_and_usage_rollups_have_golden_coverage(self) -> None:
        run_id, events = _golden_events()
        service = _service(
            RolloutCapability.PRESENTATION_V2_1,
            RolloutCapability.ARTIFACT_REPOSITORY,
            RolloutCapability.OPERATION_GATEWAY,
        )
        comparators = ShadowProjectionComparators(service)

        surface = comparators.compare_surface_tabs(
            run_id=run_id,
            events=events,
            sample_key=run_id,
        )
        receipt = comparators.compare_receipt(
            run_id=run_id,
            events=events,
            run_status="completed",
            sample_key=run_id,
        )
        pending = comparators.compare_pending(
            run_id=run_id,
            events=events,
            sample_key=run_id,
        )
        artifact = comparators.compare_artifact_draft_metadata_from_events(
            events=events,
            sample_key=run_id,
        )
        arguments = {"b": [1, 2], "a": {"nested": True}}
        proposal = comparators.compare_proposal_canonicalization(
            legacy_arguments=arguments,
            canonical_args_digest=sha256_hex(canonical_json_bytes(arguments)),
            sample_key=run_id,
        )
        usage = comparators.compare_usage_folds(
            run_id=run_id,
            events=events,
            usage_rows=(
                _usage_row(
                    run_id=run_id,
                    purpose="main",
                    input_tokens=1200,
                    output_tokens=340,
                ),
                _usage_row(
                    run_id=run_id,
                    purpose="view_shaping",
                    input_tokens=400,
                    output_tokens=120,
                ),
            ),
            sample_key=run_id,
        )

        assert surface.outcome is ShadowComparisonOutcome.MATCH
        assert receipt.outcome is not ShadowComparisonOutcome.ERROR
        assert pending.outcome is not ShadowComparisonOutcome.ERROR
        assert artifact.outcome is not ShadowComparisonOutcome.ERROR
        assert proposal.outcome is ShadowComparisonOutcome.MATCH
        assert usage.outcome is ShadowComparisonOutcome.MATCH

    @pytest.mark.asyncio
    async def test_terminal_observer_is_read_only_and_never_enqueues_or_applies(
        self,
    ) -> None:
        run_id, events = _golden_events()
        observer = ShadowRunProjectionObserver(
            _service(
                RolloutCapability.PRESENTATION_V2_1,
                RolloutCapability.ARTIFACT_REPOSITORY,
            )
        )
        store = ReadOnlyEventStore(events, latest_sequence=len(events))

        results = await observer.observe(
            event_store=store,
            org_id="org-1",
            run_id=run_id,
            run_status="completed",
        )

        assert store.read_calls == ["get_latest_sequence", "list_events_after"]
        assert store.side_effect_calls == []
        assert len(results) == 4
        assert all(
            result.outcome is not ShadowComparisonOutcome.ERROR for result in results
        )

    @pytest.mark.asyncio
    async def test_observer_refuses_unbounded_run_without_reading_event_payloads(
        self,
    ) -> None:
        observer = ShadowRunProjectionObserver(
            _service(RolloutCapability.PRESENTATION_V2_1)
        )
        store = ReadOnlyEventStore((), latest_sequence=513)

        results = await observer.observe(
            event_store=store,
            org_id="org-1",
            run_id="run-too-large",
            run_status="completed",
        )

        assert store.read_calls == ["get_latest_sequence"]
        assert [result.outcome for result in results] == [
            ShadowComparisonOutcome.UNCOMPARABLE,
            ShadowComparisonOutcome.UNCOMPARABLE,
            ShadowComparisonOutcome.UNCOMPARABLE,
            ShadowComparisonOutcome.DISABLED,
        ]


class TestOperationProbeIntegration(BoundContextMixin):
    @pytest.mark.asyncio
    async def test_d2_shadow_never_reinvokes_legacy_or_mutates_its_return(self) -> None:
        emitter = RecordingEmitter()
        operation_token = self.bind(emitter=emitter)
        metrics = RecordingShadowMetrics()
        shadow_token = ShadowComparisonContext.bind_for_run(
            resolution=_resolution(RolloutCapability.OPERATION_GATEWAY),
            metrics_port=metrics,
            diagnostic_sink=RecordingDiagnosticSink(),
        )
        calls = 0
        result = {"legacy": ["opaque"]}

        async def legacy() -> object:
            nonlocal calls
            calls += 1
            return result

        try:
            observed = await OperationShadowProbe.invoke_legacy(
                capability="builtin",
                op="web_search",
                arguments={"query": "keep this model-visible input unchanged"},
                legacy=legacy,
                legacy_class="none",
            )
        finally:
            ShadowComparisonContext.unbind(shadow_token)
            OperationContext.unbind(operation_token)

        assert observed is result
        assert calls == 1
        assert result == {"legacy": ["opaque"]}
        assert all(
            event_type
            in {
                LedgerEventType.OPERATION_REQUESTED,
                LedgerEventType.OPERATION_CLASSIFIED,
                LedgerEventType.OPERATION_COMPLETED,
            }
            for event_type, _payload, _summary in emitter.events
        )
        assert metrics.comparisons
        assert not any(
            name in repr(emitter.events)
            for name in ("artifact.created", "effect.staged", "effect.applied")
        )

    @pytest.mark.asyncio
    async def test_d2_telemetry_failure_never_suppresses_the_legacy_operation(
        self,
    ) -> None:
        emitter = RecordingEmitter()
        operation_token = self.bind(emitter=emitter)
        shadow_token = ShadowComparisonContext.bind_for_run(
            resolution=_resolution(RolloutCapability.OPERATION_GATEWAY),
            metrics_port=FailingShadowMetrics(),
            diagnostic_sink=FailingDiagnosticSink(),
        )
        calls = 0
        result = {"legacy": "authoritative"}

        async def legacy() -> object:
            nonlocal calls
            calls += 1
            return result

        try:
            observed = await OperationShadowProbe.invoke_legacy(
                capability="builtin",
                op="web_search",
                arguments={"query": "unchanged"},
                legacy=legacy,
                legacy_class="external_destructive",
            )
        finally:
            ShadowComparisonContext.unbind(shadow_token)
            OperationContext.unbind(operation_token)

        assert observed is result
        assert calls == 1
        assert result == {"legacy": "authoritative"}


def _usage_row(
    *,
    run_id: str,
    purpose: str,
    input_tokens: int,
    output_tokens: int,
) -> RuntimeModelCallUsageRecord:
    return RuntimeModelCallUsageRecord(
        org_id="org-1",
        run_id=run_id,
        conversation_id="conversation-1",
        trace_id="trace-1",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        purpose=purpose,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )
