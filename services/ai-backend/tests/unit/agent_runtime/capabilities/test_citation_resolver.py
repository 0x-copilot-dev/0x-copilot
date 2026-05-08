"""Unit tests for ``CitationResolver`` and ``ConversationOrdinalAllocator``.

PR 04 — citations binding map.

These tests pin the resolver's tokenization, idempotency, dedupe, and
sealed-ordinal contract, plus the allocator's monotonic / mapping
guarantees and idempotency on retried tool_call_ids. They use only the
public seams (``observe_delta`` / ``allocate_for_tool_call`` /
``sealed_ordinals``) so the internal buffering strategy can evolve
without churning tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from agent_runtime.capabilities.citation_resolver import CitationResolver
from agent_runtime.capabilities.conversation_ordinals import (
    ConversationOrdinalAllocator,
)
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.persistence.ports import ConversationOrdinalConflict
from runtime_adapters.in_memory.conversation_tool_ordinal_store import (
    InMemoryConversationToolOrdinalStore,
)
from runtime_api.schemas import RuntimeApiEventType


class _Values:
    RUN_ID = "run_xyz"
    ORG_ID = "org_x"
    CONVERSATION_ID = "conv_x"
    MESSAGE_A = "msg_a"
    MESSAGE_B = "msg_b"
    TOOL_CALL_ONE = "call_one"
    TOOL_CALL_TWO = "call_two"
    TOOL_CALL_THREE = "call_three"
    TOOL_NAME = "web_search"


@dataclass(frozen=True)
class _StubRun:
    """Minimal RunRecord-shaped stub for the resolver tests.

    The resolver only reads ``run_id`` from the record, so a frozen
    dataclass with that single attribute is sufficient.
    """

    run_id: str


class _RecordingProducer:
    """In-memory stand-in for ``RuntimeEventProducer``."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def append_api_event(
        self,
        *,
        run: Any,
        source: StreamEventSource,
        event_type: RuntimeApiEventType,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> None:
        self.events.append(
            {
                "run_id": run.run_id,
                "source": source,
                "event_type": event_type,
                "payload": payload,
            }
        )


def _build_allocator(
    *,
    starting_ordinal: int = 0,
    mapping: dict[int, str] | None = None,
    store: InMemoryConversationToolOrdinalStore | None = None,
) -> ConversationOrdinalAllocator:
    return ConversationOrdinalAllocator(
        org_id=_Values.ORG_ID,
        conversation_id=_Values.CONVERSATION_ID,
        run_id=_Values.RUN_ID,
        store=store,
        starting_ordinal=starting_ordinal,
        ordinal_to_tool_call_id=mapping or {},
    )


def _build_resolver(
    *,
    starting_ordinal: int = 0,
    mapping: dict[int, str] | None = None,
) -> tuple[CitationResolver, _RecordingProducer, ConversationOrdinalAllocator]:
    allocator = _build_allocator(starting_ordinal=starting_ordinal, mapping=mapping)
    producer = _RecordingProducer()
    resolver = CitationResolver(
        run=_StubRun(run_id=_Values.RUN_ID),
        allocator=allocator,
        producer=producer,
        source=StreamEventSource.MODEL,
    )
    return resolver, producer, allocator


