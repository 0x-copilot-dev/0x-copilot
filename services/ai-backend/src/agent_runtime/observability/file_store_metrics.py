"""OTel meters for the file-native runtime store.

Publishes through the same ``opentelemetry.metrics.get_meter`` pipeline as the
other runtime meters (``retention_metrics``, ``approval_metrics``,
``db_statement_metrics``) so the file store reuses the one metrics seam rather
than inventing a parallel one. Gracefully no-ops when OTel is not importable
(dev/test without the SDK), so every call site is guard-free.

**Label discipline (secret-safe):** every label is a bounded, low-cardinality
enum-ish token — never a conversation / run / user id, path, file name, or any
byte of user content. Cardinality-unbounded values (ids, sizes) are recorded as
metric *values*, never labels.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_METER_NAME = "agent_runtime.file_store"

_OPS_TOTAL = "file_store_ops_total"
_FAILURES_TOTAL = "file_store_failures_total"
_QUOTA_REJECTIONS_TOTAL = "file_store_quota_rejections_total"
_CORRUPTION_TOTAL = "file_store_corruption_total"
_INDEX_REBUILDS_TOTAL = "file_store_index_rebuilds_total"
_OBJECTS_COLLECTED_TOTAL = "file_store_objects_collected_total"
_COMMITTED_BYTES_TOTAL = "file_store_committed_bytes_total"
_INDEX_REBUILD_DURATION_SECONDS = "file_store_index_rebuild_duration_seconds"
_INDEX_REBUILD_BUCKETS = (0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 30.0)


class FileStoreOp:
    """Bounded ``op`` label values (low cardinality by construction)."""

    OPEN = "open"
    APPEND = "append"
    LOAD = "load"
    LIST = "list"
    DELETE = "delete"
    OBJECT_GC = "object_gc"
    RETENTION_SWEEP = "retention_sweep"
    INDEX_REBUILD = "index_rebuild"
    CATALOG_OPEN = "catalog_open"


class FileStoreOutcome:
    """Bounded ``outcome`` label values."""

    OK = "ok"
    ERROR = "error"


class FileStoreCorruptionKind:
    """Bounded ``kind`` label values for corruption events.

    Mirrors :class:`runtime_adapters.file.repair.JsonlLineKind` verbatim so the
    metric label vocabulary and the repair diagnosis vocabulary never drift.
    """

    TORN_TAIL = "torn_tail"
    INTERIOR_CORRUPT = "interior_corrupt"


class FileStoreRebuildTrigger:
    """Bounded ``trigger`` label values for index rebuilds."""

    OPEN = "open"
    CATALOG_DISCARD = "catalog_discard"
    REPAIR = "repair"


class FileStoreMetrics:
    """Per-process meter facade for file-store signals.

    Constructed once by ``FileRuntimeApiStore`` (via ``FileStoreTelemetry``).
    Every call site is best-effort: a metric failure is logged at DEBUG and is
    never propagated to the store's write/read path.
    """

    def __init__(self) -> None:
        self._meter = self._build_meter()
        self._ops_total: Any | None = None
        self._failures_total: Any | None = None
        self._quota_rejections_total: Any | None = None
        self._corruption_total: Any | None = None
        self._index_rebuilds_total: Any | None = None
        self._objects_collected_total: Any | None = None
        self._committed_bytes_total: Any | None = None
        self._index_rebuild_seconds: Any | None = None

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

    def record_op(self, *, op: str, outcome: str = FileStoreOutcome.OK) -> None:
        """Increment ``file_store_ops_total`` for one completed operation."""

        if self._ops_total is None:
            self._ops_total = self._counter(_OPS_TOTAL)
        if self._ops_total is None:
            return
        try:
            self._ops_total.add(1, {"op": op, "outcome": outcome})
        except Exception:
            logger.debug("file_store_metrics.ops.record_failed", exc_info=True)

    def record_failure(self, *, op: str, reason: str) -> None:
        """Increment ``file_store_failures_total`` for one failed operation."""

        if self._failures_total is None:
            self._failures_total = self._counter(_FAILURES_TOTAL)
        if self._failures_total is None:
            return
        try:
            self._failures_total.add(1, {"op": op, "reason": reason})
        except Exception:
            logger.debug("file_store_metrics.failures.record_failed", exc_info=True)

    def record_quota_rejection(self) -> None:
        """Increment ``file_store_quota_rejections_total``."""

        if self._quota_rejections_total is None:
            self._quota_rejections_total = self._counter(_QUOTA_REJECTIONS_TOTAL)
        if self._quota_rejections_total is None:
            return
        try:
            self._quota_rejections_total.add(1)
        except Exception:
            logger.debug("file_store_metrics.quota.record_failed", exc_info=True)

    def record_corruption(self, *, kind: str) -> None:
        """Increment ``file_store_corruption_total`` for one corruption event."""

        if self._corruption_total is None:
            self._corruption_total = self._counter(_CORRUPTION_TOTAL)
        if self._corruption_total is None:
            return
        try:
            self._corruption_total.add(1, {"kind": kind})
        except Exception:
            logger.debug("file_store_metrics.corruption.record_failed", exc_info=True)

    def record_index_rebuild(
        self, *, trigger: str, elapsed_seconds: float | None = None
    ) -> None:
        """Count one index rebuild and (optionally) observe its duration."""

        if self._index_rebuilds_total is None:
            self._index_rebuilds_total = self._counter(_INDEX_REBUILDS_TOTAL)
        if self._index_rebuilds_total is not None:
            try:
                self._index_rebuilds_total.add(1, {"trigger": trigger})
            except Exception:
                logger.debug("file_store_metrics.rebuild.record_failed", exc_info=True)
        if elapsed_seconds is None:
            return
        if self._index_rebuild_seconds is None:
            self._index_rebuild_seconds = self._histogram(
                _INDEX_REBUILD_DURATION_SECONDS, buckets=_INDEX_REBUILD_BUCKETS
            )
        if self._index_rebuild_seconds is None:
            return
        try:
            self._index_rebuild_seconds.record(elapsed_seconds, {"trigger": trigger})
        except Exception:
            logger.debug(
                "file_store_metrics.rebuild_duration.record_failed", exc_info=True
            )

    def record_objects_collected(self, *, count: int) -> None:
        """Increment ``file_store_objects_collected_total`` by ``count``."""

        if count <= 0:
            return
        if self._objects_collected_total is None:
            self._objects_collected_total = self._counter(_OBJECTS_COLLECTED_TOTAL)
        if self._objects_collected_total is None:
            return
        try:
            self._objects_collected_total.add(count)
        except Exception:
            logger.debug("file_store_metrics.objects.record_failed", exc_info=True)

    def record_committed_bytes(self, *, kind: str, size: int) -> None:
        """Increment ``file_store_committed_bytes_total`` by ``size``."""

        if size <= 0:
            return
        if self._committed_bytes_total is None:
            self._committed_bytes_total = self._counter(_COMMITTED_BYTES_TOTAL)
        if self._committed_bytes_total is None:
            return
        try:
            self._committed_bytes_total.add(size, {"kind": kind})
        except Exception:
            logger.debug("file_store_metrics.bytes.record_failed", exc_info=True)


__all__ = (
    "FileStoreMetrics",
    "FileStoreOp",
    "FileStoreOutcome",
    "FileStoreCorruptionKind",
    "FileStoreRebuildTrigger",
)
