"""Unit tests for ``CitationResolver`` and ``ConversationOrdinalAllocator``.

PR 1.1-rev2 — model-declared citation pointers.

These tests pin the resolver's tokenization, idempotency, dedupe, and
sealed-ordinal contract, plus the allocator's monotonic / mapping
guarantees. They are intentionally small and use only the public seams
(``observe_delta`` / ``allocate_for_tool_call`` / ``sealed_ordinals``)
so the internal buffering strategy can evolve without churning tests.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pytest

from agent_runtime.capabilities.citation_resolver import CitationResolver
from agent_runtime.capabilities.conversation_ordinals import (
    ConversationOrdinalAllocator,
    ConversationOrdinalSeeder,
)
from agent_runtime.execution.contracts import StreamEventSource
from runtime_api.schemas import RuntimeApiEventType


class _Values:
    RUN_ID = "run_xyz"
    ORG_ID = "org_x"
    CONVERSATION_ID = "conv_x"
    MESSAGE_A = "msg_a"
    MESSAGE_B = "msg_b"
    TOOL_CALL_PRIOR = "call_one"
    TOOL_CALL_PRIOR2 = "call_two"
    TOOL_CALL_NEW = "call_three"


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


def _build_resolver(
    *,
    starting_ordinal: int = 0,
    mapping: dict[int, str] | None = None,
) -> tuple[CitationResolver, _RecordingProducer, ConversationOrdinalAllocator]:
    allocator = ConversationOrdinalAllocator(
        conversation_id=_Values.CONVERSATION_ID,
        starting_ordinal=starting_ordinal,
        ordinal_to_tool_call_id=mapping or {},
    )
    producer = _RecordingProducer()
    resolver = CitationResolver(
        run=_StubRun(run_id=_Values.RUN_ID),
        allocator=allocator,
        producer=producer,
        source=StreamEventSource.MODEL,
    )
    return resolver, producer, allocator


class TestConversationOrdinalAllocator:
    def test_allocate_is_monotonic_from_seed(self) -> None:
        allocator = ConversationOrdinalAllocator(
            conversation_id=_Values.CONVERSATION_ID,
            starting_ordinal=4,
        )
        assert allocator.allocate() == 5
        assert allocator.allocate() == 6
        assert allocator.last_allocated == 6

    def test_allocate_for_tool_call_records_mapping(self) -> None:
        allocator = ConversationOrdinalAllocator(
            conversation_id=_Values.CONVERSATION_ID,
            starting_ordinal=0,
            ordinal_to_tool_call_id={1: _Values.TOOL_CALL_PRIOR},
        )
        new_ordinal = allocator.allocate_for_tool_call(
            tool_call_id=_Values.TOOL_CALL_NEW
        )
        assert new_ordinal == 1  # collision with seed; last allocated overwrites
        # Seed mapping was {1: prior}; allocate_for_tool_call(1) overwrites to new.
        assert allocator.tool_call_id_for(1) == _Values.TOOL_CALL_NEW

    def test_allocate_for_tool_call_rejects_empty_id(self) -> None:
        allocator = ConversationOrdinalAllocator(
            conversation_id=_Values.CONVERSATION_ID,
            starting_ordinal=0,
        )
        with pytest.raises(ValueError):
            allocator.allocate_for_tool_call(tool_call_id="")

    def test_bind_unbind_round_trip(self) -> None:
        allocator = ConversationOrdinalAllocator(
            conversation_id=_Values.CONVERSATION_ID,
            starting_ordinal=0,
        )
        assert ConversationOrdinalAllocator.active() is None
        token = ConversationOrdinalAllocator.bind_for_run(allocator)
        assert ConversationOrdinalAllocator.active() is allocator
        ConversationOrdinalAllocator.unbind(token)
        assert ConversationOrdinalAllocator.active() is None

    def test_negative_seed_rejected(self) -> None:
        with pytest.raises(ValueError):
            ConversationOrdinalAllocator(
                conversation_id=_Values.CONVERSATION_ID,
                starting_ordinal=-1,
            )

    def test_has_ordinal(self) -> None:
        allocator = ConversationOrdinalAllocator(
            conversation_id=_Values.CONVERSATION_ID,
            starting_ordinal=2,
        )
        assert allocator.has_ordinal(1)
        assert allocator.has_ordinal(2)
        assert not allocator.has_ordinal(3)
        allocator.allocate()
        assert allocator.has_ordinal(3)


class TestCitationResolverTokenization:
    @pytest.mark.asyncio
    async def test_emits_one_event_per_complete_token(self) -> None:
        resolver, producer, _ = _build_resolver(
            starting_ordinal=2,
            mapping={1: _Values.TOOL_CALL_PRIOR, 2: _Values.TOOL_CALL_PRIOR2},
        )
        await resolver.observe_delta(
            message_id=_Values.MESSAGE_A,
            delta_text="See [[1]] and also [[2]] later.",
        )
        ordinals = [
            event["payload"]["link"]["conversation_ordinal"]
            for event in producer.events
        ]
        assert ordinals == [1, 2]

    @pytest.mark.asyncio
    async def test_partial_token_split_across_deltas(self) -> None:
        resolver, producer, _ = _build_resolver(
            starting_ordinal=3,
            mapping={3: _Values.TOOL_CALL_NEW},
        )
        # Deliver ``[[3]]`` in three pieces; the resolver should not
        # emit until the closing ``]]`` arrives.
        await resolver.observe_delta(message_id=_Values.MESSAGE_A, delta_text="Per [[")
        assert producer.events == []
        await resolver.observe_delta(message_id=_Values.MESSAGE_A, delta_text="3")
        assert producer.events == []
        await resolver.observe_delta(
            message_id=_Values.MESSAGE_A, delta_text="]] launch."
        )
        assert len(producer.events) == 1
        link = producer.events[0]["payload"]["link"]
        assert link["conversation_ordinal"] == 3
        # ``Per `` is 4 chars; the marker starts at offset 4.
        assert link["prose_offset"] == 4
        assert link["prose_length"] == len("[[3]]")

    @pytest.mark.asyncio
    async def test_re_delivered_delta_is_idempotent(self) -> None:
        resolver, producer, _ = _build_resolver(
            starting_ordinal=1,
            mapping={1: _Values.TOOL_CALL_PRIOR},
        )
        await resolver.observe_delta(message_id=_Values.MESSAGE_A, delta_text="[[1]]")
        # The exact same delta text is observed again — a re-deliver
        # on stream resume must not duplicate the event.
        await resolver.observe_delta(message_id=_Values.MESSAGE_A, delta_text="")
        assert len(producer.events) == 1

    @pytest.mark.asyncio
    async def test_same_ordinal_at_two_offsets_emits_twice(self) -> None:
        resolver, producer, _ = _build_resolver(
            starting_ordinal=1,
            mapping={1: _Values.TOOL_CALL_PRIOR},
        )
        await resolver.observe_delta(
            message_id=_Values.MESSAGE_A,
            delta_text="[[1]] vs [[1]] mid prose.",
        )
        offsets = [
            event["payload"]["link"]["prose_offset"] for event in producer.events
        ]
        assert len(producer.events) == 2
        assert offsets == [0, 9]

    @pytest.mark.asyncio
    async def test_unknown_ordinal_emits_with_empty_tool_call_id(self) -> None:
        resolver, producer, _ = _build_resolver(starting_ordinal=0)
        await resolver.observe_delta(message_id=_Values.MESSAGE_A, delta_text="[[42]]")
        assert len(producer.events) == 1
        assert producer.events[0]["payload"]["link"]["source_tool_call_id"] == ""

    @pytest.mark.asyncio
    async def test_separate_messages_track_independently(self) -> None:
        resolver, producer, _ = _build_resolver(
            starting_ordinal=2,
            mapping={1: _Values.TOOL_CALL_PRIOR, 2: _Values.TOOL_CALL_PRIOR2},
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


class TestConversationOrdinalSeeder:
    @pytest.mark.asyncio
    async def test_seed_counts_prior_tool_starts(self) -> None:
        # Stub event store: returns events for two prior runs of the
        # conversation, each with a TOOL_CALL_STARTED event carrying
        # a call_id payload.
        @dataclass(frozen=True)
        class _StubEvent:
            event_type: RuntimeApiEventType
            conversation_id: str
            payload: dict[str, Any]

        prior_events_run_1: Sequence[Any] = (
            _StubEvent(
                event_type=RuntimeApiEventType.TOOL_CALL_STARTED,
                conversation_id=_Values.CONVERSATION_ID,
                payload={"call_id": _Values.TOOL_CALL_PRIOR},
            ),
        )
        prior_events_run_2: Sequence[Any] = (
            _StubEvent(
                event_type=RuntimeApiEventType.TOOL_CALL_STARTED,
                conversation_id=_Values.CONVERSATION_ID,
                payload={"call_id": _Values.TOOL_CALL_PRIOR2},
            ),
            _StubEvent(
                event_type=RuntimeApiEventType.TOOL_RESULT,
                conversation_id=_Values.CONVERSATION_ID,
                payload={"call_id": _Values.TOOL_CALL_PRIOR2},
            ),
            # Cross-conversation event must be filtered out.
            _StubEvent(
                event_type=RuntimeApiEventType.TOOL_CALL_STARTED,
                conversation_id="other_conv",
                payload={"call_id": "ignored"},
            ),
        )

        class _StubEventStore:
            async def list_events_after(
                self, *, org_id: str, run_id: str, after_sequence: int
            ) -> Sequence[Any]:
                return {
                    "run_a": prior_events_run_1,
                    "run_b": prior_events_run_2,
                }[run_id]

        seed = await ConversationOrdinalSeeder.seed_from_event_log(
            org_id=_Values.ORG_ID,
            conversation_id=_Values.CONVERSATION_ID,
            prior_run_ids=("run_a", "run_b"),
            event_store=_StubEventStore(),
        )
        assert seed.starting_ordinal == 2
        assert seed.ordinal_to_tool_call_id == {
            1: _Values.TOOL_CALL_PRIOR,
            2: _Values.TOOL_CALL_PRIOR2,
        }