class TestConversationOrdinalAllocatorMemoryOnly:
    """Allocator behaviour when no store is bound (replay / unit-test path)."""

    @pytest.mark.asyncio
    async def test_allocate_for_tool_call_is_monotonic(self) -> None:
        allocator = _build_allocator(starting_ordinal=4)
        first = await allocator.allocate_for_tool_call(
            tool_call_id=_Values.TOOL_CALL_ONE, tool_name=_Values.TOOL_NAME
        )
        second = await allocator.allocate_for_tool_call(
            tool_call_id=_Values.TOOL_CALL_TWO, tool_name=_Values.TOOL_NAME
        )
        assert first == 5
        assert second == 6
        assert allocator.last_allocated == 6

    @pytest.mark.asyncio
    async def test_idempotent_on_same_tool_call_id(self) -> None:
        # Regression pin: a retried allocate for the same tool_call_id
        # (LangGraph re-dispatch on resume) collapses to the existing
        # ordinal — no second allocation, no counter bump.
        allocator = _build_allocator()
        first = await allocator.allocate_for_tool_call(
            tool_call_id=_Values.TOOL_CALL_ONE, tool_name=_Values.TOOL_NAME
        )
        second = await allocator.allocate_for_tool_call(
            tool_call_id=_Values.TOOL_CALL_ONE, tool_name=_Values.TOOL_NAME
        )
        assert first == second == 1
        assert allocator.last_allocated == 1

    @pytest.mark.asyncio
    async def test_rejects_empty_tool_call_id(self) -> None:
        allocator = _build_allocator()
        with pytest.raises(ValueError):
            await allocator.allocate_for_tool_call(
                tool_call_id="", tool_name=_Values.TOOL_NAME
            )

    @pytest.mark.asyncio
    async def test_rejects_empty_tool_name(self) -> None:
        allocator = _build_allocator()
        with pytest.raises(ValueError):
            await allocator.allocate_for_tool_call(
                tool_call_id=_Values.TOOL_CALL_ONE, tool_name=""
            )

    def test_bind_unbind_round_trip(self) -> None:
        allocator = _build_allocator()
        assert ConversationOrdinalAllocator.active() is None
        token = ConversationOrdinalAllocator.bind_for_run(allocator)
        assert ConversationOrdinalAllocator.active() is allocator
        ConversationOrdinalAllocator.unbind(token)
        assert ConversationOrdinalAllocator.active() is None

    def test_negative_seed_rejected(self) -> None:
        with pytest.raises(ValueError):
            _build_allocator(starting_ordinal=-1)

    def test_has_ordinal_reflects_seed(self) -> None:
        allocator = _build_allocator(starting_ordinal=2)
        assert allocator.has_ordinal(1)
        assert allocator.has_ordinal(2)
        assert not allocator.has_ordinal(3)

    def test_tool_call_id_for_unknown_returns_none(self) -> None:
        allocator = _build_allocator(
            starting_ordinal=2,
            mapping={1: _Values.TOOL_CALL_ONE, 2: _Values.TOOL_CALL_TWO},
        )
        assert allocator.tool_call_id_for(1) == _Values.TOOL_CALL_ONE
        assert allocator.tool_call_id_for(99) is None


