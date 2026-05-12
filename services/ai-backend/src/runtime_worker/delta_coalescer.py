"""Per-run buffer that coalesces ``MODEL_DELTA`` chunks into batched DB writes to reduce round-trips."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.execution.contracts import StreamEventSource
from runtime_api.schemas import RunRecord, RuntimeApiEventType, RuntimeEventEnvelope


@dataclass
class DeltaCoalescer:
    """Buffer ``MODEL_DELTA`` chunks and flush them as a single batched DB write when the window or chunk cap is reached."""

    producer: RuntimeEventProducer
    run: RunRecord
    window_ms: int = 0
    max_chunks: int = 64
    source: StreamEventSource = StreamEventSource.MODEL
    event_type: RuntimeApiEventType = RuntimeApiEventType.MODEL_DELTA
    _buffer: list[Mapping[str, object]] = field(default_factory=list)
    _first_added_at: float | None = None

    @property
    def coalescing_enabled(self) -> bool:
        """Return ``True`` when the coalesce window is positive (batching active)."""
        return self.window_ms > 0

    @property
    def pending(self) -> int:
        """Return the number of buffered chunks not yet flushed to the DB."""
        return len(self._buffer)

    async def add_delta(
        self,
        *,
        payload: Mapping[str, object],
        metadata: Mapping[str, object] | None = None,
        summary: str | None = None,
    ) -> None:
        """Buffer one delta. Flushes if window or max-chunks would exceed."""

        if not self.coalescing_enabled:
            await self.producer.append_api_event(
                run=self.run,
                source=self.source,
                event_type=self.event_type,
                payload=dict(payload),
                metadata=dict(metadata) if metadata is not None else None,
                summary=summary,
            )
            return

        entry: dict[str, object] = {"payload": dict(payload)}
        if metadata is not None:
            entry["metadata"] = dict(metadata)
        if summary is not None:
            entry["summary"] = summary
        self._buffer.append(entry)
        if self._first_added_at is None:
            self._first_added_at = time.monotonic()

        if len(self._buffer) >= self.max_chunks:
            await self.flush()
            return
        elapsed_ms = (time.monotonic() - self._first_added_at) * 1000
        if elapsed_ms >= self.window_ms:
            await self.flush()

    async def flush(self) -> Sequence[RuntimeEventEnvelope]:
        """Flush buffered deltas via the producer's batch path.

        Safe to call when the buffer is empty or coalescing is disabled —
        returns ``()`` in both cases. Always reset buffer state before
        awaiting so a re-entrant call (e.g. cancellation handler) sees an
        empty buffer.
        """

        if not self._buffer:
            return ()
        buffered = self._buffer
        self._buffer = []
        self._first_added_at = None
        return await self.producer.append_api_events_batch(
            run=self.run,
            source=self.source,
            event_type=self.event_type,
            entries=buffered,
        )

    async def __aenter__(self) -> "DeltaCoalescer":
        """Enter the async context manager."""
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        """Flush on exit, even on cancellation, so buffered chunks are never stranded."""
        await self.flush()
