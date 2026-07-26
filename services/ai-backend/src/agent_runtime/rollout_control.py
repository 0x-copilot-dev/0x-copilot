"""Pure, trusted E2 cohort admission and D6 promotion controls.

This is deliberately a control-plane module.  It receives only an immutable
deployment configuration and verified identity facts, then returns decisions.
It owns no request parsing, config reload, executor, queue, stage, or effect
port.  In particular, a decision returned here cannot route or dispatch an
effect differently by itself.

The D2 shadow comparator remains the owner of shadow mismatch telemetry and
its protected fingerprints.  This module adds the adjacent low-cardinality
telemetry needed for D6 cohort admission and promotion/rollback decisions.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, Protocol, runtime_checkable

from opentelemetry import metrics
from pydantic import Field, model_validator

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.rollout import E2RolloutResolution, RolloutCapability, RolloutMode


class RolloutCohortConfigurationError(ValueError):
    """Safe error for malformed cohort startup configuration or identity facts."""


class CohortMatchScope(StrEnum):
    """Closed, identifier-free selector shape for metrics and diagnostics."""

    ORG = "org"
    USER = "user"
    DEVICE = "device"
    CONNECTOR = "connector"
    COMPOSITE = "composite"


class CohortAdmissionOutcome(StrEnum):
    """Closed cohort-admission outcomes; no input value becomes a label."""

    ADMITTED = "admitted"
    GLOBAL_OFF = "global_off"
    NO_MATCHING_COHORT = "no_matching_cohort"


class RolloutTransitionOutcome(StrEnum):
    """Closed transition validation outcome."""

    NO_CHANGE = "no_change"
    ADVANCE_TO_SHADOW = "advance_to_shadow"
    PROMOTION_ALLOWED = "promotion_allowed"
    ROLLBACK_ALLOWED = "rollback_allowed"
    REJECTED = "rejected"


class SoakEvaluationOutcome(StrEnum):
    """Closed D6 soak evaluation result."""

    PROMOTE = "promote"
    INSUFFICIENT_SOAK = "insufficient_soak"
    INSUFFICIENT_ADMISSIONS = "insufficient_admissions"
    MISMATCH_RATE_EXCEEDED = "mismatch_rate_exceeded"
    ERROR_RATE_EXCEEDED = "error_rate_exceeded"
    INVALID_OBSERVATION_WINDOW = "invalid_observation_window"


class RolloutDiagnosticKind(StrEnum):
    """Bounded diagnostic event kind for the cohort/promotion control plane."""

    COHORT_ADMISSION = "cohort_admission"
    TRANSITION = "transition"


_IDENTIFIER_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_FINGERPRINT_HEX_LENGTH: Final = 32
_DIAGNOSTIC_SAMPLE_DENOMINATOR: Final = 64
_PROCESS_FINGERPRINT_KEY: Final[bytes] = secrets.token_bytes(32)
_LOGGER = logging.getLogger(__name__)
_METER_NAME: Final = "agent_runtime.e2_rollout_control"
_COHORTS_JSON_ENVIRONMENT_NAME: Final = "E2_ROLLOUT_COHORTS_JSON"


class RolloutCohortRule(RuntimeContract):
    """One exact-match, trusted capability cohort rule.

    A rule must constrain at least one of D6's supported cohort dimensions.
    The rule contains identifiers for matching only; none are emitted in a
    decision, metric, or diagnostic.
    """

    capability: RolloutCapability
    org_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    user_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    device_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    connector_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)

    @model_validator(mode="after")
    def _requires_a_selector(self) -> "RolloutCohortRule":
        if not any((self.org_id, self.user_id, self.device_id, self.connector_id)):
            raise ValueError("a rollout cohort rule requires a selector")
        return self

    @property
    def match_scope(self) -> CohortMatchScope:
        """Return only the selector shape, never a selector value."""

        selected = sum(
            value is not None
            for value in (self.org_id, self.user_id, self.device_id, self.connector_id)
        )
        if selected > 1:
            return CohortMatchScope.COMPOSITE
        if self.org_id is not None:
            return CohortMatchScope.ORG
        if self.user_id is not None:
            return CohortMatchScope.USER
        if self.device_id is not None:
            return CohortMatchScope.DEVICE
        return CohortMatchScope.CONNECTOR

    def matches(self, subject: "VerifiedRolloutCohortSubject") -> bool:
        """Return whether every selector on this rule matches verified facts."""

        return all(
            expected is None or expected == actual
            for expected, actual in (
                (self.org_id, subject.org_id),
                (self.user_id, subject.user_id),
                (self.device_id, subject.device_id),
                (self.connector_id, subject.connector_id),
            )
        )


class _StartupRolloutCohortDocument(RuntimeContract):
    """Private JSON document accepted only from the deployment environment."""

    rules: tuple[RolloutCohortRule, ...] = Field(default_factory=tuple, max_length=128)

    @model_validator(mode="after")
    def _requires_unique_rules(self) -> "_StartupRolloutCohortDocument":
        if len(self.rules) != len(set(self.rules)):
            raise ValueError("rollout cohort rules must be unique")
        return self


class VerifiedRolloutCohortFacts(RuntimeContract):
    """Identity/device facts produced by a server-authoritative provider only."""

    org_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    user_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    device_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    connector_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)


@runtime_checkable
class VerifiedRolloutCohortFactsProvider(Protocol):
    """Explicit composition seam for server-verified rollout identity facts.

    Implementations must derive facts from a verified session, persisted run,
    attested device, or server-side connector registry.  Runtime API payloads
    and plain mappings do not satisfy this boundary and are rejected by
    :meth:`VerifiedRolloutCohortSubject.from_verified_facts_provider`.
    """

    def verified_rollout_cohort_facts(self) -> VerifiedRolloutCohortFacts:
        """Return facts already verified by the implementation's authority."""