class TestConversationOrdinalAllocatorWithStore:
    """Allocator behaviour against the InMemory binding store (write-through path)."""

    @pytest.mark.asyncio
    async def test_writes_through_to_store(self) -> None:
        store = InMemoryConversationToolOrdinalStore()
        allocator = _build_allocator(store=store)
        ordinal = await allocator.allocate_for_tool_call(
            tool_call_id=_Values.TOOL_CALL_ONE, tool_name=_Values.TOOL_NAME
        )
        rows = await store.load(
            org_id=_Values.ORG_ID, conversation_id=_Values.CONVERSATION_ID
        )
        assert ordinal == 1
        assert len(rows) == 1
        assert rows[0].tool_call_id == _Values.TOOL_CALL_ONE
        assert rows[0].run_id == _Values.RUN_ID

    @pytest.mark.asyncio
    async def test_idempotent_retry_does_not_duplicate_row(self) -> None:
        store = InMemoryConversationToolOrdinalStore()
        allocator = _build_allocator(store=store)
        first = await allocator.allocate_for_tool_call(
            tool_call_id=_Values.TOOL_CALL_ONE, tool_name=_Values.TOOL_NAME
        )
        second = await allocator.allocate_for_tool_call(
            tool_call_id=_Values.TOOL_CALL_ONE, tool_name=_Values.TOOL_NAME
        )
        rows = await store.load(
            org_id=_Values.ORG_ID, conversation_id=_Values.CONVERSATION_ID
        )
        assert first == second
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_for_conversation_restores_from_store(self) -> None:
        # Pre-populate the store with two bindings; for_conversation
        # should restore counter=2 and the canonical mapping so a
        # subsequent allocate returns 3.
        store = InMemoryConversationToolOrdinalStore()
        await store.record(
            org_id=_Values.ORG_ID,
            conversation_id=_Values.CONVERSATION_ID,
            conversation_ordinal=1,
            tool_call_id=_Values.TOOL_CALL_ONE,
            tool_name=_Values.TOOL_NAME,
            run_id="run_prior",
        )
        await store.record(
            org_id=_Values.ORG_ID,
            conversation_id=_Values.CONVERSATION_ID,
            conversation_ordinal=2,
            tool_call_id=_Values.TOOL_CALL_TWO,
            tool_name=_Values.TOOL_NAME,
            run_id="run_prior",
        )
        allocator = await ConversationOrdinalAllocator.for_conversation(
            org_id=_Values.ORG_ID,
            conversation_id=_Values.CONVERSATION_ID,
            run_id=_Values.RUN_ID,
            store=store,
        )
        assert allocator.last_allocated == 2
        assert allocator.tool_call_id_for(1) == _Values.TOOL_CALL_ONE
        assert allocator.tool_call_id_for(2) == _Values.TOOL_CALL_TWO
        next_ordinal = await allocator.allocate_for_tool_call(
            tool_call_id=_Values.TOOL_CALL_THREE, tool_name=_Values.TOOL_NAME
        )
        assert next_ordinal == 3

    @pytest.mark.asyncio
    async def test_reload_after_conflict_returns_canonical_ordinal(self) -> None:
        # Simulate a concurrent allocator: one allocator wrote (3,
        # call_three) for the conversation while we held an in-memory
        # counter of 0. Our next allocate for "call_three" must observe
        # the conflict, reload, and return the canonical ordinal 3
        # instead of inserting a duplicate.
        store = InMemoryConversationToolOrdinalStore()
        await store.record(
            org_id=_Values.ORG_ID,
            conversation_id=_Values.CONVERSATION_ID,
            conversation_ordinal=3,
            tool_call_id=_Values.TOOL_CALL_THREE,
            tool_name=_Values.TOOL_NAME,
            run_id="run_winner",
        )
        # Build allocator after the winning write; counter starts at 0
        # locally because we haven't reloaded yet.
        allocator = _build_allocator(store=store)
        ordinal = await allocator.allocate_for_tool_call(
            tool_call_id=_Values.TOOL_CALL_THREE, tool_name=_Values.TOOL_NAME
        )
        assert ordinal == 3
        assert allocator.tool_call_id_for(3) == _Values.TOOL_CALL_THREE

    @pytest.mark.asyncio
    async def test_conflict_signature_is_typed(self) -> None:
        # Sanity: the ConversationOrdinalConflict type the allocator
        # catches is the same type our adapter raises.
        store = InMemoryConversationToolOrdinalStore()
        await store.record(
            org_id=_Values.ORG_ID,
            conversation_id=_Values.CONVERSATION_ID,
            conversation_ordinal=1,
            tool_call_id=_Values.TOOL_CALL_ONE,
            tool_name=_Values.TOOL_NAME,
            run_id=_Values.RUN_ID,
        )
        with pytest.raises(ConversationOrdinalConflict):
            await store.record(
                org_id=_Values.ORG_ID,
                conversation_id=_Values.CONVERSATION_ID,
                conversation_ordinal=2,
                tool_call_id=_Values.TOOL_CALL_ONE,
                tool_name=_Values.TOOL_NAME,
                run_id=_Values.RUN_ID,
            )


