"""Low-cardinality, fail-soft Operation Gateway metrics."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from agent_runtime.capabilities.operations.contracts import (
    OperationEventEmitter,
    OperationMetricsPort,
    OperationOutcomePresenter,
    OperationPresentationOutcome,
)
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType

_LOGGER = logging.getLogger(__name__)
_METER_NAME = "agent_runtime.operation_gateway"


class _TelemetryFailurePolicy:
    """Distinguish a broken sink from cancellation of the owning task."""

    @staticmethod
    def task_is_cancelling() -> bool:
        try:
            task = asyncio.current_task()
        except RuntimeError:
            return False
        return task is not None and task.cancelling() > 0


@dataclass(frozen=True)
class FailSoftOperationEventEmitter:
    """Contain sink failures without swallowing cancellation of the run."""

    delegate: OperationEventEmitter

    @classmethod
    def wrap(
        cls,
        emitter: OperationEventEmitter,
    ) -> FailSoftOperationEventEmitter:
        if isinstance(emitter, cls):
            return emitter
        return cls(delegate=emitter)

    async def emit(
        self,
        event_type: LedgerEventType,
        payload: Mapping[str, object],
        summary: str | None = None,
    ) -> None:
        try:
            await self.delegate.emit(event_type, payload, summary)
        except asyncio.CancelledError:
            if _TelemetryFailurePolicy.task_is_cancelling():
                raise
            _LOGGER.debug("operation_gateway.event_emitter_failed")
        except Exception:
            _LOGGER.debug("operation_gateway.event_emitter_failed")


@dataclass(frozen=True)
class FailSoftOperationOutcomePresenter:
    """Keep a display-only presentation failure out of operation execution.

    A result is durable before the gateway invokes this port.  The wrapper
    preserves that execution invariant and prevents individual transport
    adapters from inventing their own best-effort ledger/surface branches.
    """

    delegate: OperationOutcomePresenter

    @classmethod
    def wrap(
        cls, presenter: OperationOutcomePresenter
    ) -> FailSoftOperationOutcomePresenter:
        if isinstance(presenter, cls):
            return presenter
        return cls(delegate=presenter)

    async def present(self, outcome: OperationPresentationOutcome) -> None:
        try:
            await self.delegate.present(outcome)
        except asyncio.CancelledError:
            if _TelemetryFailurePolicy.task_is_cancelling():
                raise
            _LOGGER.debug("operation_gateway.outcome_presentation_failed")
        except Exception:
            _LOGGER.debug("operation_gateway.outcome_presentation_failed")


@dataclass(frozen=True)
class FailSoftOperationMetrics:
    """Contain every injected metric sink failure at the context boundary."""

    delegate: OperationMetricsPort

    @classmethod
    def wrap(cls, metrics: OperationMetricsPort) -> FailSoftOperationMetrics:
        if isinstance(metrics, cls):
            return metrics
        return cls(delegate=metrics)

    def requested(self, *, capability: str, op: str, effect_class: str) -> None:
        self._invoke(
            self.delegate.requested,
            capability=capability,
            op=op,
            effect_class=effect_class,
        )

    def completed(
        self,
        *,
        capability: str,
        op: str,
        effect_class: str,
        outcome: str,
        latency_ms: int,
    ) -> None:
        self._invoke(
            self.delegate.completed,
            capability=capability,
            op=op,
            effect_class=effect_class,
            outcome=outcome,
            latency_ms=latency_ms,
        )

    def failed(
        self, *, capability: str, op: str, effect_class: str, failure_code: str
    ) -> None:
        self._invoke(
            self.delegate.failed,
            capability=capability,
            op=op,
            effect_class=effect_class,
            failure_code=failure_code,
        )

    def classification_mismatch(
        self,
        *,
        capability: str,
        op: str,
        legacy_class: str,
        gateway_class: str,
    ) -> None:
        self._invoke(
            self.delegate.classification_mismatch,
            capability=capability,
            op=op,
            legacy_class=legacy_class,
            gateway_class=gateway_class,
        )

    def disposition_mismatch(
        self,
        *,
        capability: str,
        op: str,
        legacy_disposition: str,
        gateway_disposition: str,
    ) -> None:
        self._invoke(
            self.delegate.disposition_mismatch,
            capability=capability,
            op=op,
            legacy_disposition=legacy_disposition,
            gateway_disposition=gateway_disposition,
        )

    def stage_required(self, *, capability: str, op: str, effect_class: str) -> None:
        self._invoke(
            self.delegate.stage_required,
            capability=capability,
            op=op,
            effect_class=effect_class,
        )

    def artifact_intent(self, *, capability: str, op: str) -> None:
        self._invoke(
            self.delegate.artifact_intent,
            capability=capability,
            op=op,
        )

    @staticmethod
    def _invoke(method: Callable[..., None], **values: object) -> None:
        try:
            method(**values)
        except asyncio.CancelledError:
            if _TelemetryFailurePolicy.task_is_cancelling():
                raise
            _LOGGER.debug("operation_gateway.metric_failed")
        except Exception:
            _LOGGER.debug("operation_gateway.metric_failed")


class OperationGatewayMetrics:
    """OTel facade whose labels are descriptor enum-ish values only."""

    def __init__(self) -> None:
        self._meter = self._build_meter()
        self._instruments: dict[str, Any] = {}

    @staticmethod
    def _build_meter() -> Any:
        try:
            from opentelemetry import metrics

            return metrics.get_meter(_METER_NAME)
        except Exception:  # pragma: no cover - optional SDK / defensive
            return None

    def requested(self, *, capability: str, op: str, effect_class: str) -> None:
        self._add(
            "operation_gateway_requests_total",
            {"capability": capability, "op": op, "effect_class": effect_class},
        )

    def completed(
        self,
        *,
        capability: str,
        op: str,
        effect_class: str,
        outcome: str,
        latency_ms: int,
    ) -> None:
        attrs = {
            "capability": capability,
            "op": op,
            "effect_class": effect_class,
            "outcome": outcome,
        }
        self._add("operation_gateway_completions_total", attrs)
        self._record(
            "operation_gateway_latency_ms",
            max(0, latency_ms),
            {
                "capability": capability,
                "op": op,
                "effect_class": effect_class,
            },
        )

    def failed(
        self, *, capability: str, op: str, effect_class: str, failure_code: str
    ) -> None:
        self._add(
            "operation_gateway_failures_total",
            {
                "capability": capability,
                "op": op,
                "effect_class": effect_class,
                "failure_code": failure_code,
            },
        )

    def classification_mismatch(
        self,
        *,
        capability: str,
        op: str,
        legacy_class: str,
        gateway_class: str,
    ) -> None:
        self._add(
            "operation_gateway_classification_mismatch_total",
            {
                "capability": capability,
                "op": op,
                "legacy_class": legacy_class,
                "gateway_class": gateway_class,
            },
        )

    def disposition_mismatch(
        self,
        *,
        capability: str,
        op: str,
        legacy_disposition: str,
        gateway_disposition: str,
    ) -> None:
        self._add(
            "operation_gateway_disposition_mismatch_total",
            {
                "capability": capability,
                "op": op,
                "legacy_disposition": legacy_disposition,
                "gateway_disposition": gateway_disposition,
            },
        )

    def stage_required(self, *, capability: str, op: str, effect_class: str) -> None:
        self._add(
            "operation_gateway_stage_required_total",
            {"capability": capability, "op": op, "effect_class": effect_class},
        )

    def artifact_intent(self, *, capability: str, op: str) -> None:
        self._add(
            "operation_gateway_artifact_intent_total",
            {"capability": capability, "op": op},
        )

    def _counter(self, name: str) -> Any:
        if self._meter is None:
            return None
        try:
            return self._instruments.setdefault(name, self._meter.create_counter(name))
        except Exception:
            return None

    def _histogram(self, name: str) -> Any:
        if self._meter is None:
            return None
        try:
            return self._instruments.setdefault(
                name, self._meter.create_histogram(name, unit="ms")
            )
        except Exception:
            return None

    def _add(self, name: str, attrs: dict[str, str]) -> None:
        instrument = self._counter(name)
        if instrument is None:
            return
        try:
            instrument.add(1, attrs)
        except Exception:
            _LOGGER.debug("operation_gateway.metric_failed")

    def _record(self, name: str, value: int, attrs: dict[str, str]) -> None:
        instrument = self._histogram(name)
        if instrument is None:
            return
        try:
            instrument.record(value, attrs)
        except Exception:
            _LOGGER.debug("operation_gateway.metric_failed")


__all__ = (
    "FailSoftOperationEventEmitter",
    "FailSoftOperationOutcomePresenter",
    "FailSoftOperationMetrics",
    "OperationGatewayMetrics",
)