class VerifiedRolloutCohortSubject(RuntimeContract):
    """Resolved verified facts, never parsed from a request-shaped mapping."""

    org_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    user_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    device_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    connector_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)

    @classmethod
    def from_verified_facts_provider(
        cls, provider: VerifiedRolloutCohortFactsProvider
    ) -> "VerifiedRolloutCohortSubject":
        """Copy only facts from the explicit trusted provider seam.

        This constructor intentionally has no mapping/JSON overload.  A caller
        handling a request must first cross an authenticated server-owned
        identity or connector/device-attestation seam before it can construct
        a cohort subject.
        """

        try:
            if isinstance(provider, Mapping) or not isinstance(
                provider, VerifiedRolloutCohortFactsProvider
            ):
                raise TypeError
            facts = provider.verified_rollout_cohort_facts()
            if not isinstance(facts, VerifiedRolloutCohortFacts):
                raise TypeError
        except Exception as exc:
            raise RolloutCohortConfigurationError(
                "verified rollout cohort facts must come from a trusted provider"
            ) from exc
        return cls(
            org_id=facts.org_id,
            user_id=facts.user_id,
            device_id=facts.device_id,
            connector_id=facts.connector_id,
        )


class RolloutCohortPolicy(RuntimeContract):
    """Immutable policy resolved from deployment environment at startup only."""

    rules: tuple[RolloutCohortRule, ...] = Field(default_factory=tuple)

    @classmethod
    def from_startup_environment(
        cls, environment: Mapping[str, str]
    ) -> "RolloutCohortPolicy":
        """Resolve ``E2_ROLLOUT_COHORTS_JSON`` once from deployment environment.

        ``RuntimeSettings.load`` is the only production call site.  The JSON
        value must be an array of exact-match rules; missing or blank config
        produces an empty policy.  Unknown fields, a former caller-supplied
        trust label, malformed JSON, duplicate rules, and more than 128 rules
        fail startup with a safe error that never includes config values.
        """

        try:
            raw = environment.get(_COHORTS_JSON_ENVIRONMENT_NAME)
            if raw is None or not raw.strip():
                return cls()
            if not isinstance(raw, str):
                raise TypeError
            document = json.loads(raw)
            if not isinstance(document, list):
                raise TypeError
            parsed = _StartupRolloutCohortDocument.model_validate({"rules": document})
        except Exception as exc:
            raise RolloutCohortConfigurationError(
                f"{_COHORTS_JSON_ENVIRONMENT_NAME} is malformed"
            ) from exc
        return cls(rules=parsed.rules)

    def admit(
        self,
        *,
        resolution: E2RolloutResolution,
        subject: VerifiedRolloutCohortSubject,
        capability: RolloutCapability,
    ) -> "CohortAdmissionDecision":
        """Purely decide whether a verified subject is in a globally enabled lane."""

        mode = resolution.modes.mode_for(capability)
        if mode is RolloutMode.OFF:
            return CohortAdmissionDecision(
                capability=capability,
                mode=mode,
                outcome=CohortAdmissionOutcome.GLOBAL_OFF,
            )
        matching = next(
            (
                rule
                for rule in self.rules
                if rule.capability is capability and rule.matches(subject)
            ),
            None,
        )
        if matching is None:
            return CohortAdmissionDecision(
                capability=capability,
                mode=mode,
                outcome=CohortAdmissionOutcome.NO_MATCHING_COHORT,
            )
        return CohortAdmissionDecision(
            capability=capability,
            mode=mode,
            outcome=CohortAdmissionOutcome.ADMITTED,
            match_scope=matching.match_scope,
        )


