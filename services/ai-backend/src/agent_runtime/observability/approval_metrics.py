"""OTel meters for two-stage approval forwarding (PR 1.4.1 Gap #9).

Exposes three signals through the existing OTel pipeline (configured by
C11 in :mod:`agent_runtime.observability.otel`):

  - ``approval_forward_total`` — counter, labels ``decision_kind`` +
    ``depth``. Increments once per successful forward.
  - ``approval_forward_invalid_total`` — counter, label ``reason``.
    Increments on every guard rejection inside ``_guard_forwardable``
    so dashboards can split self-forward / cross-org / depth-cap /
    membership-invalid without parsing logs.
  - ``approval_chain_resolution_seconds`` — histogram. Recorded on the
    leaf decision when the parent chain is non-empty; bucket layout
    chosen so 30s / 1min / 5min / 30min / 1h / 1d are the natural read
    points on a Grafana board.

DRY anchors: these meters publish through the same
``opentelemetry.metrics.get_meter`` pipeline as
``db_statement_metrics`` and ``run_metrics``. No new exporter, no
parallel dashboard primitive. The module gracefully no-ops when OTel
isn't importable (dev/test) so callers don't have to guard.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


_METER_NAME = "agent_runtime.approvals"
_FORWARD_TOTAL = "approval_forward_total"
_FORWARD_INVALID_TOTAL = "approval_forward_invalid_total"
_CHAIN_RESOLUTION_SECONDS = "approval_chain_resolution_seconds"
_CHAIN_RESOLUTION_BUCKETS = (30, 60, 300, 1800, 3600, 86400)


class ApprovalMetrics:
    """Per-process meter facade for forward-related signals.

    Constructed once by ``RuntimeApiService`` (and by the worker handler
    when present). All call sites are best-effort: a failure to publish
    a metric is logged at DEBUG and never propagated.
    """

    def __init__(self) -> None:
        self._meter = self._build_meter()
        self._forward_total: Any | None = None
        self._forward_invalid_total: Any | None = None
        self._chain_resolution_seconds: Any | None = None

    @staticmethod
    def _build_meter() -> Any:
        try:
            from opentelemetry import metrics as otel_metrics
        except ImportError:  # pragma: no cover - optional dep
            return None
        try:
            return otel_metrics.get_meter(_METER_NAME)
        except Exception:  # pragma: no cover - defensive
            return None

    def _counter(self, name: str) -> Any:
        if self._meter is None:
            return None
        try:
            return self._meter.create_counter(name)
        except Exception:  # pragma: no cover - defensive
            return None

    def _histogram(self, name: str, *, buckets: tuple[int, ...]) -> Any:
        if self._meter is None:
            return None
        try:
            # ``explicit_bucket_boundaries_advisory`` is the OTel-Python
            # name for explicit buckets in v1.x; older versions ignore
            # the kwarg and fall back to default buckets, which is
            # fine for v1 of these signals.
            return self._meter.create_histogram(
                name,
                explicit_bucket_boundaries_advisory=list(buckets),
            )
        except TypeError:
            try:
                return self._meter.create_histogram(name)
            except Exception:  # pragma: no cover - defensive
                return None
        except Exception:  # pragma: no cover - defensive
            return None

    def record_forward_success(self, *, approval_kind: str | None, depth: int) -> None:
        if self._forward_total is None:
            self._forward_total = self._counter(_FORWARD_TOTAL)
        if self._forward_total is None:
            return
        try:
            self._forward_total.add(
                1,
                {
                    "decision_kind": approval_kind or "unknown",
                    "depth": str(depth),
                },
            )
        except Exception:
            logger.debug("approval_metrics.forward_total.record_failed", exc_info=True)

    def record_forward_invalid(self, *, reason: str) -> None:
        if self._forward_invalid_total is None:
            self._forward_invalid_total = self._counter(_FORWARD_INVALID_TOTAL)
        if self._forward_invalid_total is None:
            return
        try:
            self._forward_invalid_total.add(1, {"reason": reason})
        except Exception:
            logger.debug(
                "approval_metrics.forward_invalid.record_failed", exc_info=True
            )

    def record_chain_resolution_seconds(self, *, elapsed_seconds: float) -> None:
        if self._chain_resolution_seconds is None:
            self._chain_resolution_seconds = self._histogram(
                _CHAIN_RESOLUTION_SECONDS,
                buckets=_CHAIN_RESOLUTION_BUCKETS,
            )
        if self._chain_resolution_seconds is None:
            return
        try:
            self._chain_resolution_seconds.record(elapsed_seconds)
        except Exception:
            logger.debug(
                "approval_metrics.chain_resolution.record_failed", exc_info=True
            )


# Pre-defined reason codes for ``record_forward_invalid``. Keeping them
# string constants avoids label-cardinality drift across call sites.
class ForwardInvalidReason:
    NOT_PENDING = "not_pending"
    KIND_NOT_SUPPORTED = "kind_not_supported"
    TARGET_INVALID = "target_invalid"
    CHAIN_TOO_DEEP = "chain_too_deep"
    SELF_FORWARD = "self_forward"
    RESOLVER_UNAVAILABLE = "resolver_unavailable"