class TestCitationResolverTokenization:
    @pytest.mark.asyncio
    async def test_emits_one_event_per_complete_token(self) -> None:
        resolver, producer, _ = _build_resolver(
            starting_ordinal=2,
            mapping={1: _Values.TOOL_CALL_ONE, 2: _Values.TOOL_CALL_TWO},
        )
        await resolver.observe_delta(
            message_id=_Values.MESSAGE_A, delta_text="See [[1]] and "
        )
        await resolver.observe_delta(message_id=_Values.MESSAGE_A, delta_text="[[2]].")
        ordinals = [
            event["payload"]["link"]["conversation_ordinal"]
            for event in producer.events
        ]
        assert ordinals == [1, 2]

    @pytest.mark.asyncio
    async def test_partial_tokens_do_not_emit_until_complete(self) -> None:
        resolver, producer, _ = _build_resolver(
            starting_ordinal=1, mapping={1: _Values.TOOL_CALL_ONE}
        )
        await resolver.observe_delta(message_id=_Values.MESSAGE_A, delta_text="[[1")
        assert producer.events == []
        await resolver.observe_delta(message_id=_Values.MESSAGE_A, delta_text="]]")
        assert len(producer.events) == 1

    @pytest.mark.asyncio
    async def test_redelivered_delta_does_not_double_emit(self) -> None:
        resolver, producer, _ = _build_resolver(
            starting_ordinal=1, mapping={1: _Values.TOOL_CALL_ONE}
        )
        await resolver.observe_delta(message_id=_Values.MESSAGE_A, delta_text="[[1]]")
        await resolver.observe_delta(message_id=_Values.MESSAGE_A, delta_text="[[1]]")
        # Buffer accumulates the second delta; the regex re-finds the
        # first match but the (offset, ordinal) idempotency key blocks it.
        # The second [[1]] *at a new offset* should still emit.
        assert len(producer.events) == 2
        offsets = [
            event["payload"]["link"]["prose_offset"] for event in producer.events
        ]
        assert offsets == [0, 5]

    @pytest.mark.asyncio
    async def test_stamps_source_tool_call_id_when_bound(self) -> None:
        resolver, producer, _ = _build_resolver(
            starting_ordinal=1, mapping={1: _Values.TOOL_CALL_ONE}
        )
        await resolver.observe_delta(message_id=_Values.MESSAGE_A, delta_text="[[1]]")
        assert producer.events[0]["payload"]["link"]["source_tool_call_id"] == (
            _Values.TOOL_CALL_ONE
        )

    @pytest.mark.asyncio
    async def test_unbound_ordinal_emits_event_with_empty_call_id(self) -> None:
        # Hallucinated ordinal — model wrote [[99]] but no allocation
        # was ever recorded. Resolver still emits (so the FE can render
        # the muted ``?``); source_tool_call_id is the empty string.
        resolver, producer, _ = _build_resolver(starting_ordinal=1)
        await resolver.observe_delta(message_id=_Values.MESSAGE_A, delta_text="[[99]]")
        assert len(producer.events) == 1
        link = producer.events[0]["payload"]["link"]
        assert link["conversation_ordinal"] == 99
        assert link["source_tool_call_id"] == ""

    @pytest.mark.asyncio
    async def test_separate_messages_track_independently(self) -> None:
        resolver, producer, _ = _build_resolver(
            starting_ordinal=2,
            mapping={1: _Values.TOOL_CALL_ONE, 2: _Values.TOOL_CALL_TWO},
        )
        await resolver.observe_delta(message_id=_Values.MESSAGE_A, delta_text="[[1]]")
        await resolver.observe_delta(message_id=_Values.MESSAGE_B, delta_text="[[2]]")
        message_ids = {
            event["payload"]["link"]["message_id"] for event in producer.events
        }
        assert message_ids == {_Values.MESSAGE_A, _Values.MESSAGE_B}

    @pytest.mark.asyncio
    async def test_sealed_ordinals_in_first_occurrence_order(self) -> None:
        resolver, _, _ = _build_resolver(
            starting_ordinal=2,
            mapping={1: "a", 2: "b"},
        )
        await resolver.observe_delta(
            message_id=_Values.MESSAGE_A,
            delta_text="See [[2]] before [[1]] and [[2]] again.",
        )
        # Ordinal 2 first; 1 second; second [[2]] does not re-add.
        assert resolver.sealed_ordinals() == [2, 1]