class CohortAdmissionDecision(RuntimeContract):
    """A value-only admission result; it has no routing or dispatch method."""

    capability: RolloutCapability
    mode: RolloutMode
    outcome: CohortAdmissionOutcome
    match_scope: CohortMatchScope | None = None


class RolloutSoakMetrics(RuntimeContract):
    """Explicit aggregate counters used by the pure D6 evaluator."""

    cohort_admissions: int = Field(ge=0)
    shadow_comparisons: int = Field(ge=0)
    shadow_mismatches: int = Field(ge=0)
    evaluation_errors: int = Field(ge=0)

    @model_validator(mode="after")
    def _consistent_counts(self) -> "RolloutSoakMetrics":
        if self.shadow_mismatches > self.shadow_comparisons:
            raise ValueError("shadow mismatches cannot exceed comparisons")
        if self.evaluation_errors > self.shadow_comparisons:
            raise ValueError("evaluation errors cannot exceed comparisons")
        return self


class RolloutSoakRequirements(RuntimeContract):
    """Static promotion thresholds.  None can be implicit or zero-length."""

    minimum_soak: timedelta
    minimum_admissions: int = Field(ge=1)
    maximum_mismatch_rate: float = Field(ge=0, le=1)
    maximum_error_rate: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _positive_soak(self) -> "RolloutSoakRequirements":
        if self.minimum_soak <= timedelta(0):
            raise ValueError("minimum soak must be positive")
        return self


class RolloutSoakDecision(RuntimeContract):
    """Read-only D6 evaluation; ``PROMOTE`` does not change configuration."""

    capability: RolloutCapability
    outcome: SoakEvaluationOutcome
    elapsed_seconds: int = Field(ge=0)
    admissions: int = Field(ge=0)
    mismatch_rate: float = Field(ge=0, le=1)
    error_rate: float = Field(ge=0, le=1)

    @property
    def permits_enforce_promotion(self) -> bool:
        """Whether a caller may submit a separately authorised config change."""

        return self.outcome is SoakEvaluationOutcome.PROMOTE


