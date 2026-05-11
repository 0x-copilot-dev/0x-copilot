"""P4 — Stage 1 cursor consolidation tests for the in-memory adapter.

The Postgres adapter is exercised by integration tests that need a live
database; these unit tests pin the contract that both adapters must honor
(parity), the producer's auto-detection of the adapter mode, and the H3
monotonic guard on ``set_run_latest_sequence``.
"""

from __future__ import annotations

import asyncio

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


class _FixturesMixin:
    """Shared run/conversation seeding used across consolidation tests.

    Bypasses ``create_run_with_user_message`` and writes directly into the
    in-memory dicts the same way the existing test corpus does
    (e.g. ``test_approval_undo.py``). Keeps the consolidation tests focused
    on the cursor-write contract rather than the run-creation pipeline.
    """

    ORG_ID = "org_p4"
    USER_ID = "user_p4"
    CONVERSATION_ID = "conv_p4"
    RUN_ID = "run_p4"
    USER_MESSAGE_ID = "msg_p4_user"
    TRACE_ID = "trace_p4"

    def _seed_run(self, store: InMemoryRuntimeApiStore) -> RunRecord:
        store.messages[self.USER_MESSAGE_ID] = MessageRecord(
            message_id=self.USER_MESSAGE_ID,
            conversation_id=self.CONVERSATION_ID,
            org_id=self.ORG_ID,
            role=MessageRole.USER,
            content_text="hello",
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
        return run

    def _draft(
        self,
        *,
        run_id: str | None = None,
        conversation_id: str | None = None,
        event_type: RuntimeApiEventType = RuntimeApiEventType.MODEL_DELTA,
    ) -> RuntimeEventDraft:
        return RuntimeEventDraft(
            run_id=run_id or self.RUN_ID,
            conversation_id=conversation_id or self.CONVERSATION_ID,
            org_id=self.ORG_ID,
            source=StreamEventSource.MODEL,
            event_type=event_type,
            trace_id=self.TRACE_ID,
            payload={},
            metadata={},
        )


class TestInMemoryConsolidatedAppend(_FixturesMixin):
    """``append_event`` advances the run cursor when consolidation is enabled."""

    async def test_default_constructor_does_not_consolidate(self) -> None:
        """Existing tests + callers that use the bare constructor see the
        pre-P4 behavior — ``append_event`` does not touch ``latest_sequence_no``.
        """

        store = InMemoryRuntimeApiStore()
        assert store.consolidates_cursor_writes is False

        self._seed_run(store)
        envelope = await store.append_event(self._draft())

        assert envelope.sequence_no == 1
        run = await store.get_run(org_id=self.ORG_ID, run_id=self.RUN_ID)
        assert run is not None
        # ``RunRecord.latest_sequence_no`` defaults to 0 (the sentinel for
        # "no events yet"); pre-P4 ``append_event`` doesn't touch it.
        assert run.latest_sequence_no == 0

    async def test_consolidated_constructor_advances_cursor(self) -> None:
        """With ``consolidated_writes=True`` the run cursor advances in-line."""

        store = InMemoryRuntimeApiStore(consolidated_writes=True)
        assert store.consolidates_cursor_writes is True

        self._seed_run(store)
        envelope = await store.append_event(self._draft())

        assert envelope.sequence_no == 1
        run = await store.get_run(org_id=self.ORG_ID, run_id=self.RUN_ID)
        assert run is not None
        assert run.latest_sequence_no == 1

    async def test_consolidated_append_no_op_for_unknown_run(self) -> None:
        """Appending for a run with no ``agent_runs`` row records the event
        but does not crash on the missing ``self.runs`` key.
        """

        store = InMemoryRuntimeApiStore(consolidated_writes=True)

        envelope = await store.append_event(
            self._draft(run_id="ghost_run", conversation_id="ghost_conv")
        )

        assert envelope.sequence_no == 1
        # Event lands in events_by_run (matches pre-P4 in-memory behavior so
        # legacy tests that append without a seeded run still pass).
        assert len(store.events_by_run["ghost_run"]) == 1


class TestInMemorySetLatestSequenceMonotonic(_FixturesMixin):
    """H3 parity — ``set_run_latest_sequence`` never rewinds the cursor."""

    async def test_smaller_value_is_no_op(self) -> None:
        store = InMemoryRuntimeApiStore()
        self._seed_run(store)

        await store.set_run_latest_sequence(run_id=self.RUN_ID, latest_sequence_no=5)
        run_after_5 = await store.get_run(org_id=self.ORG_ID, run_id=self.RUN_ID)
        assert run_after_5 is not None
        assert run_after_5.latest_sequence_no == 5

        # Out-of-order arrival must not rewind.
        result = await store.set_run_latest_sequence(
            run_id=self.RUN_ID, latest_sequence_no=3
        )
        assert result.latest_sequence_no == 5

    async def test_equal_value_is_no_op(self) -> None:
        """Equal-value writes are also no-ops (mirrors Postgres ``< new``)."""

        store = InMemoryRuntimeApiStore()
        self._seed_run(store)

        await store.set_run_latest_sequence(run_id=self.RUN_ID, latest_sequence_no=7)
        result = await store.set_run_latest_sequence(
            run_id=self.RUN_ID, latest_sequence_no=7
        )
        assert result.latest_sequence_no == 7

    async def test_larger_value_advances(self) -> None:
        store = InMemoryRuntimeApiStore()
        self._seed_run(store)

        await store.set_run_latest_sequence(run_id=self.RUN_ID, latest_sequence_no=5)
        result = await store.set_run_latest_sequence(
            run_id=self.RUN_ID, latest_sequence_no=10
        )
        assert result.latest_sequence_no == 10


class TestProducerSkipsRedundantCursorCall(_FixturesMixin):
    """The producer auto-detects the adapter's mode and acts accordingly."""

    async def test_producer_calls_when_adapter_does_not_consolidate(self) -> None:
        """Pre-P4 path: producer issues its own ``set_run_latest_sequence``."""

        store = InMemoryRuntimeApiStore()
        producer = RuntimeEventProducer(persistence=store, event_store=store)
        assert producer._consolidated_writes is False

        run = self._seed_run(store)
        envelope = await producer.append_api_event(
            run=run,
            source=StreamEventSource.MODEL,
            event_type=RuntimeApiEventType.MODEL_DELTA,
            payload={"delta": "hi"},
        )

        assert envelope.sequence_no == 1
        run_after = await store.get_run(org_id=self.ORG_ID, run_id=self.RUN_ID)
        assert run_after is not None
        # Cursor was advanced by the producer's separate call.
        assert run_after.latest_sequence_no == 1

    async def test_producer_skips_when_adapter_consolidates(self) -> None:
        """P4 path: producer skips the redundant cursor call; adapter's
        in-line update is the sole source of cursor advancement."""

        store = InMemoryRuntimeApiStore(consolidated_writes=True)
        producer = RuntimeEventProducer(persistence=store, event_store=store)
        assert producer._consolidated_writes is True

        run = self._seed_run(store)

        # Wrap set_run_latest_sequence to count calls — must stay zero from
        # the producer side. The adapter's append_event drives the in-line
        # update via the same instance method, so we count to detect double
        # invocation.
        original = store.set_run_latest_sequence
        call_count = 0

        async def counting_set(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return await original(*args, **kwargs)

        store.set_run_latest_sequence = counting_set  # type: ignore[method-assign]

        envelope = await producer.append_api_event(
            run=run,
            source=StreamEventSource.MODEL,
            event_type=RuntimeApiEventType.MODEL_DELTA,
            payload={"delta": "hi"},
        )

        assert envelope.sequence_no == 1
        # In-memory adapter's append_event calls self.set_run_latest_sequence
        # in-line when consolidated, so we expect exactly one call total
        # (from the adapter, NOT from the producer).
        assert call_count == 1

        run_after = await store.get_run(org_id=self.ORG_ID, run_id=self.RUN_ID)
        assert run_after is not None
        assert run_after.latest_sequence_no == 1

    async def test_producer_consolidated_path_invokes_callback(self) -> None:
        """``on_event_appended`` is invoked regardless of consolidation mode."""

        store = InMemoryRuntimeApiStore(consolidated_writes=True)
        notifications: list[str] = []
        producer = RuntimeEventProducer(
            persistence=store,
            event_store=store,
            on_event_appended=notifications.append,
        )

        run = self._seed_run(store)
        await producer.append_api_event(
            run=run,
            source=StreamEventSource.MODEL,
            event_type=RuntimeApiEventType.MODEL_DELTA,
            payload={"delta": "hi"},
        )

        assert notifications == [self.RUN_ID]


class TestConsolidatedConcurrentAppends(_FixturesMixin):
    """Concurrent ``append_event`` calls preserve monotonic ``sequence_no``
    and end with the cursor at the highest sequence number."""

    async def test_concurrent_appends_assign_unique_sequence_numbers(
        self,
    ) -> None:
        store = InMemoryRuntimeApiStore(consolidated_writes=True)
        self._seed_run(store)

        N = 50
        envelopes = await asyncio.gather(
            *(store.append_event(self._draft()) for _ in range(N))
        )

        sequence_nos = sorted(e.sequence_no for e in envelopes)
        assert sequence_nos == list(range(1, N + 1))

        run = await store.get_run(org_id=self.ORG_ID, run_id=self.RUN_ID)
        assert run is not None
        # Cursor matches the maximum sequence number written.
        assert run.latest_sequence_no == N

    async def test_concurrent_set_latest_sequence_does_not_rewind(self) -> None:
        """Out-of-order ``set_run_latest_sequence`` calls under asyncio
        concurrency must leave the cursor at the maximum value seen."""

        store = InMemoryRuntimeApiStore()
        self._seed_run(store)

        targets = [10, 3, 7, 25, 1, 15, 8]
        await asyncio.gather(
            *(
                store.set_run_latest_sequence(run_id=self.RUN_ID, latest_sequence_no=v)
                for v in targets
            )
        )

        run = await store.get_run(org_id=self.ORG_ID, run_id=self.RUN_ID)
        assert run is not None
        assert run.latest_sequence_no == max(targets)


class TestSettingsFlagWiring:
    """End-to-end: settings flag flows through the adapter factory and the
    producer auto-detects via the adapter."""

    def test_default_settings_default_consolidation_on(self) -> None:
        from agent_runtime.settings import RuntimeSettings

        settings = RuntimeSettings.load(
            environ={
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )
        assert settings.execution.consolidated_event_writes is True

    def test_explicit_false_rolls_back(self) -> None:
        from agent_runtime.settings import RuntimeSettings

        settings = RuntimeSettings.load(
            environ={
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
                "RUNTIME_EVENT_WRITE_CONSOLIDATED": "false",
            }
        )
        assert settings.execution.consolidated_event_writes is False

    def test_factory_threads_flag_into_in_memory_store(self) -> None:
        from agent_runtime.settings import RuntimeSettings
        from runtime_adapters.factory import RuntimeAdapterFactory

        settings = RuntimeSettings.load(
            environ={
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
                "RUNTIME_STORE_BACKEND": "in_memory",
            }
        )
        ports = RuntimeAdapterFactory.from_settings(settings)
        assert ports.event_store.consolidates_cursor_writes is True

    def test_factory_threads_rollback_flag(self) -> None:
        from agent_runtime.settings import RuntimeSettings
        from runtime_adapters.factory import RuntimeAdapterFactory

        settings = RuntimeSettings.load(
            environ={
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
                "RUNTIME_STORE_BACKEND": "in_memory",
                "RUNTIME_EVENT_WRITE_CONSOLIDATED": "false",
            }
        )
        ports = RuntimeAdapterFactory.from_settings(settings)
        assert ports.event_store.consolidates_cursor_writes is False
