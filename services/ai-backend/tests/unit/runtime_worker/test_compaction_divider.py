"""The compaction divider: an oversized tool result is visible in the transcript.

This runtime has always compacted. ``ToolResultAdmissionAdapter`` bounds every
tool result before the model sees it, parks the source in the object store, and
builds a :class:`ContextCompressionEvent` describing exactly what it did --
which ``ToolResultOffloader.apply`` then dropped on the floor. The user saw a
bounded preview and no account of where the rest of the bytes went, so "the
agent forgot something it already read" was unexplainable from the transcript.

These tests drive the REAL seam, not a mock of it: a real
``ToolResultAdmissionAdapter`` over a real ``InMemoryOffloadWriter``, a real
``RuntimeEventProducer`` over a real ``InMemoryRuntimeApiStore``, and
``StreamMessageProcessor.process`` -- the same method the worker's stream loop
calls for every chunk. Nothing here asserts against a hand-built payload.

The four claims, one class each:

* the typed event is emitted, with counts measured at the admission seam;
* the presentation boundary projects its label SERVER-side, so no client
  infers a timeline label from an event-name prefix;
* a run that compacted nothing emits nothing -- the divider is not decoration;
* the event survives replay, which is what makes it a transcript part rather
  than a toast that only a live SSE subscriber ever saw.
"""

from __future__ import annotations

import os

os.environ.setdefault("OPENAI_API_KEY", "sk-test-compaction-divider")

import pytest

from agent_runtime.api.constants import Keys, Messages
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.context.memory.compaction import CompactionNotice
from agent_runtime.context.memory.constants import Values as MemoryValues
from agent_runtime.context.memory.contracts import ContextCompressionStrategy
from agent_runtime.context.tool_result_admission import ToolResultAdmissionAdapter
from agent_runtime.execution.contracts import AgentRuntimeContext, StreamEventSource
from runtime_adapters.in_memory.offload import InMemoryOffloadWriter
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    RunRecord,
    RuntimeApiEventType,
    RuntimeEventPresentationProjector,
)
from runtime_worker.stream_parts import StreamNamespace
from runtime_worker.stream_subagents import StreamUpdateProcessor
from runtime_worker.stream_tools import StreamMessageProcessor
from runtime_worker.tool_result_offload import ToolResultOffloader

_ORG_ID = "org_compaction"
_USER_ID = "user_compaction"
_CONVERSATION_ID = "conv_compaction"
_RUN_ID = "run_compaction"
_USER_MESSAGE_ID = "msg_compaction"
_TOOL_NAME = "read_file"
_CALL_ID = "call_compaction_1"

#: Comfortably past the adapter's inline budget so the offload branch runs.
_OVERSIZED_OUTPUT = "SEARCH RESULT LINE\n" * 12_000
#: Far under it, so the same code path admits inline and compacts nothing.
_SMALL_OUTPUT = "a short tool result"