def evaluate_soak(
    *,
    capability: RolloutCapability,
    started_at: datetime,
    observed_at: datetime,
    metrics_snapshot: RolloutSoakMetrics,
    requirements: RolloutSoakRequirements,
) -> RolloutSoakDecision:
    """Evaluate D6 metrics deterministically without changing state or config."""

    _require_aware(started_at)
    _require_aware(observed_at)
    elapsed = observed_at - started_at
    comparisons = metrics_snapshot.shadow_comparisons
    mismatch_rate = (
        metrics_snapshot.shadow_mismatches / comparisons if comparisons else 0.0
    )
    error_rate = (
        metrics_snapshot.evaluation_errors / comparisons if comparisons else 0.0
    )
    elapsed_seconds = max(0, int(elapsed.total_seconds()))
    common = {
        "capability": capability,
        "elapsed_seconds": elapsed_seconds,
        "admissions": metrics_snapshot.cohort_admissions,
        "mismatch_rate": mismatch_rate,
        "error_rate": error_rate,
    }
    if elapsed < timedelta(0):
        return RolloutSoakDecision(
            outcome=SoakEvaluationOutcome.INVALID_OBSERVATION_WINDOW,
            **common,
        )
    if elapsed < requirements.minimum_soak:
        return RolloutSoakDecision(
            outcome=SoakEvaluationOutcome.INSUFFICIENT_SOAK,
            **common,
        )
    if metrics_snapshot.cohort_admissions < requirements.minimum_admissions:
        return RolloutSoakDecision(
            outcome=SoakEvaluationOutcome.INSUFFICIENT_ADMISSIONS,
            **common,
        )
    if mismatch_rate > requirements.maximum_mismatch_rate:
        return RolloutSoakDecision(
            outcome=SoakEvaluationOutcome.MISMATCH_RATE_EXCEEDED,
            **common,
        )
    if error_rate > requirements.maximum_error_rate:
        return RolloutSoakDecision(
            outcome=SoakEvaluationOutcome.ERROR_RATE_EXCEEDED,
            **common,
        )
    return RolloutSoakDecision(outcome=SoakEvaluationOutcome.PROMOTE, **common)


class RolloutTransitionDecision(RuntimeContract):
    """Pure transition validation with no configuration-write capability."""

    capability: RolloutCapability
    current_mode: RolloutMode
    requested_mode: RolloutMode
    outcome: RolloutTransitionOutcome


def validate_transition(
    *,
    capability: RolloutCapability,
    current_mode: RolloutMode,
    requested_mode: RolloutMode,
    soak_decision: RolloutSoakDecision | None = None,
) -> RolloutTransitionDecision:
    """Validate D6 promotion gates and D8's safe, asymmetric rollback.

    An enforce promotion must advance from shadow and use a successful soak
    evaluation for the same capability.  Any lower-authority mode is a safe
    rollback decision only: it cannot resume legacy paths or dispatch work.
    """

    if current_mode is requested_mode:
        outcome = RolloutTransitionOutcome.NO_CHANGE
    elif requested_mode.rank < current_mode.rank:
        outcome = RolloutTransitionOutcome.ROLLBACK_ALLOWED
    elif requested_mode is RolloutMode.SHADOW:
        outcome = RolloutTransitionOutcome.ADVANCE_TO_SHADOW
    elif (
        current_mode is RolloutMode.SHADOW
        and soak_decision is not None
        and soak_decision.capability is capability
        and soak_decision.permits_enforce_promotion
    ):
        outcome = RolloutTransitionOutcome.PROMOTION_ALLOWED
    else:
        outcome = RolloutTransitionOutcome.REJECTED
    return RolloutTransitionDecision(
        capability=capability,
        current_mode=current_mode,
        requested_mode=requested_mode,
        outcome=outcome,
    )


class ProtectedRolloutDiagnostic(RuntimeContract):
    """Sampled diagnostic with a process-keyed digest and no raw identifiers."""

    kind: RolloutDiagnosticKind
    capability: RolloutCapability
    mode: RolloutMode
    outcome: str = Field(pattern=r"^[a-z_]{1,64}$")
    match_scope: CohortMatchScope | None = None
    sample_key_digest: str = Field(
        min_length=_FINGERPRINT_HEX_LENGTH, max_length=_FINGERPRINT_HEX_LENGTH
    )


