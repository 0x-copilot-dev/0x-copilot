"""OTel meters for the C8 retention sweeper (Phase 1).

Two signals through the existing pipeline:

  - ``retention_swept_rows_total`` — counter, labels ``kind``, ``action``
    (``tombstone`` | ``delete``), ``dry_run``. Incremented once per
    non-zero sweep outcome chunk with the affected row count.
  - ``retention_sweep_duration_seconds`` — histogram, label ``kind``.
    Recorded once per (org, kind) sweep call.

Gracefully no-ops when OTel is not importable (dev / test without the SDK).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_METER_NAME = "agent_runtime.retention"
_SWEPT_ROWS_TOTAL = "retention_swept_rows_total"
_SWEEP_DURATION_SECONDS = "retention_sweep_duration_seconds"
_SWEEP_DURATION_BUCKETS = (0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0)


class RetentionMetrics:
    """Per-process meter facade for retention sweep signals.

    Constructed once by ``RetentionSweeperLoop`` at startup. All call
    sites are best-effort: a metric failure is logged at DEBUG and never
    propagated to the caller.
    """

    def __init__(self) -> None:
        self._meter = self._build_meter()
        self._swept_rows_total: Any | None = None
        self._sweep_duration_seconds: Any | None = None

    @staticmethod
    def _build_meter() -> Any:
        try:
            from opentelemetry import metrics as otel_metrics
        except ImportError:
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

    def _histogram(self, name: str, *, buckets: tuple[float, ...]) -> Any:
        if self._meter is None:
            return None
        try:
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

    def record_swept_rows(
        self,
        *,
        kind: str,
        action: str,
        count: int,
        dry_run: bool,
    ) -> None:
        """Increment ``retention_swept_rows_total`` by ``count``."""

        if count <= 0:
            return
        if self._swept_rows_total is None:
            self._swept_rows_total = self._counter(_SWEPT_ROWS_TOTAL)
        if self._swept_rows_total is None:
            return
        try:
            self._swept_rows_total.add(
                count,
                {"kind": kind, "action": action, "dry_run": str(dry_run).lower()},
            )
        except Exception:
            logger.debug("retention_metrics.swept_rows.record_failed", exc_info=True)

    def record_sweep_duration(self, *, kind: str, elapsed_seconds: float) -> None:
        """Observe ``retention_sweep_duration_seconds`` for one (org, kind) call."""

        if self._sweep_duration_seconds is None:
            self._sweep_duration_seconds = self._histogram(
                _SWEEP_DURATION_SECONDS, buckets=_SWEEP_DURATION_BUCKETS
            )
        if self._sweep_duration_seconds is None:
            return
        try:
            self._sweep_duration_seconds.record(elapsed_seconds, {"kind": kind})
        except Exception:
            logger.debug("retention_metrics.duration.record_failed", exc_info=True)
