"""Low-cardinality F10 metrics projected from canonical invocation records.

The projector accepts only validated ``SequencedModelInvocationRecord`` values.
It never accepts request context, identifiers as labels, provider exceptions,
prompts, responses, endpoint URLs, or credentials.  Stable record identities
are retained only in bounded process memory to make overlapping journal replay
exactly once for the lifetime of a projector.

Circuit signals are deliberately conservative projections of journal facts:

* an ``open_circuit`` route exclusion observes an opened circuit;
* a later admitted attempt for that excluded deployment observes a probe; and
* successful terminal attribution to that probe observes recovery.

No probe or recovery is synthesized when the journal does not prove it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import logging
from types import MappingProxyType
from typing import Final, Literal, Protocol

from pydantic import Field, NonNegativeFloat, model_validator

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.execution.model_invocation.contracts import (
    ModelAttemptDecisionKind,
    ModelAttemptDecisionReason,
    ModelFailureClass,
    ModelFallbackPolicy,
    ModelRouteExclusionReason,
)
from agent_runtime.execution.model_invocation.journal import (
    ModelAttemptAdmissionRecord,
    ModelAttemptFailedRecord,
    ModelAttemptLifecycleState,
    ModelAttemptStateRecord,
    ModelAttemptUsageRecord,
    ModelInvocationCompletedRecord,
    ModelInvocationFailedRecord,
    ModelInvocationFailureReason,
    ModelInvocationPlannedRecord,
    ModelInvocationRecoveryRecord,
    ModelRecoveryKind,
    ModelRecoveryOutcome,
    ModelRouteExcludedRecord,
    SequencedModelInvocationRecord,
)
from agent_runtime.observability.attribution import Purpose


_LOGGER = logging.getLogger(__name__)
_METER_NAME: Final = "agent_runtime.model_invocation"


class ModelInvocationMetricName(StrEnum):
    """Stable metric names owned by the F10 journal projector."""

    ROUTE_PLANS_TOTAL = "model_invocation_route_plans_total"
    ROUTE_EXCLUSIONS_TOTAL = "model_invocation_route_exclusions_total"
    POLICY_EXCLUSIONS_TOTAL = "model_invocation_policy_exclusions_total"
    ATTEMPTS_TOTAL = "model_invocation_attempts_total"
    RECOVERIES_TOTAL = "model_invocation_recoveries_total"
    TERMINAL_TOTAL = "model_invocation_terminal_total"
    AMBIGUOUS_TOTAL = "model_invocation_ambiguous_total"
    ATTEMPT_LATENCY_SECONDS = "model_invocation_attempt_latency_seconds"
    FALLBACK_LATENCY_SECONDS = "model_invocation_fallback_latency_seconds"
    REPORTED_TOKENS_TOTAL = "model_invocation_reported_tokens_total"
    REPORTED_COST_MICROUSD_TOTAL = "model_invocation_reported_cost_microusd_total"
    MISSING_FINALIZATION_TOTAL = "model_invocation_missing_finalization_total"
    CIRCUIT_EVENTS_TOTAL = "model_invocation_circuit_events_total"


class ModelInvocationAttemptKind(StrEnum):
    PRIMARY = "primary"
    RETRY = "retry"
    FALLBACK = "fallback"
    UNKNOWN = "unknown"


class ModelInvocationTerminalOutcome(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"


class ModelInvocationUsageSource(StrEnum):
    PROVIDER_REPORTED = "provider_reported"
    UNREPORTED = "unreported"


class ModelInvocationTokenKind(StrEnum):
    INPUT = "input"
    OUTPUT = "output"
    CACHED_INPUT = "cached_input"
    CACHE_CREATION_INPUT = "cache_creation_input"
    REASONING = "reasoning"
    AUDIO_INPUT = "audio_input"
    AUDIO_OUTPUT = "audio_output"


class ModelInvocationPolicyDimension(StrEnum):
    REGION = "region"
    PRIVACY = "privacy"
    BYOK = "byok"


class ModelInvocationCircuitEvent(StrEnum):
    OPENED = "opened"
    PROBED = "probed"
    RECOVERED = "recovered"


class ModelInvocationAmbiguousSource(StrEnum):
    ATTEMPT_STATE = "attempt_state"
    ATTEMPT_FAILURE = "attempt_failure"
    RECOVERY = "recovery"
    TERMINAL = "terminal"


MetricInstrument = Literal["counter", "histogram"]


@dataclass(frozen=True)
class ModelInvocationMetricDefinition:
    """One fixed instrument and its complete allowed label vocabulary."""

    instrument: MetricInstrument
    labels: tuple[tuple[str, frozenset[str]], ...]
    description: str


def _values(enum_type: type[StrEnum]) -> frozenset[str]:
    return frozenset(item.value for item in enum_type)


_NONE: Final = "none"
_METRIC_DEFINITIONS: Final[
    dict[ModelInvocationMetricName, ModelInvocationMetricDefinition]
] = {
    ModelInvocationMetricName.ROUTE_PLANS_TOTAL: ModelInvocationMetricDefinition(
        instrument="counter",
        labels=(
            ("purpose", _values(Purpose)),
            ("fallback_policy", _values(ModelFallbackPolicy)),
        ),
        description="Canonical F10 route plans.",
    ),
    ModelInvocationMetricName.ROUTE_EXCLUSIONS_TOTAL: ModelInvocationMetricDefinition(
        instrument="counter",
        labels=(("reason", _values(ModelRouteExclusionReason)),),
        description="Canonical route exclusions by closed policy reason.",
    ),
    ModelInvocationMetricName.POLICY_EXCLUSIONS_TOTAL: ModelInvocationMetricDefinition(
        instrument="counter",
        labels=(("dimension", _values(ModelInvocationPolicyDimension)),),
        description="Region, privacy, and BYOK route exclusions.",
    ),
    ModelInvocationMetricName.ATTEMPTS_TOTAL: ModelInvocationMetricDefinition(
        instrument="counter",
        labels=(
            ("attempt_kind", _values(ModelInvocationAttemptKind)),
            ("decision", _values(ModelAttemptDecisionKind)),
            ("reason", _values(ModelAttemptDecisionReason)),
        ),
        description="Primary, retry, and fallback admission decisions.",
    ),
    ModelInvocationMetricName.RECOVERIES_TOTAL: ModelInvocationMetricDefinition(
        instrument="counter",
        labels=(
            ("kind", _values(ModelRecoveryKind)),
            ("outcome", _values(ModelRecoveryOutcome)),
        ),
        description="Canonical retry, reroute, and crash-recovery outcomes.",
    ),
    ModelInvocationMetricName.TERMINAL_TOTAL: ModelInvocationMetricDefinition(
        instrument="counter",
        labels=(
            ("outcome", _values(ModelInvocationTerminalOutcome)),
            (
                "reason",
                frozenset({_NONE, *(_values(ModelInvocationFailureReason))}),
            ),
        ),
        description="Terminal invocation outcomes.",
    ),
    ModelInvocationMetricName.AMBIGUOUS_TOTAL: ModelInvocationMetricDefinition(
        instrument="counter",
        labels=(("source", _values(ModelInvocationAmbiguousSource)),),
        description="Canonical facts that prove ambiguous provider state.",
    ),
    ModelInvocationMetricName.ATTEMPT_LATENCY_SECONDS: (
        ModelInvocationMetricDefinition(
            instrument="histogram",
            labels=(
                ("attempt_kind", _values(ModelInvocationAttemptKind)),
                ("usage_source", _values(ModelInvocationUsageSource)),
            ),
            description="Finalized per-attempt duration.",
        )
    ),
    ModelInvocationMetricName.FALLBACK_LATENCY_SECONDS: (
        ModelInvocationMetricDefinition(
            instrument="histogram",
            labels=(("outcome", _values(ModelInvocationTerminalOutcome)),),
            description="End-to-end latency for invocations that used a fallback.",
        )
    ),
    ModelInvocationMetricName.REPORTED_TOKENS_TOTAL: (
        ModelInvocationMetricDefinition(
            instrument="counter",
            labels=(
                ("attempt_kind", _values(ModelInvocationAttemptKind)),
                ("token_kind", _values(ModelInvocationTokenKind)),
            ),
            description="Provider-reported tokens by independently finalized attempt.",
        )
    ),
    ModelInvocationMetricName.REPORTED_COST_MICROUSD_TOTAL: (
        ModelInvocationMetricDefinition(
            instrument="counter",
            labels=(("attempt_kind", _values(ModelInvocationAttemptKind)),),
            description="Provider-reported attempt cost in micro-US dollars.",
        )
    ),
    ModelInvocationMetricName.MISSING_FINALIZATION_TOTAL: (
        ModelInvocationMetricDefinition(
            instrument="counter",
            labels=(("attempt_kind", _values(ModelInvocationAttemptKind)),),
            description="Admitted attempts lacking an exactly-once usage finalizer.",
        )
    ),
    ModelInvocationMetricName.CIRCUIT_EVENTS_TOTAL: ModelInvocationMetricDefinition(
        instrument="counter",
        labels=(("event", _values(ModelInvocationCircuitEvent)),),
        description="Journal-proven circuit open, probe, and recovery observations.",
    ),
}

MODEL_INVOCATION_METRIC_DEFINITIONS: Final = MappingProxyType(_METRIC_DEFINITIONS)


class ModelInvocationMetricFact(RuntimeContract):
    """Validated metric fact with canonical, registry-bounded attributes."""

    name: ModelInvocationMetricName
    value: NonNegativeFloat
    attributes: tuple[tuple[str, str], ...] = Field(max_length=3)

    @model_validator(mode="after")
    def _attributes_are_registered(self) -> "ModelInvocationMetricFact":
        definition = _METRIC_DEFINITIONS[self.name]
        expected = definition.labels
        if tuple(key for key, _value in self.attributes) != tuple(
            key for key, _values in expected
        ):
            raise ValueError("metric attributes do not match the fixed registry")
        for (_key, value), (_expected_key, allowed) in zip(
            self.attributes, expected, strict=True
        ):
            if value not in allowed:
                raise ValueError("metric attribute value is outside the fixed registry")
        return self

    @property
    def otel_attributes(self) -> dict[str, str]:
        return dict(self.attributes)


class ModelInvocationMetricsPort(Protocol):
    """Sink for facts that already passed the bounded registry."""

    def record(self, fact: ModelInvocationMetricFact) -> None: ...


class OpenTelemetryModelInvocationMetrics:
    """Fail-soft OTel facade; publication can never change runtime behavior."""

    def __init__(self) -> None:
        self._instruments: dict[ModelInvocationMetricName, object] = {}
        try:
            from opentelemetry import metrics

            self._meter = metrics.get_meter(_METER_NAME)
        except Exception:  # pragma: no cover - defensive optional dependency
            self._meter = None

    def record(self, fact: ModelInvocationMetricFact) -> None:
        if self._meter is None:
            return
        try:
            instrument = self._instruments.get(fact.name)
            if instrument is None:
                definition = _METRIC_DEFINITIONS[fact.name]
                factory = (
                    self._meter.create_counter
                    if definition.instrument == "counter"
                    else self._meter.create_histogram
                )
                instrument = factory(
                    fact.name.value,
                    description=definition.description,
                )
                self._instruments[fact.name] = instrument
            if _METRIC_DEFINITIONS[fact.name].instrument == "counter":
                instrument.add(fact.value, fact.otel_attributes)  # type: ignore[attr-defined]
            else:
                instrument.record(  # type: ignore[attr-defined]
                    fact.value, fact.otel_attributes
                )
        except Exception:
            _LOGGER.debug("model_invocation_metric_publish_failed", exc_info=True)


class ModelInvocationMetricsProjectionError(ValueError):
    """The supplied replay cannot be projected without losing exactness."""


class ModelInvocationMetricsReplayCheckpoint(RuntimeContract):
    """Content-free cursor returned when an outer run is durably terminal.

    ``run_id`` and the sequence cursor are composition state only. They are
    never passed to the metric facade or used as OTel attributes.
    """

    run_id: str = Field(min_length=1, max_length=160)
    after_sequence: int = Field(ge=1)
    projected_records: int = Field(ge=1)


@dataclass(frozen=True)
class _AttemptProjection:
    invocation_id: str
    deployment_id: str
    kind: ModelInvocationAttemptKind


class ModelInvocationMetricsProjector:
    """Stateful exact-once projector for overlapping canonical journal replay.

    One instance owns one run from its first record through the outer run's
    durable terminal boundary. It never evicts deduplication identities:
    exceeding ``max_records`` fails explicitly instead of silently
    double-counting an old record. At the outer terminal boundary, call
    :meth:`seal_terminal_replay`, persist/use its cursor as composition state,
    and discard the projector. A new run gets a new projector.
    """

    def __init__(
        self,
        *,
        metrics: ModelInvocationMetricsPort | None = None,
        max_records: int = 4096,
    ) -> None:
        if max_records < 1 or max_records > 100_000:
            raise ValueError("max_records must be between 1 and 100000")
        self._metrics = metrics or OpenTelemetryModelInvocationMetrics()
        self._max_records = max_records
        self._seen: dict[tuple[str, str], tuple[int, str]] = {}
        self._run_id: str | None = None
        self._max_sequence = 0
        self._sealed = False
        self._attempts: dict[str, _AttemptProjection] = {}
        self._finalized_attempts: set[str] = set()
        self._missing_emitted: set[str] = set()
        self._fallback_invocations: set[str] = set()
        self._open_deployments: set[str] = set()
        self._probe_attempts: dict[str, str] = {}

    @property
    def projected_record_count(self) -> int:
        return len(self._seen)

    def project(
        self,
        records: tuple[SequencedModelInvocationRecord, ...],
        *,
        detect_missing_finalization: bool = False,
    ) -> None:
        """Project new records and optionally audit open usage finalizers.

        ``detect_missing_finalization`` is for a terminal replay or a
        crash/recovery boundary.  Do not set it after an ordinary live prefix,
        because an in-flight admitted attempt is expected to lack final usage.
        """

        if self._sealed:
            raise ModelInvocationMetricsProjectionError(
                "sealed invocation metric replay cannot accept more records"
            )
        for sequenced in records:
            self._project_one(sequenced)
        if detect_missing_finalization:
            self.detect_missing_finalization()

    def seal_terminal_replay(self) -> ModelInvocationMetricsReplayCheckpoint:
        """Audit finalizers and close this run-scoped projector for rotation.

        The caller must invoke this only after the *outer run* terminal event is
        durable. Sealing an ordinary model-invocation terminal would lose
        exact-once state for later model calls in the same run.
        """

        if self._sealed:
            raise ModelInvocationMetricsProjectionError(
                "invocation metric replay is already sealed"
            )
        if self._run_id is None or not self._seen:
            raise ModelInvocationMetricsProjectionError(
                "cannot seal an empty invocation metric replay"
            )
        self.detect_missing_finalization()
        self._sealed = True
        return ModelInvocationMetricsReplayCheckpoint(
            run_id=self._run_id,
            after_sequence=self._max_sequence,
            projected_records=len(self._seen),
        )

    def detect_missing_finalization(self) -> None:
        """Emit once for each admitted attempt not finalized in the replay."""

        for attempt_id, attempt in self._attempts.items():
            if (
                attempt_id in self._finalized_attempts
                or attempt_id in self._missing_emitted
            ):
                continue
            self._emit(
                ModelInvocationMetricName.MISSING_FINALIZATION_TOTAL,
                1,
                attempt_kind=attempt.kind.value,
            )
            self._missing_emitted.add(attempt_id)

    def _project_one(self, sequenced: SequencedModelInvocationRecord) -> None:
        record = sequenced.record
        if self._run_id is None:
            self._run_id = record.run_id
        elif record.run_id != self._run_id:
            raise ModelInvocationMetricsProjectionError(
                "one invocation metric projector cannot span multiple runs"
            )
        identity = (record.run_id, record.record_id)
        prior = self._seen.get(identity)
        signature = (sequenced.sequence_no, record.record_digest)
        if prior is not None:
            if prior != signature:
                raise ModelInvocationMetricsProjectionError(
                    "duplicate invocation metric record conflicts with prior replay"
                )
            return
        if len(self._seen) >= self._max_records:
            raise ModelInvocationMetricsProjectionError(
                "invocation metric replay exceeds its exact-once record bound"
            )
        if sequenced.sequence_no <= self._max_sequence:
            raise ModelInvocationMetricsProjectionError(
                "new invocation metric record regresses journal sequence"
            )
        self._seen[identity] = signature
        self._max_sequence = sequenced.sequence_no
        self._project_record(record)

    def _project_record(self, record: object) -> None:
        if isinstance(record, ModelInvocationPlannedRecord):
            self._emit(
                ModelInvocationMetricName.ROUTE_PLANS_TOTAL,
                1,
                purpose=record.purpose.value,
                fallback_policy=record.fallback_policy.value,
            )
            return
        if isinstance(record, ModelRouteExcludedRecord):
            for reason in record.reasons:
                self._emit(
                    ModelInvocationMetricName.ROUTE_EXCLUSIONS_TOTAL,
                    1,
                    reason=reason.value,
                )
                policy_dimension = _policy_dimension(reason)
                if policy_dimension is not None:
                    self._emit(
                        ModelInvocationMetricName.POLICY_EXCLUSIONS_TOTAL,
                        1,
                        dimension=policy_dimension.value,
                    )
                if (
                    reason is ModelRouteExclusionReason.OPEN_CIRCUIT
                    and record.deployment_id not in self._open_deployments
                ):
                    self._open_deployments.add(record.deployment_id)
                    self._emit(
                        ModelInvocationMetricName.CIRCUIT_EVENTS_TOTAL,
                        1,
                        event=ModelInvocationCircuitEvent.OPENED.value,
                    )
            return
        if isinstance(record, ModelAttemptAdmissionRecord):
            kind = _attempt_kind(record.reason)
            self._emit(
                ModelInvocationMetricName.ATTEMPTS_TOTAL,
                1,
                attempt_kind=kind.value,
                decision=record.decision.value,
                reason=record.reason.value,
            )
            if record.decision is ModelAttemptDecisionKind.ADMIT:
                assert record.attempt_id is not None
                assert record.deployment_id is not None
                self._attempts[record.attempt_id] = _AttemptProjection(
                    invocation_id=record.invocation_id,
                    deployment_id=record.deployment_id,
                    kind=kind,
                )
                if record.deployment_id in self._open_deployments:
                    self._probe_attempts[record.attempt_id] = record.deployment_id
                    self._emit(
                        ModelInvocationMetricName.CIRCUIT_EVENTS_TOTAL,
                        1,
                        event=ModelInvocationCircuitEvent.PROBED.value,
                    )
                if kind is ModelInvocationAttemptKind.FALLBACK:
                    self._fallback_invocations.add(record.invocation_id)
            return
        if isinstance(record, ModelAttemptUsageRecord):
            attempt = self._attempts.get(record.attempt_id)
            kind = (
                attempt.kind
                if attempt is not None
                else ModelInvocationAttemptKind.UNKNOWN
            )
            source = (
                ModelInvocationUsageSource.PROVIDER_REPORTED
                if record.provider_reported
                else ModelInvocationUsageSource.UNREPORTED
            )
            self._emit(
                ModelInvocationMetricName.ATTEMPT_LATENCY_SECONDS,
                record.duration_ms / 1000,
                attempt_kind=kind.value,
                usage_source=source.value,
            )
            if record.provider_reported:
                token_values = {
                    ModelInvocationTokenKind.INPUT: record.input_tokens,
                    ModelInvocationTokenKind.OUTPUT: record.output_tokens,
                    ModelInvocationTokenKind.CACHED_INPUT: record.cached_input_tokens,
                    ModelInvocationTokenKind.CACHE_CREATION_INPUT: (
                        record.cache_creation_input_tokens
                    ),
                    ModelInvocationTokenKind.REASONING: record.reasoning_tokens,
                    ModelInvocationTokenKind.AUDIO_INPUT: record.audio_input_tokens,
                    ModelInvocationTokenKind.AUDIO_OUTPUT: record.audio_output_tokens,
                }
                for token_kind, value in token_values.items():
                    self._emit(
                        ModelInvocationMetricName.REPORTED_TOKENS_TOTAL,
                        value,
                        attempt_kind=kind.value,
                        token_kind=token_kind.value,
                    )
                self._emit(
                    ModelInvocationMetricName.REPORTED_COST_MICROUSD_TOTAL,
                    record.cost_microusd,
                    attempt_kind=kind.value,
                )
            self._finalized_attempts.add(record.attempt_id)
            return
        if isinstance(record, ModelAttemptStateRecord):
            if record.state is ModelAttemptLifecycleState.AMBIGUOUS:
                self._emit(
                    ModelInvocationMetricName.AMBIGUOUS_TOTAL,
                    1,
                    source=ModelInvocationAmbiguousSource.ATTEMPT_STATE.value,
                )
            return
        if isinstance(record, ModelAttemptFailedRecord):
            if record.failure_class is ModelFailureClass.AMBIGUOUS_PROVIDER_STATE:
                self._emit(
                    ModelInvocationMetricName.AMBIGUOUS_TOTAL,
                    1,
                    source=ModelInvocationAmbiguousSource.ATTEMPT_FAILURE.value,
                )
            self._probe_attempts.pop(record.attempt_id, None)
            return
        if isinstance(record, ModelInvocationRecoveryRecord):
            self._emit(
                ModelInvocationMetricName.RECOVERIES_TOTAL,
                1,
                kind=record.kind.value,
                outcome=record.outcome.value,
            )
            if (
                record.kind is ModelRecoveryKind.ALTERNATE_ROUTE
                and record.outcome is ModelRecoveryOutcome.ADMITTED
            ):
                self._fallback_invocations.add(record.invocation_id)
            if record.outcome is ModelRecoveryOutcome.AMBIGUOUS:
                self._emit(
                    ModelInvocationMetricName.AMBIGUOUS_TOTAL,
                    1,
                    source=ModelInvocationAmbiguousSource.RECOVERY.value,
                )
            return
        if isinstance(record, ModelInvocationCompletedRecord):
            self._terminal(
                record,
                outcome=ModelInvocationTerminalOutcome.COMPLETED,
                reason=_NONE,
            )
            deployment_id = self._probe_attempts.pop(record.terminal_attempt_id, None)
            if deployment_id is not None:
                self._open_deployments.discard(deployment_id)
                self._emit(
                    ModelInvocationMetricName.CIRCUIT_EVENTS_TOTAL,
                    1,
                    event=ModelInvocationCircuitEvent.RECOVERED.value,
                )
            return
        if isinstance(record, ModelInvocationFailedRecord):
            ambiguous = (
                record.reason is ModelInvocationFailureReason.AMBIGUOUS_RECOVERY
                or record.failure_class is ModelFailureClass.AMBIGUOUS_PROVIDER_STATE
            )
            outcome = (
                ModelInvocationTerminalOutcome.AMBIGUOUS
                if ambiguous
                else ModelInvocationTerminalOutcome.FAILED
            )
            self._terminal(record, outcome=outcome, reason=record.reason.value)
            if ambiguous:
                self._emit(
                    ModelInvocationMetricName.AMBIGUOUS_TOTAL,
                    1,
                    source=ModelInvocationAmbiguousSource.TERMINAL.value,
                )

    def _terminal(
        self,
        record: ModelInvocationCompletedRecord | ModelInvocationFailedRecord,
        *,
        outcome: ModelInvocationTerminalOutcome,
        reason: str,
    ) -> None:
        self._emit(
            ModelInvocationMetricName.TERMINAL_TOTAL,
            1,
            outcome=outcome.value,
            reason=reason,
        )
        if record.invocation_id in self._fallback_invocations:
            self._emit(
                ModelInvocationMetricName.FALLBACK_LATENCY_SECONDS,
                record.total_duration_ms / 1000,
                outcome=outcome.value,
            )
        self._detect_missing_for_invocation(record.invocation_id)

    def _detect_missing_for_invocation(self, invocation_id: str) -> None:
        for attempt_id, attempt in self._attempts.items():
            if attempt.invocation_id != invocation_id:
                continue
            if (
                attempt_id in self._finalized_attempts
                or attempt_id in self._missing_emitted
            ):
                continue
            self._emit(
                ModelInvocationMetricName.MISSING_FINALIZATION_TOTAL,
                1,
                attempt_kind=attempt.kind.value,
            )
            self._missing_emitted.add(attempt_id)

    def _emit(
        self,
        name: ModelInvocationMetricName,
        value: int | float,
        **attributes: str,
    ) -> None:
        definition = _METRIC_DEFINITIONS[name]
        ordered = tuple(
            (label_name, attributes[label_name])
            for label_name, _allowed in definition.labels
        )
        self._metrics.record(
            ModelInvocationMetricFact(
                name=name,
                value=value,
                attributes=ordered,
            )
        )


def _attempt_kind(reason: ModelAttemptDecisionReason) -> ModelInvocationAttemptKind:
    if reason is ModelAttemptDecisionReason.FIRST_ATTEMPT:
        return ModelInvocationAttemptKind.PRIMARY
    if reason is ModelAttemptDecisionReason.SAFE_SAME_DEPLOYMENT_RETRY:
        return ModelInvocationAttemptKind.RETRY
    if reason is ModelAttemptDecisionReason.SAFE_ALTERNATE_ROUTE:
        return ModelInvocationAttemptKind.FALLBACK
    return ModelInvocationAttemptKind.UNKNOWN


def _policy_dimension(
    reason: ModelRouteExclusionReason,
) -> ModelInvocationPolicyDimension | None:
    if reason is ModelRouteExclusionReason.REGION_MISMATCH:
        return ModelInvocationPolicyDimension.REGION
    if reason is ModelRouteExclusionReason.PRIVACY_INCOMPATIBLE:
        return ModelInvocationPolicyDimension.PRIVACY
    if reason in {
        ModelRouteExclusionReason.BYOK_REQUIRED,
        ModelRouteExclusionReason.BYOK_DISALLOWED,
    }:
        return ModelInvocationPolicyDimension.BYOK
    return None


__all__ = (
    "MODEL_INVOCATION_METRIC_DEFINITIONS",
    "ModelInvocationAmbiguousSource",
    "ModelInvocationAttemptKind",
    "ModelInvocationCircuitEvent",
    "ModelInvocationMetricDefinition",
    "ModelInvocationMetricFact",
    "ModelInvocationMetricName",
    "ModelInvocationMetricsPort",
    "ModelInvocationMetricsProjectionError",
    "ModelInvocationMetricsProjector",
    "ModelInvocationMetricsReplayCheckpoint",
    "ModelInvocationPolicyDimension",
    "ModelInvocationTerminalOutcome",
    "ModelInvocationTokenKind",
    "ModelInvocationUsageSource",
    "OpenTelemetryModelInvocationMetrics",
)