class RolloutControlMetricsPort(Protocol):
    """Telemetry port restricted to closed, low-cardinality vocabularies."""

    def cohort_admission(
        self,
        *,
        capability: RolloutCapability,
        mode: RolloutMode,
        outcome: CohortAdmissionOutcome,
        match_scope: CohortMatchScope | None,
    ) -> None:
        """Record one cohort admission decision."""

    def transition(
        self,
        *,
        capability: RolloutCapability,
        current_mode: RolloutMode,
        requested_mode: RolloutMode,
        outcome: RolloutTransitionOutcome,
    ) -> None:
        """Record one promotion, rollback, or rejected transition decision."""

    def diagnostic_sampled(self, *, kind: RolloutDiagnosticKind) -> None:
        """Record retention of one protected diagnostic."""


class RolloutControlDiagnosticSink(Protocol):
    """A sink that can receive only the protected diagnostic schema."""

    def record(self, diagnostic: ProtectedRolloutDiagnostic) -> None:
        """Record a protected rollout diagnostic."""


class RolloutControlMetrics:
    """OpenTelemetry implementation using only fixed enum-derived labels."""

    def __init__(self) -> None:
        meter = metrics.get_meter(_METER_NAME)
        self._admissions = meter.create_counter(
            "surfaces_rollout_cohort_admissions_total",
            description="Bounded E2 rollout cohort admission outcomes.",
        )
        self._transitions = meter.create_counter(
            "surfaces_rollout_transition_decisions_total",
            description="Bounded E2 rollout promotion and rollback decisions.",
        )
        self._diagnostics = meter.create_counter(
            "surfaces_rollout_control_diagnostics_total",
            description="Sampled protected E2 rollout control diagnostics.",
        )

    def cohort_admission(
        self,
        *,
        capability: RolloutCapability,
        mode: RolloutMode,
        outcome: CohortAdmissionOutcome,
        match_scope: CohortMatchScope | None,
    ) -> None:
        self._admissions.add(
            1,
            {
                "capability": capability.value,
                "mode": mode.value,
                "outcome": outcome.value,
                "match_scope": match_scope.value if match_scope is not None else "none",
            },
        )

    def transition(
        self,
        *,
        capability: RolloutCapability,
        current_mode: RolloutMode,
        requested_mode: RolloutMode,
        outcome: RolloutTransitionOutcome,
    ) -> None:
        self._transitions.add(
            1,
            {
                "capability": capability.value,
                "current_mode": current_mode.value,
                "requested_mode": requested_mode.value,
                "outcome": outcome.value,
            },
        )

    def diagnostic_sampled(self, *, kind: RolloutDiagnosticKind) -> None:
        self._diagnostics.add(1, {"kind": kind.value})


class ProtectedRolloutDiagnosticLogSink:
    """Emit only already-redacted rollout diagnostics."""

    def record(self, diagnostic: ProtectedRolloutDiagnostic) -> None:
        _LOGGER.warning(
            "e2_rollout_control_diagnostic",
            extra={"rollout_control": diagnostic.model_dump(mode="json")},
        )


