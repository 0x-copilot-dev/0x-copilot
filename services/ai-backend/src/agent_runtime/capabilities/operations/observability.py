"""Low-cardinality, fail-soft Operation Gateway metrics."""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)
_METER_NAME = "agent_runtime.operation_gateway"


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
            _LOGGER.debug("operation_gateway.metric_failed", exc_info=True)

    def _record(self, name: str, value: int, attrs: dict[str, str]) -> None:
        instrument = self._histogram(name)
        if instrument is None:
            return
        try:
            instrument.record(value, attrs)
        except Exception:
            _LOGGER.debug("operation_gateway.metric_failed", exc_info=True)


__all__ = ("OperationGatewayMetrics",)
