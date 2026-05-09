"""PR 04 — ``ToolObservationIndexBuilder`` join against the binding store.

The cross-turn observation builder used to count ``TOOL_CALL_STARTED``
events on prior runs to assign positional ordinals. After PR 04 the
ordinals come from the persistent binding map
(``agent_conversation_tool_ordinals``); the builder joins observations
to the map by ``tool_call_id``. These tests pin the new join contract:

* Observations whose ``call_id`` is bound in the store get the canonical
  ``conversation_ordinal`` — the same number the in-turn allocator
  recorded when the tool fired.
* Observations whose ``call_id`` is *not* in the store (cross-turn from
  before PR 04 lands, or subagent summaries that don't go through the
  allocator) lack ``conversation_ordinal`` rather than getting an
  invented value.
* When no store is bound at construction (replay / eval path), the
  builder runs without raising and every observation comes back with
  ``conversation_ordinal=None``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime, timezone

from agent_runtime.execution.contracts import StreamEventSource
from runtime_adapters.in_memory.conversation_tool_ordinal_store import (
    InMemoryConversationToolOrdinalStore,
)
from runtime_api.schemas import (
    MessageRecord,
    RuntimeActivityKind,
    RuntimeApiEventType,
    RuntimeEventEnvelope,
    RuntimeEventRedactionState,
    RuntimeEventVisibility,
)
from runtime_worker.tool_observations import ToolObservationIndexBuilder


class _Fixture:
    ORG_ID = "org_pr04"
    CONVERSATION_ID = "conv_pr04"
    PRIOR_RUN_ID = "run_prior"
    CURRENT_RUN_ID = "run_current"
    CALL_ID_LINEAR = "call_linear_list_issues"
    CALL_ID_WEB = "call_web_search"
    UNBOUND_CALL_ID = "call_unbound"
    TRACE = "trace_pr04"

    def __init__(self) -> None:
        self.events_by_run: dict[str, list[RuntimeEventEnvelope]] = {}

    def append_tool_events(self, *, run_id: str, call_id: str, tool_name: str) -> None:
        # Two events per tool invocation so ``_observations_for_run``
        # produces a ToolObservation: TOOL_CALL_STARTED records the
        # call, TOOL_RESULT carries the output preview.
        events = self.events_by_run.setdefault(run_id, [])
        for event_type, payload in (
            (
                RuntimeApiEventType.TOOL_CALL_STARTED,
                {
                    "tool_name": tool_name,
                    "call_id": call_id,
                    "args": {"q": "blockers"},
                },
            ),
            (
                RuntimeApiEventType.TOOL_RESULT,
                {
                    "tool_name": tool_name,
                    "call_id": call_id,
                    "output": {"summary": "found 2"},
                },
            ),
        ):
            events.append(
                RuntimeEventEnvelope(
                    run_id=run_id,
                    conversation_id=self.CONVERSATION_ID,
                    sequence_no=len(events) + 1,
                    source=StreamEventSource.TOOL,
                    event_type=event_type,
                    trace_id=self.TRACE,
                    activity_kind=RuntimeActivityKind.TOOL,
                    visibility=RuntimeEventVisibility.USER,
                    redaction_state=RuntimeEventRedactionState.REDACTED,
                    payload=payload,
                    created_at=datetime.now(timezone.utc),
                )
            )

    def message_chain(self) -> tuple[MessageRecord, ...]:
        # Two assistant messages, one per run, in document order
        # (prior first). The builder reads only ``run_id`` from each
        # entry and walks events for the prior runs.
        return (
            MessageRecord(
                message_id="assistant_prior",
                conversation_id=self.CONVERSATION_ID,
                org_id=self.ORG_ID,
                run_id=self.PRIOR_RUN_ID,
                role="assistant",
                content_text="ok",
                content_format="text",
                parent_message_id=None,
            ),
            MessageRecord(
                message_id="assistant_current",
                conversation_id=self.CONVERSATION_ID,
                org_id=self.ORG_ID,
                run_id=self.CURRENT_RUN_ID,
                role="assistant",
                content_text="ok",
                content_format="text",
                parent_message_id="assistant_prior",
            ),
        )


class _StubEventStore:
    """Minimal ``EventStorePort`` stand-in keyed off run_id only."""

    def __init__(self, events_by_run: dict[str, list[RuntimeEventEnvelope]]) -> None:
        self._events_by_run = events_by_run

    async def list_events_after(
        self,
        *,
        org_id: str,
        run_id: str,
        after_sequence: int,
    ) -> Sequence[RuntimeEventEnvelope]:
        return tuple(
            event
            for event in self._events_by_run.get(run_id, [])
            if event.sequence_no > after_sequence
        )


class TestBuilderWithBindingStore:
    def test_stamps_ordinal_from_store_for_bound_call_id(self) -> None:
        f = _Fixture()
        f.append_tool_events(
            run_id=_Fixture.PRIOR_RUN_ID,
            call_id=_Fixture.CALL_ID_LINEAR,
            tool_name="call_tool",
        )
        ordinal_store = InMemoryConversationToolOrdinalStore()
        asyncio.run(
            ordinal_store.record(
                org_id=_Fixture.ORG_ID,
                conversation_id=_Fixture.CONVERSATION_ID,
                conversation_ordinal=4,
                tool_call_id=_Fixture.CALL_ID_LINEAR,
                tool_name="linear.list_issues",
                run_id=_Fixture.PRIOR_RUN_ID,
            )
        )

        builder = ToolObservationIndexBuilder(
            _StubEventStore(f.events_by_run),
            conversation_tool_ordinal_store=ordinal_store,
        )
        index = asyncio.run(
            builder.build(
                org_id=_Fixture.ORG_ID,
                conversation_id=_Fixture.CONVERSATION_ID,
                current_run_id=_Fixture.CURRENT_RUN_ID,
                selected_messages=f.message_chain(),
            )
        )

        assert len(index.observations) == 1
        observation = index.observations[0]
        assert observation.call_id == _Fixture.CALL_ID_LINEAR
        assert observation.conversation_ordinal == 4
        # The prompt context surfaces the ``cite as [[N]]`` marker
        # for bound observations — that's the cross-turn primitive
        # the model reads to reuse a prior turn's tool.
        assert index.prompt_context is not None
        assert "cite as [[4]]" in index.prompt_context

    def test_unbound_call_id_yields_none_ordinal(self) -> None:
        # Ordinal map present but the observation's call_id isn't in
        # it (e.g. the binding pre-dates PR 04's persistence). The
        # observation surfaces with ``conversation_ordinal=None`` —
        # the prompt context still describes the prior result, just
        # without a ``cite as [[N]]`` hint.
        f = _Fixture()
        f.append_tool_events(
            run_id=_Fixture.PRIOR_RUN_ID,
            call_id=_Fixture.UNBOUND_CALL_ID,
            tool_name="legacy_tool",
        )
        ordinal_store = InMemoryConversationToolOrdinalStore()
        # Store carries a binding for an unrelated call_id — pins
        # that we don't accidentally match by tool_name or any other
        # field.
        asyncio.run(
            ordinal_store.record(
                org_id=_Fixture.ORG_ID,
                conversation_id=_Fixture.CONVERSATION_ID,
                conversation_ordinal=1,
                tool_call_id="some_other_call",
                tool_name="other",
                run_id=_Fixture.PRIOR_RUN_ID,
            )
        )

        builder = ToolObservationIndexBuilder(
            _StubEventStore(f.events_by_run),
            conversation_tool_ordinal_store=ordinal_store,
        )
        index = asyncio.run(
            builder.build(
                org_id=_Fixture.ORG_ID,
                conversation_id=_Fixture.CONVERSATION_ID,
                current_run_id=_Fixture.CURRENT_RUN_ID,
                selected_messages=f.message_chain(),
            )
        )

        assert len(index.observations) == 1
        assert index.observations[0].conversation_ordinal is None
        assert index.prompt_context is not None
        assert "cite as [[" not in index.prompt_context

    def test_no_store_returns_observation_without_ordinal(self) -> None:
        # Replay / eval path — no store at construction time.
        # Observations come back without ordinals; nothing raises.
        f = _Fixture()
        f.append_tool_events(
            run_id=_Fixture.PRIOR_RUN_ID,
            call_id=_Fixture.CALL_ID_WEB,
            tool_name="web_search",
        )

        builder = ToolObservationIndexBuilder(_StubEventStore(f.events_by_run))
        index = asyncio.run(
            builder.build(
                org_id=_Fixture.ORG_ID,
                conversation_id=_Fixture.CONVERSATION_ID,
                current_run_id=_Fixture.CURRENT_RUN_ID,
                selected_messages=f.message_chain(),
            )
        )

        assert len(index.observations) == 1
        assert index.observations[0].conversation_ordinal is None
