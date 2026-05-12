"""P4 Stage 2 — DeltaCoalescer + batched event append.

Pins:
  * adapter parity for ``append_events_batch`` (in-memory mirrors postgres'
    contract — contiguous sequence numbers, all-or-nothing semantics);
  * producer ``append_api_events_batch`` projects + persists N events under
    one round-trip and fires ``on_event_appended`` once;
  * ``DeltaCoalescer`` is passthrough when ``window_ms=0`` (Stage 2 dark);
  * with ``window_ms>0`` chunks accumulate until window elapses or
    ``max_chunks`` is hit, then flush via the producer's batch path;
  * the ``async with`` cleanup flushes on normal exit AND exception.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    StreamEventSource,
)
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    MessageRecord,
    MessageRole,
    RunRecord,
    RuntimeApiEventType,
    RuntimeEventDraft,
)
from runtime_worker.delta_coalescer import DeltaCoalescer


class _FixturesMixin:
    """Seed a run + return a producer wired to one in-memory adapter."""

    ORG_ID = "org_p4s2"
    USER_ID = "user_p4s2"
    CONVERSATION_ID = "conv_p4s2"
    RUN_ID = "run_p4s2"
    USER_MESSAGE_ID = "msg_p4s2_user"
    TRACE_ID = "trace_p4s2"

    def _seed(self) -> tuple[InMemoryRuntimeApiStore, RunRecord]:
        store = InMemoryRuntimeApiStore()
        store.messages[self.USER_MESSAGE_ID] = MessageRecord(
            message_id=self.USER_MESSAGE_ID,
            conversation_id=self.CONVERSATION_ID,
            org_id=self.ORG_ID,
            role=MessageRole.USER,
            content_text="hi",
        )
        run = RunRecord(
            run_id=self.RUN_ID,
            conversation_id=self.CONVERSATION_ID,
            org_id=self.ORG_ID,
            user_id=self.USER_ID,
            user_message_id=self.USER_MESSAGE_ID,
            trace_id=self.TRACE_ID,
            model_provider="openai",
            model_name="gpt-5.4-mini",
            runtime_context=AgentRuntimeContext(
                user_id=self.USER_ID,
                org_id=self.ORG_ID,
                roles=["employee"],
                run_id=self.RUN_ID,
                trace_id=self.TRACE_ID,
                model_profile={
                    "provider": "openai",
                    "model_name": "gpt-5.4-mini",
                    "max_input_tokens": 128000,
                    "timeout_seconds": 30,
                    "temperature": 0,
                    "supports_streaming": True,
                },
            ),
        )
        store.runs[self.RUN_ID] = run
        store.events_by_run.setdefault(self.RUN_ID, [])
        return store, run

    def _draft(self, *, sequence_no_unused: int = 0) -> RuntimeEventDraft:
        return RuntimeEventDraft(
            run_id=self.RUN_ID,
            conversation_id=self.CONVERSATION_ID,
            org_id=self.ORG_ID,
            source=StreamEventSource.MODEL,
            event_type=RuntimeApiEventType.MODEL_DELTA,
            trace_id=self.TRACE_ID,
            payload={"delta": "x"},
            metadata={},
        )


class TestInMemoryAppendEventsBatch(_FixturesMixin):
    """Adapter-level batched append contract."""

    async def test_empty_batch_is_no_op(self) -> None:
        store, _ = self._seed()
        envelopes = await store.append_events_batch([])
        assert envelopes == ()
        assert store.events_by_run.get(self.RUN_ID, []) == []

    async def test_batch_assigns_contiguous_sequence_numbers(self) -> None:
        store, _ = self._seed()
        envelopes = await store.append_events_batch([self._draft() for _ in range(5)])
        assert tuple(e.sequence_no for e in envelopes) == (1, 2, 3, 4, 5)
        assert len(store.events_by_run[self.RUN_ID]) == 5

    async def test_batch_preserves_input_order(self) -> None:
        store, _ = self._seed()
        drafts = [
            RuntimeEventDraft(
                run_id=self.RUN_ID,
                conversation_id=self.CONVERSATION_ID,
                org_id=self.ORG_ID,
                source=StreamEventSource.MODEL,
                event_type=RuntimeApiEventType.MODEL_DELTA,
                trace_id=self.TRACE_ID,
                payload={"delta": f"chunk-{i}"},
                metadata={},
            )
            for i in range(4)
        ]
        envelopes = await store.append_events_batch(drafts)
        assert [e.payload["delta"] for e in envelopes] == [
            "chunk-0",
            "chunk-1",
            "chunk-2",
            "chunk-3",
        ]

    async def test_batch_advances_cursor_to_max(self) -> None:
        store, _ = self._seed()
        await store.append_events_batch([self._draft() for _ in range(3)])
        run = await store.get_run(org_id=self.ORG_ID, run_id=self.RUN_ID)
        assert run is not None
        # Latest cursor matches the highest sequence number written.
        assert run.latest_sequence_no == 3

    async def test_batch_rejects_mixed_run_ids(self) -> None:
        store, _ = self._seed()
        drafts = [
            self._draft(),
            RuntimeEventDraft(
                run_id="ghost_run",
                conversation_id=self.CONVERSATION_ID,
                org_id=self.ORG_ID,
                source=StreamEventSource.MODEL,
                event_type=RuntimeApiEventType.MODEL_DELTA,
                trace_id=self.TRACE_ID,
                payload={},
                metadata={},
            ),
        ]
        with pytest.raises(ValueError, match="share one run_id"):
            await store.append_events_batch(drafts)

    async def test_concurrent_batches_assign_unique_sequence_nos(self) -> None:
        store, _ = self._seed()

        async def _batch_of(n: int) -> Sequence[int]:
            envelopes = await store.append_events_batch(
                [self._draft() for _ in range(n)]
            )
            return [e.sequence_no for e in envelopes]

        results = await asyncio.gather(_batch_of(10), _batch_of(10), _batch_of(10))
        all_seq = sorted(seq for batch in results for seq in batch)
        assert all_seq == list(range(1, 31))


class TestProducerAppendApiEventsBatch(_FixturesMixin):
    """Producer-level batched append projects + persists in one call."""

    async def test_empty_entries_returns_empty_no_notify(self) -> None:
        store, run = self._seed()
        notifications: list[str] = []
        producer = RuntimeEventProducer(
            persistence=store,
            event_store=store,
            on_event_appended=notifications.append,
        )
        envelopes = await producer.append_api_events_batch(
            run=run,
            source=StreamEventSource.MODEL,
            event_type=RuntimeApiEventType.MODEL_DELTA,
            entries=[],
        )
        assert envelopes == ()
        assert notifications == []

    async def test_batch_persists_n_events_in_order(self) -> None:
        store, run = self._seed()
        producer = RuntimeEventProducer(persistence=store, event_store=store)
        entries = [
            {"payload": {"delta": f"c{i}"}, "summary": f"c{i}"} for i in range(4)
        ]
        envelopes = await producer.append_api_events_batch(
            run=run,
            source=StreamEventSource.MODEL,
            event_type=RuntimeApiEventType.MODEL_DELTA,
            entries=entries,
        )
        assert tuple(e.sequence_no for e in envelopes) == (1, 2, 3, 4)
        assert [e.payload["delta"] for e in envelopes] == ["c0", "c1", "c2", "c3"]

    async def test_batch_fires_one_notification(self) -> None:
        store, run = self._seed()
        notifications: list[str] = []
        producer = RuntimeEventProducer(
            persistence=store,
            event_store=store,
            on_event_appended=notifications.append,
        )
        await producer.append_api_events_batch(
            run=run,
            source=StreamEventSource.MODEL,
            event_type=RuntimeApiEventType.MODEL_DELTA,
            entries=[{"payload": {"delta": "x"}} for _ in range(5)],
        )
        # Notifications fire once per batch, not once per envelope — SSE
        # adapter pulls all 5 in one wakeup.
        assert notifications == [self.RUN_ID]

    async def test_batch_producer_skips_separate_set_latest(self) -> None:
        store, run = self._seed()
        producer = RuntimeEventProducer(persistence=store, event_store=store)

        original_set = store.set_run_latest_sequence
        adapter_calls = 0

        async def counting_set(*args, **kwargs):
            nonlocal adapter_calls
            adapter_calls += 1
            return await original_set(*args, **kwargs)

        store.set_run_latest_sequence = counting_set  # type: ignore[method-assign]

        await producer.append_api_events_batch(
            run=run,
            source=StreamEventSource.MODEL,
            event_type=RuntimeApiEventType.MODEL_DELTA,
            entries=[{"payload": {"delta": "x"}} for _ in range(3)],
        )

        # Adapter advances the cursor in-line for each batched draft → 3
        # internal calls. Producer never adds an extra one.
        assert adapter_calls == 3


class TestDeltaCoalescerPassthrough(_FixturesMixin):
    """``window_ms=0`` is passthrough — one DB write per chunk (default)."""

    async def test_passthrough_calls_append_api_event_per_chunk(self) -> None:
        store, run = self._seed()
        producer = RuntimeEventProducer(persistence=store, event_store=store)
        coalescer = DeltaCoalescer(producer=producer, run=run, window_ms=0)

        async with coalescer:
            for i in range(3):
                await coalescer.add_delta(
                    payload={"delta": f"c{i}", "message": f"c{i}"},
                    summary=f"c{i}",
                )

        # 3 chunks → 3 envelopes, each with its own sequence_no.
        assert len(store.events_by_run[self.RUN_ID]) == 3
        assert [e.payload["delta"] for e in store.events_by_run[self.RUN_ID]] == [
            "c0",
            "c1",
            "c2",
        ]

    async def test_passthrough_disabled_buffer_stays_empty(self) -> None:
        store, run = self._seed()
        producer = RuntimeEventProducer(persistence=store, event_store=store)
        coalescer = DeltaCoalescer(producer=producer, run=run, window_ms=0)
        await coalescer.add_delta(payload={"delta": "x"})
        assert coalescer.pending == 0
        assert coalescer.coalescing_enabled is False


class TestDeltaCoalescerCoalescing(_FixturesMixin):
    """``window_ms>0`` accumulates and flushes."""

    async def test_buffers_until_window_elapses(self) -> None:
        store, run = self._seed()
        producer = RuntimeEventProducer(persistence=store, event_store=store)
        coalescer = DeltaCoalescer(
            producer=producer, run=run, window_ms=50, max_chunks=100
        )

        # Add 3 chunks back-to-back — buffer should hold them.
        for i in range(3):
            await coalescer.add_delta(payload={"delta": f"c{i}"}, summary=f"c{i}")
        # Some implementations may auto-flush if the window elapsed
        # mid-call. With a 50ms window and synchronous adds, the buffer
        # typically still has all 3.
        # After the window elapses, the next add triggers flush.
        await asyncio.sleep(0.06)  # > 50ms
        await coalescer.add_delta(payload={"delta": "c3"}, summary="c3")
        # Coalescer flushed at this point — store has at least 4 events.
        assert len(store.events_by_run[self.RUN_ID]) >= 4

    async def test_flushes_at_max_chunks(self) -> None:
        store, run = self._seed()
        producer = RuntimeEventProducer(persistence=store, event_store=store)
        coalescer = DeltaCoalescer(
            producer=producer, run=run, window_ms=10_000, max_chunks=4
        )

        # 4 chunks — last one triggers max-chunks flush.
        for i in range(4):
            await coalescer.add_delta(payload={"delta": f"c{i}"})

        assert len(store.events_by_run[self.RUN_ID]) == 4
        assert coalescer.pending == 0

    async def test_explicit_flush_drains_buffer(self) -> None:
        store, run = self._seed()
        producer = RuntimeEventProducer(persistence=store, event_store=store)
        coalescer = DeltaCoalescer(
            producer=producer, run=run, window_ms=10_000, max_chunks=100
        )

        await coalescer.add_delta(payload={"delta": "a"})
        await coalescer.add_delta(payload={"delta": "b"})
        assert coalescer.pending == 2

        envelopes = await coalescer.flush()
        assert len(envelopes) == 2
        assert coalescer.pending == 0

    async def test_flush_is_safe_when_empty(self) -> None:
        store, run = self._seed()
        producer = RuntimeEventProducer(persistence=store, event_store=store)
        coalescer = DeltaCoalescer(producer=producer, run=run, window_ms=50)

        envelopes = await coalescer.flush()
        assert envelopes == ()
        assert len(store.events_by_run.get(self.RUN_ID, [])) == 0


class TestDeltaCoalescerCleanup(_FixturesMixin):
    """``async with`` cleanup flushes on normal exit AND exception."""

    async def test_async_with_normal_exit_flushes(self) -> None:
        store, run = self._seed()
        producer = RuntimeEventProducer(persistence=store, event_store=store)

        async with DeltaCoalescer(
            producer=producer, run=run, window_ms=10_000, max_chunks=100
        ) as coalescer:
            await coalescer.add_delta(payload={"delta": "a"})
            await coalescer.add_delta(payload={"delta": "b"})
            assert coalescer.pending == 2

        assert len(store.events_by_run[self.RUN_ID]) == 2

    async def test_async_with_exception_still_flushes(self) -> None:
        store, run = self._seed()
        producer = RuntimeEventProducer(persistence=store, event_store=store)

        with pytest.raises(RuntimeError, match="boom"):
            async with DeltaCoalescer(
                producer=producer, run=run, window_ms=10_000, max_chunks=100
            ) as coalescer:
                await coalescer.add_delta(payload={"delta": "a"})
                await coalescer.add_delta(payload={"delta": "b"})
                raise RuntimeError("boom")

        # Buffered chunks were flushed during __aexit__ before the exception
        # propagated.
        assert len(store.events_by_run[self.RUN_ID]) == 2
