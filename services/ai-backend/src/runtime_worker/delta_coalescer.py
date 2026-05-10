"""Per-run buffer that coalesces ``MODEL_DELTA`` chunks (P4 Stage 2).

The streaming executor used to call ``RuntimeEventProducer.append_api_event``
once per provider chunk. A long completion produces 100+ chunks in quick
succession; each was its own DB round-trip. This buffer accumulates the
chunks for a configurable window and flushes them as one batched append via
``RuntimeEventProducer.append_api_events_batch`` (one transaction, one
multi-row INSERT in Postgres).

Rules:
  * Coalescing is opt-in per ``RuntimeExecutionSettings.delta_coalesce_window_ms``.
    Default is 0 (passthrough — every chunk is appended individually,
    matching pre-Stage-2 behavior). Stage 2 ships dark.
  * One delta per envelope is preserved on the wire — the buffer batches
    the *DB write*, not the SSE frame. SSE clients still see one
    ``MODEL_DELTA`` envelope per chunk and resume / replay semantics are
    unchanged.
  * The streaming executor explicitly flushes before any non-``MODEL_DELTA``
    event so envelope ordering is preserved (a TOOL_CALL never lands ahead
    of the deltas that preceded it).
  * The executor must call ``flush()`` from a ``finally`` block so a
    cancelled or failing stream never strands buffered chunks.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.execution.contracts import StreamEventSource
from runtime_api.schemas import RunRecord, RuntimeApiEventType, RuntimeEventEnvelope


@dataclass
class DeltaCoalescer:
    """Buffer ``MODEL_DELTA`` chunks for a coalesce window."""

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
        return self.window_ms > 0

    @property
    def pending(self) -> int:
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
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        # Always flush — even on cancellation / exception. Buffered
        # chunks lost to a worker crash are no worse than the model
        # output that was being streamed when the worker died.
        await self.flush()
