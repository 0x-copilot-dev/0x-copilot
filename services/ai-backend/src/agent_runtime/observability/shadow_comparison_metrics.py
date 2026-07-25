"""Low-cardinality, fail-soft telemetry for E2 D2 shadow comparisons.

This module is deliberately a metrics/diagnostic *sink*, not a rollout or
execution seam.  It accepts only closed comparison vocabulary and protected
fingerprints; no tenant, user, run, connector, path, title, payload, proposal,
or exception text can become an attribute or structured-log value.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Final, Protocol

from opentelemetry import metrics

if TYPE_CHECKING:
    from agent_runtime.rollout_shadow import (
        ProtectedShadowDiagnostic,
        ShadowComparisonKind,
        ShadowComparisonOutcome,
    )
    from agent_runtime.rollout import RolloutCapability


_LOGGER = logging.getLogger(__name__)
_METER_NAME: Final = "agent_runtime.e2_shadow_comparison"


class ShadowComparisonMetricsPort(Protocol):
    """The only telemetry port the pure D2 comparator may use."""

    def comparison(
        self,
        *,
        kind: "ShadowComparisonKind",
        capability: "RolloutCapability",
        outcome: "ShadowComparisonOutcome",
    ) -> None:
        """Record one bounded comparison outcome."""

    def diagnostic_sampled(
        self,
        *,
        kind: "ShadowComparisonKind",
        capability: "RolloutCapability",
    ) -> None:
        """Record that a protected mismatch diagnostic was retained."""


class ShadowComparisonDiagnosticSink(Protocol):
    """A protected diagnostic sink: callers cannot pass raw comparison input."""

    def record(self, diagnostic: "ProtectedShadowDiagnostic") -> None:
        """Record one already-scrubbed diagnostic."""


class ShadowComparisonMetrics:
    """OpenTelemetry implementation with a closed label vocabulary by type."""

    def __init__(self) -> None:
        meter = metrics.get_meter(_METER_NAME)
        self._comparisons = meter.create_counter(
            "surfaces_rollout_shadow_comparisons_total",
            description="Bounded E2 shadow comparison outcomes.",
        )
        self._diagnostics = meter.create_counter(
            "surfaces_rollout_shadow_diagnostics_total",
            description="Sampled protected E2 shadow mismatch diagnostics.",
        )

    def comparison(
        self,
        *,
        kind: "ShadowComparisonKind",
        capability: "RolloutCapability",
        outcome: "ShadowComparisonOutcome",
    ) -> None:
        self._comparisons.add(
            1,
            {
                "kind": kind.value,
                "capability": capability.value,
                "outcome": outcome.value,
            },
        )

    def diagnostic_sampled(
        self,
        *,
        kind: "ShadowComparisonKind",
        capability: "RolloutCapability",
    ) -> None:
        self._diagnostics.add(
            1,
            {"kind": kind.value, "capability": capability.value},
        )


class ProtectedShadowDiagnosticLogSink:
    """Emit only pre-scrubbed fingerprints to the process-local protected log."""

    def record(self, diagnostic: "ProtectedShadowDiagnostic") -> None:
        _LOGGER.warning(
            "e2_shadow_comparison_mismatch",
            extra={"shadow_comparison": diagnostic.model_dump(mode="json")},
        )


class FailSoftShadowComparisonMetrics:
    """Contain metric and diagnostic sink failures without changing the caller."""

    def __init__(
        self,
        *,
        metrics_port: ShadowComparisonMetricsPort,
        diagnostic_sink: ShadowComparisonDiagnosticSink,
    ) -> None:
        self._metrics_port = metrics_port
        self._diagnostic_sink = diagnostic_sink

    def comparison(
        self,
        *,
        kind: "ShadowComparisonKind",
        capability: "RolloutCapability",
        outcome: "ShadowComparisonOutcome",
    ) -> None:
        self._invoke(
            self._metrics_port.comparison,
            kind=kind,
            capability=capability,
            outcome=outcome,
        )

    def diagnostic_sampled(
        self,
        *,
        kind: "ShadowComparisonKind",
        capability: "RolloutCapability",
        diagnostic: "ProtectedShadowDiagnostic",
    ) -> None:
        self._invoke(
            self._metrics_port.diagnostic_sampled,
            kind=kind,
            capability=capability,
        )
        self._invoke(self._diagnostic_sink.record, diagnostic)

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
            _LOGGER.debug("e2_shadow_comparison_sink_cancelled")
        except Exception:
            _LOGGER.debug("e2_shadow_comparison_sink_failed")


__all__ = (
    "FailSoftShadowComparisonMetrics",
    "ProtectedShadowDiagnosticLogSink",
    "ShadowComparisonDiagnosticSink",
    "ShadowComparisonMetrics",
    "ShadowComparisonMetricsPort",
)
