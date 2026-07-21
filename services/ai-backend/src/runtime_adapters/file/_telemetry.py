"""Structured logs + metrics for the file store's key operations.

Thin orchestration layer over the runtime observability seam: it drives the
:class:`~agent_runtime.observability.file_store_metrics.FileStoreMetrics` OTel
facade for counters/durations and emits PII-safe ``file_store.*`` structured
logs (the AC2 "Structured logs" contract) through stdlib logging — the same
``logging`` pipeline :class:`RuntimeLogger` wraps.

**Secret safety.** Nothing here logs payloads, tokens, search text, raw ids, or
physical paths. Conversation references are one-way hashed to a short digest
(:meth:`_safe_ref`); everything else is a count, size, duration, reason code, or
bounded outcome. Every call site is best-effort — a telemetry failure never
propagates into the store's read/write path.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager

from agent_runtime.observability.file_store_metrics import (
    FileStoreCorruptionKind,
    FileStoreMetrics,
    FileStoreOp,
    FileStoreOutcome,
    FileStoreRebuildTrigger,
)


class FileStoreEvent:
    """Canonical ``file_store.*`` structured-log event names (AC2 contract)."""

    OPENED = "file_store.opened"
    APPEND_COMMITTED = "file_store.append_committed"
    APPEND_FAILED = "file_store.append_failed"
    QUOTA_REJECTED = "file_store.quota_rejected"
    TORN_TAIL_IGNORED = "file_store.torn_tail_ignored"
    INTERIOR_CORRUPTION = "file_store.interior_corruption"
    INDEX_REBUILD_STARTED = "file_store.index_rebuild_started"
    INDEX_REBUILD_COMPLETED = "file_store.index_rebuild_completed"
    INDEX_REBUILD_FAILED = "file_store.index_rebuild_failed"
    CATALOG_DISCARDED = "file_store.catalog_discarded"
    DELETION_COMPLETED = "file_store.deletion_completed"
    OBJECTS_COLLECTED = "file_store.session_payloads_removed"
    RETENTION_SWEEP_COMPLETED = "file_store.retention_compaction_completed"
    STATE_COMPACTED = "file_store.state_ledger_compacted"


class FileStoreTelemetry:
    """Emit structured logs + metrics for one file store's operations.

    Holds a per-process :class:`FileStoreMetrics` and a module logger. Methods
    are named for the store operation they instrument; each is a single-line
    call at the write/read site.
    """

    _LOG_KEY = "file_store"

    def __init__(
        self,
        *,
        metrics: FileStoreMetrics | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._metrics = metrics or FileStoreMetrics()
        self._logger = logger or logging.getLogger("agent_runtime.file_store")

    # ----- helpers -------------------------------------------------------

    @staticmethod
    def _safe_ref(value: str | None) -> str | None:
        """One-way short digest of an id so logs carry no raw/logical id."""

        if not value:
            return None
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]

    def _emit(
        self,
        event: str,
        *,
        level: int = logging.INFO,
        **fields: object,
    ) -> None:
        # Only pre-vetted safe scalars ever reach here; still route through a
        # single choke point so the shape stays uniform and greppable.
        safe = {key: value for key, value in fields.items() if value is not None}
        try:
            self._logger.log(
                level, event, extra={self._LOG_KEY: {"event": event, **safe}}
            )
        except Exception:  # pragma: no cover - logging must never break the store
            pass

    # ----- lifecycle -----------------------------------------------------

    def store_opened(self, *, conversations: int, catalog_rebuilt: bool) -> None:
        self._emit(
            FileStoreEvent.OPENED,
            conversations=conversations,
            catalog_rebuilt=catalog_rebuilt,
        )
        self._metrics.record_op(op=FileStoreOp.OPEN, outcome=FileStoreOutcome.OK)

    def state_ledger_compacted(
        self, *, table: str, lines_before: int, lines_after: int
    ) -> None:
        """A back-office state ledger was folded to its live set at boot.

        ``table`` is a static table identifier (never an id/payload), so it is
        safe to log; the counts quantify the reclaimed history.
        """

        self._emit(
            FileStoreEvent.STATE_COMPACTED,
            table=table,
            lines_before=lines_before,
            lines_after=lines_after,
        )

    def catalog_discarded(self) -> None:
        """The disposable SQLite catalog was torn and discarded on connect()."""

        self._emit(FileStoreEvent.CATALOG_DISCARDED, level=logging.WARNING)
        self._metrics.record_op(
            op=FileStoreOp.CATALOG_OPEN, outcome=FileStoreOutcome.ERROR
        )
        self._metrics.record_failure(
            op=FileStoreOp.CATALOG_OPEN, reason="db_corrupt_discarded"
        )

    @contextmanager
    def index_rebuild(
        self, *, catalog_discarded: bool = False, records: int = 0
    ) -> Iterator[None]:
        """Time + log an index rebuild; count it (and any failure) on exit.

        ``catalog_discarded`` ties the rebuild counter to a torn-catalog cause
        (``trigger=catalog_discard``) vs. an ordinary boot (``trigger=open``).
        """

        trigger = (
            FileStoreRebuildTrigger.CATALOG_DISCARD
            if catalog_discarded
            else FileStoreRebuildTrigger.OPEN
        )
        self._emit(FileStoreEvent.INDEX_REBUILD_STARTED, trigger=trigger)
        started = time.perf_counter()
        try:
            yield
        except Exception:
            self._emit(
                FileStoreEvent.INDEX_REBUILD_FAILED,
                level=logging.ERROR,
                trigger=trigger,
            )
            self._metrics.record_op(
                op=FileStoreOp.INDEX_REBUILD, outcome=FileStoreOutcome.ERROR
            )
            self._metrics.record_failure(
                op=FileStoreOp.INDEX_REBUILD, reason="rebuild_error"
            )
            raise
        elapsed = time.perf_counter() - started
        self._emit(
            FileStoreEvent.INDEX_REBUILD_COMPLETED,
            trigger=trigger,
            records=records,
            duration_ms=int(elapsed * 1000),
        )
        self._metrics.record_op(
            op=FileStoreOp.INDEX_REBUILD, outcome=FileStoreOutcome.OK
        )
        self._metrics.record_index_rebuild(trigger=trigger, elapsed_seconds=elapsed)

    # ----- write path ----------------------------------------------------

    def append_committed(self, *, kind: str, size: int) -> None:
        """Count one committed record + its byte size (no per-append log)."""

        self._metrics.record_op(op=FileStoreOp.APPEND, outcome=FileStoreOutcome.OK)
        self._metrics.record_committed_bytes(kind=kind, size=size)

    def append_failed(self, *, kind: str, reason: str) -> None:
        self._emit(
            FileStoreEvent.APPEND_FAILED, level=logging.ERROR, kind=kind, reason=reason
        )
        self._metrics.record_op(op=FileStoreOp.APPEND, outcome=FileStoreOutcome.ERROR)
        self._metrics.record_failure(op=FileStoreOp.APPEND, reason=reason)

    def quota_rejected(self, *, incoming_bytes: int) -> None:
        """A write was refused before any byte landed (byte-ceiling hit)."""

        self._emit(
            FileStoreEvent.QUOTA_REJECTED,
            level=logging.WARNING,
            incoming_bytes=incoming_bytes,
        )
        self._metrics.record_quota_rejection()
        self._metrics.record_op(op=FileStoreOp.APPEND, outcome=FileStoreOutcome.ERROR)

    # ----- corruption ----------------------------------------------------

    def interior_corruption(
        self, *, conversation_id: str | None, line_number: int
    ) -> None:
        """A canonical stream failed closed on interior corruption."""

        self._emit(
            FileStoreEvent.INTERIOR_CORRUPTION,
            level=logging.ERROR,
            conversation_ref=self._safe_ref(conversation_id),
            line_number=line_number,
        )
        self._metrics.record_op(op=FileStoreOp.LOAD, outcome=FileStoreOutcome.ERROR)
        self._metrics.record_corruption(kind=FileStoreCorruptionKind.INTERIOR_CORRUPT)

    def torn_tail_ignored(self, *, conversation_id: str | None) -> None:
        self._emit(
            FileStoreEvent.TORN_TAIL_IGNORED,
            level=logging.WARNING,
            conversation_ref=self._safe_ref(conversation_id),
        )
        self._metrics.record_corruption(kind=FileStoreCorruptionKind.TORN_TAIL)

    # ----- deletion / retention -----------------------------------------

    def deletion_completed(
        self,
        *,
        conversations: int,
        objects_collected: int,
        trigger: str,
    ) -> None:
        self._emit(
            FileStoreEvent.DELETION_COMPLETED,
            conversations=conversations,
            objects_collected=objects_collected,
            trigger=trigger,
        )
        self._metrics.record_op(op=FileStoreOp.DELETE, outcome=FileStoreOutcome.OK)
        if objects_collected:
            self._emit(
                FileStoreEvent.OBJECTS_COLLECTED, objects_collected=objects_collected
            )
            self._metrics.record_op(
                op=FileStoreOp.OBJECT_GC, outcome=FileStoreOutcome.OK
            )
            self._metrics.record_objects_collected(count=objects_collected)

    def retention_sweep_completed(
        self, *, conversations: int, objects_collected: int, dry_run: bool
    ) -> None:
        self._emit(
            FileStoreEvent.RETENTION_SWEEP_COMPLETED,
            conversations=conversations,
            objects_collected=objects_collected,
            dry_run=dry_run,
        )
        self._metrics.record_op(
            op=FileStoreOp.RETENTION_SWEEP, outcome=FileStoreOutcome.OK
        )


__all__ = ("FileStoreTelemetry", "FileStoreEvent")