def _run_record() -> RunRecord:
    return RunRecord(
        run_id=_RUN_ID,
        conversation_id=_CONVERSATION_ID,
        org_id=_ORG_ID,
        user_id=_USER_ID,
        user_message_id=_USER_MESSAGE_ID,
        trace_id="trace_compaction",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        runtime_context=AgentRuntimeContext(
            user_id=_USER_ID,
            org_id=_ORG_ID,
            roles=["employee"],
            run_id=_RUN_ID,
            trace_id="trace_compaction",
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


class _Seam:
    """The real worker seam, assembled from real collaborators."""

    def __init__(self) -> None:
        self.store = InMemoryRuntimeApiStore()
        self.run = _run_record()
        self.store.runs[_RUN_ID] = self.run
        self.store.events_by_run.setdefault(_RUN_ID, [])
        self.producer = RuntimeEventProducer(
            persistence=self.store,
            event_store=self.store,
        )
        self.adapter = ToolResultAdmissionAdapter(InMemoryOffloadWriter())
        self.processor = StreamMessageProcessor(
            event_producer=self.producer,
            update_processor=StreamUpdateProcessor(event_producer=self.producer),
            tool_result_offloader=ToolResultOffloader(admission_adapter=self.adapter),
        )

    async def stream_tool_result(self, output: str) -> None:
        """Drive one tool-result chunk through the worker's real stream pass."""

        await self.processor.process(
            run=self.run,
            namespace=StreamNamespace.from_value(()),
            message={
                "type": "tool",
                "name": _TOOL_NAME,
                "tool_call_id": _CALL_ID,
                "content": output,
            },
            delta=None,
        )

    async def replayed_events(self):
        """Read the run's events back the way the replay route does."""

        return await self.store.list_events_after(
            org_id=_ORG_ID,
            run_id=_RUN_ID,
            after_sequence=0,
        )

    @staticmethod
    def of_type(events, event_type):
        return [event for event in events if event.event_type is event_type]


class TestCompactionEmitsTheTypedEvent:
    """Claim 1: compacting a tool result puts a typed event on the stream."""

    async def test_oversized_tool_result_emits_one_compression_note(self) -> None:
        seam = _Seam()

        await seam.stream_tool_result(_OVERSIZED_OUTPUT)

        notes = seam.of_type(
            await seam.replayed_events(), RuntimeApiEventType.COMPRESSION_NOTE
        )
        assert len(notes) == 1
        assert notes[0].source is StreamEventSource.RUNTIME

    async def test_counts_are_measured_at_the_admission_seam(self) -> None:
        """before/after are what the runtime DID, not what a policy intended.

        ``after`` must be strictly smaller than ``before`` -- that difference is
        the whole claim the divider makes -- and ``tokens_saved`` must equal it,
        because the producer derives it rather than accepting it.
        """

        seam = _Seam()

        await seam.stream_tool_result(_OVERSIZED_OUTPUT)

        payload = seam.of_type(
            await seam.replayed_events(), RuntimeApiEventType.COMPRESSION_NOTE
        )[0].payload
        before = payload["before_tokens"]
        after = payload["after_tokens"]
        assert before > after >= 0
        assert payload["tokens_saved"] == before - after
        assert payload["strategy"] == ContextCompressionStrategy.OFFLOAD.value
        assert payload["trigger"] == MemoryValues.CompactionTrigger.TOKEN_THRESHOLD
        assert payload["tool_name"] == _TOOL_NAME

    async def test_note_precedes_the_tool_result_it_explains(self) -> None:
        """Ordering is the readable one: "content was compacted, here it is".

        Also the sealing property. The run's terminal event seals the causal
        prefix ``[1..N]``; emitting in this same pre-terminal pass is what keeps
        the note provably inside that prefix instead of racing the seal.
        """

        seam = _Seam()

        await seam.stream_tool_result(_OVERSIZED_OUTPUT)

        events = await seam.replayed_events()
        note = seam.of_type(events, RuntimeApiEventType.COMPRESSION_NOTE)[0]
        result = seam.of_type(events, RuntimeApiEventType.TOOL_RESULT)[0]
        assert note.sequence_no < result.sequence_no

    async def test_the_result_the_model_got_really_was_bounded(self) -> None:
        """The divider is not describing a compaction that did not happen."""

        seam = _Seam()

        await seam.stream_tool_result(_OVERSIZED_OUTPUT)

        events = await seam.replayed_events()
        result = seam.of_type(events, RuntimeApiEventType.TOOL_RESULT)[0]
        assert result.payload.get(Keys.Field.OUTPUT_REF)
        assert len(str(result.payload[Keys.Field.OUTPUT])) < len(_OVERSIZED_OUTPUT)


class TestPresentationBoundaryProjectsTheLabel:
    """Claim 2: the label is computed server-side, once, at the append funnel."""

    async def test_display_title_is_projected_onto_the_persisted_envelope(
        self,
    ) -> None:
        """A client must never derive this from the event name.

        ``display_title`` is computed by the projector inside ``append_api_event``
        and PERSISTED, so every reader -- live SSE and replay alike -- gets the
        same server-authored sentence.
        """

        seam = _Seam()

        await seam.stream_tool_result(_OVERSIZED_OUTPUT)

        note = seam.of_type(
            await seam.replayed_events(), RuntimeApiEventType.COMPRESSION_NOTE
        )[0]
        assert note.display_title == Messages.Event.compaction_title(
            tokens_saved=note.payload["tokens_saved"],
            tool_name=_TOOL_NAME,
        )
        assert _TOOL_NAME in note.display_title
        assert note.display_title != Messages.Event.COMPRESSION_NOTE

    async def test_note_is_projected_as_an_inline_note_not_a_tool_card(self) -> None:
        seam = _Seam()

        await seam.stream_tool_result(_OVERSIZED_OUTPUT)

        note = seam.of_type(
            await seam.replayed_events(), RuntimeApiEventType.COMPRESSION_NOTE
        )[0]
        assert note.activity_kind == "note"

    @pytest.mark.parametrize(
        ("tokens_saved", "tool_name", "expected"),
        [
            (940, "grep", "Compacted 940 tokens of grep output"),
            (1_500, "read_file", "Compacted 1.5k tokens of read_file output"),
            (8_632, "read_file", "Compacted 8.6k tokens of read_file output"),
            (12_400, "read_file", "Compacted 12k tokens of read_file output"),
            (2_000, None, "Compacted 2k tokens of tool output"),
        ],
    )
    def test_title_names_the_tool_and_scales_the_count(
        self, tokens_saved: int, tool_name: str | None, expected: str
    ) -> None:
        """Naming the tool is the point: it says WHICH result the model lost."""

        assert (
            Messages.Event.compaction_title(
                tokens_saved=tokens_saved, tool_name=tool_name
            )
            == expected
        )

    def test_projector_falls_back_when_counts_are_missing_or_stale(self) -> None:
        """An old persisted row replayed through today's projector.

        Rows written before ``tokens_saved`` existed must still project a title
        rather than raising or rendering an absurd one.
        """

        legacy = RuntimeEventPresentationProjector.presentation_fields(
            event_type=RuntimeApiEventType.COMPRESSION_NOTE,
            source=StreamEventSource.RUNTIME,
            parent_task_id=None,
            payload={"before_tokens": 900, "after_tokens": 900, "strategy": "offload"},
            metadata={},
            subagent_id=None,
        )
        assert legacy[Keys.Field.DISPLAY_TITLE] == Messages.Event.COMPRESSION_NOTE

        derived = RuntimeEventPresentationProjector.presentation_fields(
            event_type=RuntimeApiEventType.COMPRESSION_NOTE,
            source=StreamEventSource.RUNTIME,
            parent_task_id=None,
            payload={
                "before_tokens": 10_000,
                "after_tokens": 1_000,
                "strategy": "offload",
                "tool_name": "read_file",
            },
            metadata={},
            subagent_id=None,
        )
        assert (
            derived[Keys.Field.DISPLAY_TITLE]
            == "Compacted 9k tokens of read_file output"
        )


class TestNoCompactionEmitsNothing:
    """Claim 3: the divider marks a real boundary, or it is not drawn."""

    async def test_small_tool_result_emits_no_note(self) -> None:
        seam = _Seam()

        await seam.stream_tool_result(_SMALL_OUTPUT)

        events = await seam.replayed_events()
        assert seam.of_type(events, RuntimeApiEventType.COMPRESSION_NOTE) == []
        assert seam.of_type(events, RuntimeApiEventType.TOOL_RESULT)

    async def test_backend_without_an_offloader_emits_no_note(self) -> None:
        """Postgres / in-memory topologies keep behaving exactly as before."""

        seam = _Seam()
        seam.processor = StreamMessageProcessor(
            event_producer=seam.producer,
            update_processor=StreamUpdateProcessor(event_producer=seam.producer),
            tool_result_offloader=None,
        )

        await seam.stream_tool_result(_OVERSIZED_OUTPUT)

        events = await seam.replayed_events()
        assert seam.of_type(events, RuntimeApiEventType.COMPRESSION_NOTE) == []
        assert seam.of_type(events, RuntimeApiEventType.TOOL_RESULT)

    def test_inline_admission_is_rejected_by_strategy_not_arithmetic(self) -> None:
        """An inline admission is the model seeing everything.

        That is the opposite of the fact this event reports, so it is refused on
        strategy even before the token difference is consulted.
        """

        admission = ToolResultAdmissionAdapter(InMemoryOffloadWriter()).admit(
            _SMALL_OUTPUT,
            trace_id="trace_compaction",
        )
        assert admission.strategy is ContextCompressionStrategy.INLINE
        assert CompactionNotice.from_admission(admission) is None


class TestNoteSurvivesReplay:
    """Claim 4: a transcript part, not a toast.

    A live SSE subscriber sees every event once. What makes this a boundary the
    transcript OWNS is that a client which was not connected -- a reload, a
    resumed session, a second device -- reads the identical event, with the
    identical server-authored label, out of the persisted ledger.
    """

    async def test_replay_returns_the_note_with_its_projected_label(self) -> None:
        seam = _Seam()
        await seam.stream_tool_result(_OVERSIZED_OUTPUT)

        # A cold reader: nothing retained from the live pass.
        replayed = seam.of_type(
            await seam.replayed_events(), RuntimeApiEventType.COMPRESSION_NOTE
        )
        assert len(replayed) == 1
        note = replayed[0]
        assert note.event_type is RuntimeApiEventType.COMPRESSION_NOTE
        assert note.activity_kind == "note"
        assert _TOOL_NAME in note.display_title
        assert note.payload["tokens_saved"] == (
            note.payload["before_tokens"] - note.payload["after_tokens"]
        )

    async def test_resuming_after_the_note_does_not_replay_it(self) -> None:
        """Reconnect semantics: ``after_sequence`` still means what it means."""

        seam = _Seam()
        await seam.stream_tool_result(_OVERSIZED_OUTPUT)

        note = seam.of_type(
            await seam.replayed_events(), RuntimeApiEventType.COMPRESSION_NOTE
        )[0]
        resumed = await seam.store.list_events_after(
            org_id=_ORG_ID,
            run_id=_RUN_ID,
            after_sequence=note.sequence_no,
        )
        assert seam.of_type(resumed, RuntimeApiEventType.COMPRESSION_NOTE) == []

    async def test_note_is_inside_the_runs_causal_prefix(self) -> None:
        """It is sequenced like every other causal fact, so the seal covers it.

        The terminal event seals ``[1..N]``. A note that landed outside that
        prefix would be an event no live client can ever receive; one inside it
        is sealed with everything else.
        """

        seam = _Seam()
        await seam.stream_tool_result(_OVERSIZED_OUTPUT)

        events = await seam.replayed_events()
        sequences = [event.sequence_no for event in events]
        assert sequences == sorted(sequences)
        note = seam.of_type(events, RuntimeApiEventType.COMPRESSION_NOTE)[0]
        assert 1 <= note.sequence_no <= max(sequences)