class RolloutControlObserver:
    """Fail-soft observer for decisions; it cannot influence those decisions."""

    def __init__(
        self,
        *,
        metrics_port: RolloutControlMetricsPort | None = None,
        diagnostic_sink: RolloutControlDiagnosticSink | None = None,
    ) -> None:
        self._metrics = metrics_port or RolloutControlMetrics()
        self._diagnostics = diagnostic_sink or ProtectedRolloutDiagnosticLogSink()

    def admission(
        self,
        decision: CohortAdmissionDecision,
        *,
        diagnostic_sample_key: str | None = None,
    ) -> None:
        """Observe an already-computed admission without retaining an identifier."""

        self._invoke(
            self._metrics.cohort_admission,
            capability=decision.capability,
            mode=decision.mode,
            outcome=decision.outcome,
            match_scope=decision.match_scope,
        )
        self._sample(
            kind=RolloutDiagnosticKind.COHORT_ADMISSION,
            capability=decision.capability,
            mode=decision.mode,
            outcome=decision.outcome.value,
            match_scope=decision.match_scope,
            sample_key=diagnostic_sample_key,
        )

    def transition(
        self,
        decision: RolloutTransitionDecision,
        *,
        diagnostic_sample_key: str | None = None,
    ) -> None:
        """Observe a promotion/rollback decision without applying it."""

        self._invoke(
            self._metrics.transition,
            capability=decision.capability,
            current_mode=decision.current_mode,
            requested_mode=decision.requested_mode,
            outcome=decision.outcome,
        )
        self._sample(
            kind=RolloutDiagnosticKind.TRANSITION,
            capability=decision.capability,
            mode=decision.requested_mode,
            outcome=decision.outcome.value,
            match_scope=None,
            sample_key=diagnostic_sample_key,
        )

    def _sample(
        self,
        *,
        kind: RolloutDiagnosticKind,
        capability: RolloutCapability,
        mode: RolloutMode,
        outcome: str,
        match_scope: CohortMatchScope | None,
        sample_key: str | None,
    ) -> None:
        if sample_key is None:
            return
        sample_key_digest = _protected_digest(sample_key)
        if int(sample_key_digest[-4:], 16) % _DIAGNOSTIC_SAMPLE_DENOMINATOR:
            return
        diagnostic = ProtectedRolloutDiagnostic(
            kind=kind,
            capability=capability,
            mode=mode,
            outcome=outcome,
            match_scope=match_scope,
            sample_key_digest=sample_key_digest,
        )
        self._invoke(self._metrics.diagnostic_sampled, kind=kind)
        self._invoke(self._diagnostics.record, diagnostic)

    @staticmethod
    def _invoke(method: object, /, *args: object, **kwargs: object) -> None:
        try:
            assert callable(method)
            method(*args, **kwargs)
        except asyncio.CancelledError:
            try:
                task = asyncio.current_task()
            except RuntimeError:
                task = None
            if task is not None and task.cancelling() > 0:
                raise
            _LOGGER.debug("e2_rollout_control_sink_cancelled")
        except Exception:
            _LOGGER.debug("e2_rollout_control_sink_failed")


def _protected_digest(value: str) -> str:
    """Return a process-keyed diagnostic correlation value, never raw input."""

    return hashlib.blake2b(
        value.encode("utf-8", errors="surrogatepass"),
        key=_PROCESS_FINGERPRINT_KEY,
        digest_size=_FINGERPRINT_HEX_LENGTH // 2,
    ).hexdigest()


def _require_aware(value: datetime) -> None:
    """Reject ambiguous time arithmetic rather than guessing a timezone."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise RolloutCohortConfigurationError(
            "rollout soak times must be timezone-aware"
        )


__all__ = (
    "CohortAdmissionDecision",
    "CohortAdmissionOutcome",
    "CohortMatchScope",
    "ProtectedRolloutDiagnostic",
    "ProtectedRolloutDiagnosticLogSink",
    "RolloutCohortConfigurationError",
    "RolloutCohortPolicy",
    "RolloutCohortRule",
    "RolloutControlDiagnosticSink",
    "RolloutControlMetrics",
    "RolloutControlMetricsPort",
    "RolloutControlObserver",
    "RolloutDiagnosticKind",
    "RolloutSoakDecision",
    "RolloutSoakMetrics",
    "RolloutSoakRequirements",
    "RolloutTransitionDecision",
    "RolloutTransitionOutcome",
    "SoakEvaluationOutcome",
    "VerifiedRolloutCohortFacts",
    "VerifiedRolloutCohortFactsProvider",
    "VerifiedRolloutCohortSubject",
    "evaluate_soak",
    "validate_transition",
)
