"""Shared fakes for Universal Operation Gateway unit tests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import SimpleNamespace

from agent_runtime.capabilities.operations.context import (
    OperationContext,
    VerifiedOperationIdentity,
)
from agent_runtime.capabilities.operations.contracts import (
    GateResolution,
    OperationArgumentResolver,
    OperationClassification,
    OperationEventEmitter,
    OperationGatewayMode,
    OperationMetricsPort,
    OperationRawResult,
    OperationRequest,
    ProposedEffect,
)
from agent_runtime.capabilities.tools.permissions import ToolUsePolicySnapshot
from agent_runtime.surfaces_v2.ledger_models import (
    EffectClass,
    LedgerEventType,
)


@dataclass
class RecordingEmitter:
    events: list[tuple[LedgerEventType, dict[str, object], str | None]] = field(
        default_factory=list
    )
    fail_on_call: int | None = None
    # Fail whenever a specific event type is emitted, regardless of position.
    # Ordinal targeting is brittle for callers that only care about one emission
    # in a longer sequence — a gate emit arrives after the operation rows, and
    # its index shifts whenever the surrounding flow changes.
    fail_on_event_type: LedgerEventType | None = None
    calls: int = 0

    async def emit(
        self,
        event_type: LedgerEventType,
        payload: Mapping[str, object],
        summary: str | None = None,
    ) -> None:
        self.calls += 1
        if self.fail_on_call == self.calls or self.fail_on_event_type is event_type:
            raise RuntimeError("telemetry-secret-must-not-escape")
        self.events.append((event_type, dict(payload), summary))


@dataclass
class RecordingMetrics:
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)
    fail: bool = False

    def _record(self, name: str, values: dict[str, object]) -> None:
        if self.fail:
            raise RuntimeError("metric-secret-must-not-escape")
        self.calls.append((name, values))

    def requested(self, **values: object) -> None:
        self._record("requested", values)

    def completed(self, **values: object) -> None:
        self._record("completed", values)

    def failed(self, **values: object) -> None:
        self._record("failed", values)

    def classification_mismatch(self, **values: object) -> None:
        self._record("classification_mismatch", values)

    def disposition_mismatch(self, **values: object) -> None:
        self._record("disposition_mismatch", values)

    def stage_required(self, **values: object) -> None:
        self._record("stage_required", values)

    def artifact_intent(self, **values: object) -> None:
        self._record("artifact_intent", values)


@dataclass
class RecordingAdapter:
    raw: OperationRawResult = field(
        default_factory=lambda: OperationRawResult(
            result_ref="payload://result-1",
            safe_summary="Read completed.",
            activity_ref="activity://read-1",
        )
    )
    proposal: ProposedEffect | None = None
    read_calls: int = 0
    proposal_calls: int = 0
    apply_calls: int = 0
    failure: BaseException | None = None

    async def execute_read(self, request: OperationRequest) -> OperationRawResult:
        del request
        self.read_calls += 1
        if self.failure is not None:
            raise self.failure
        return self.raw

    async def build_proposal(self, request: OperationRequest) -> ProposedEffect:
        del request
        self.proposal_calls += 1
        if self.failure is not None:
            raise self.failure
        if self.proposal is None:
            raise RuntimeError("test proposal is missing")
        return self.proposal

    async def apply(self) -> None:
        """A trap: the gateway must never discover or call this method."""

        self.apply_calls += 1


@dataclass
class AllowingGate:
    calls: int = 0

    async def resolve(
        self,
        *,
        request: OperationRequest,
        descriptor: object,
        classification: OperationClassification,
    ) -> GateResolution:
        del request, descriptor, classification
        self.calls += 1
        return GateResolution(allowed=True)


@dataclass
class RecordingArtifactService:
    artifact_id: str
    calls: list[dict[str, object]] = field(default_factory=list)

    async def promote_source(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return SimpleNamespace(
            record=SimpleNamespace(
                artifact=SimpleNamespace(artifact_id=self.artifact_id)
            )
        )


class BoundContextMixin:
    @staticmethod
    def bind(
        *,
        emitter: OperationEventEmitter | None = None,
        metrics: OperationMetricsPort | None = None,
        artifact_service: object | None = None,
        mode: OperationGatewayMode = OperationGatewayMode.SHADOW,
        durable_arguments: bool = False,
        arguments: OperationArgumentResolver | None = None,
    ):
        return OperationContext.bind_for_run(
            identity=VerifiedOperationIdentity(
                org_id="org-1",
                user_id="user-1",
                conversation_id="conv-1",
                run_id="run-1",
            ),
            policy_snapshot=ToolUsePolicySnapshot.from_response(),
            ledger_emitter=emitter or RecordingEmitter(),
            artifact_service=artifact_service,  # type: ignore[arg-type]
            mode=mode,
            metrics=metrics or RecordingMetrics(),
            arguments=arguments,
            canonical_arguments_durable=durable_arguments,
        )


def effect_descriptor_values(effect: EffectClass) -> dict[str, object]:
    return {
        "capability": "test",
        "op": effect.value,
        "executor": "builtin",
        "effect_class": effect,
        "result_kind": "activity",
        "supports_prepare": effect
        not in {EffectClass.NONE, EffectClass.INTERNAL_REVERSIBLE},
        "supports_reconcile": False,
        "required_gate_kinds": (),
        "max_inline_result_bytes": 4096,
    }
