"""``write_todos`` reaches the client as a checklist snapshot, not a tool card."""

from __future__ import annotations

import json

from agent_runtime.execution.contracts import AgentRuntimeContext
from runtime_api.schemas import RunRecord, RuntimeApiEventType
from runtime_worker.stream_messages import StreamMessageParser
from runtime_worker.stream_parts import StreamNamespace
from runtime_worker.stream_subagents import StreamUpdateProcessor
from runtime_worker.stream_tools import StreamMessageProcessor


#: The checklist from benchmark run ``2d30ca71ad2b4d8da58ee88deb9d4036``, whose
#: fourth ``write_todos`` call was reported as a tool crash. Recovered from that
#: run's ``tool_invocations.jsonl`` result summaries and its ``tool_call_delta``
#: argument buffers.
_RECORDED_STEPS = (
    "List three European capitals",
    "Identify the country of each capital",
    "Name one river in each country",
    "Produce final table of all three rows",
)
_RECORDED_SEQUENCE = (
    ("pending", "pending", "pending", "pending"),
    ("completed", "in_progress", "pending", "pending"),
    ("completed", "completed", "in_progress", "pending"),
    ("completed", "completed", "completed", "in_progress"),
)
#: What that run's ledger row stored for the SAME argument — every row's
#: ``content`` concatenated, every ``status`` gone.
_LEDGER_FLATTENED_TODOS = "".join(_RECORDED_STEPS)


def _recorded_todos(statuses: tuple[str, ...]) -> list[dict[str, str]]:
    """One call of the recorded sequence, as the model sent it."""
    return [
        {"content": content, "status": status}
        for content, status in zip(_RECORDED_STEPS, statuses, strict=True)
    ]


class RecordingEventProducer:
    """Collect every appended event so a test can assert the emitted stream."""

    def __init__(self) -> None:
        """Start with an empty event log."""
        self.events: list[dict[str, object]] = []

    async def append_api_event(self, **kwargs: object) -> None:
        """Record one appended event."""
        self.events.append(kwargs)


class _WriteTodosDriverMixin:
    """Drive a ``write_todos`` call through the processor exactly as the stream does."""

    TODOS = [
        {"content": "Pull the Q3 pipeline export", "status": "in_progress"},
        {"content": "Reconcile opportunity ids", "status": "pending"},
    ]

    @staticmethod
    def processor(producer: RecordingEventProducer) -> StreamMessageProcessor:
        """Build a processor wired to a recording producer."""
        update_processor = StreamUpdateProcessor(event_producer=producer)  # type: ignore[arg-type]
        return StreamMessageProcessor(
            event_producer=producer,  # type: ignore[arg-type]
            update_processor=update_processor,
        )

    @staticmethod
    def run_record() -> RunRecord:
        """Minimal run record for the stream seams under test."""
        return RunRecord(
            run_id="run_todo",
            conversation_id="conversation_todo",
            org_id="org_1",
            user_id="user_1",
            user_message_id="message_1",
            trace_id="trace_1",
            model_provider="openai",
            model_name="gpt-5.4-mini",
            runtime_context=AgentRuntimeContext(
                user_id="user_1",
                org_id="org_1",
                roles=["employee"],
                model_profile={
                    "provider": "openai",
                    "model_name": "gpt-5.4-mini",
                    "max_input_tokens": 128000,
                    "timeout_seconds": 30,
                    "temperature": 0,
                    "supports_streaming": True,
                },
                run_id="run_todo",
                trace_id="trace_1",
            ),
        )

    async def write_todos(
        self,
        processor: StreamMessageProcessor,
        run: RunRecord,
        *,
        call_id: str,
        todos: list[dict[str, str]],
        streamed: bool = False,
    ) -> None:
        """Start and settle one ``write_todos`` call.

        ``streamed`` picks the provider path where arguments arrive as JSON text
        deltas rather than one complete mapping; both must reach the projector
        with the list structure intact.
        """
        namespace = StreamNamespace.from_value(())
        args: object = (
            {"delta": json.dumps({"todos": todos})} if streamed else {"todos": todos}
        )
        await processor.append_tool_call_chunk_event(
            run=run,
            namespace=namespace,
            tool_call={"name": "write_todos", "id": call_id, "args": args},
            metadata={},
            parent_task_id=None,
        )
        await processor.process(
            run=run,
            namespace=namespace,
            message={
                "type": "tool",
                "tool_call_id": call_id,
                "name": "write_todos",
                "status": "success",
                "output": f"Updated todo list to {todos}",
            },
            delta=None,
        )

    @staticmethod
    def todo_events(producer: RecordingEventProducer) -> list[dict[str, object]]:
        """Every ``todo_list_updated`` payload the producer received, in order."""
        return [
            event["payload"]  # type: ignore[misc]
            for event in producer.events
            if event["event_type"] is RuntimeApiEventType.TODO_LIST_UPDATED
        ]


class TestRawArgumentsSurviveCoercion:
    """The display coercion flattens nested arguments; structured reads must not use it."""

    def test_payload_mapping_flattens_a_list_of_objects_to_text(self) -> None:
        # Not a hypothetical: ``json_value`` sends any list of mappings through
        # the content-block fallback, which harvests their ``content`` keys and
        # concatenates them. A todo list is exactly that shape, which is why the
        # tool card rendered one run-on string where a checklist should be.
        flattened = StreamMessageParser.payload_mapping(
            {
                "todos": [
                    {"content": "first", "status": "pending"},
                    {"content": "second", "status": "pending"},
                ]
            }
        )

        assert flattened["todos"] == "firstsecond"

    def test_raw_args_preserves_the_structure(self) -> None:
        tool_call = {
            "name": "write_todos",
            "args": {"todos": [{"content": "first", "status": "pending"}]},
        }

        raw = StreamMessageParser.raw_args(tool_call)

        assert raw == {"todos": [{"content": "first", "status": "pending"}]}


class TestTodoListEventEmission(_WriteTodosDriverMixin):
    """The panel's only data source is this event."""

    async def test_complete_argument_mapping_emits_the_checklist(self) -> None:
        producer = RecordingEventProducer()
        processor = self.processor(producer)
        await self.write_todos(
            processor, self.run_record(), call_id="call_1", todos=self.TODOS
        )

        [payload] = self.todo_events(producer)
        assert payload["generation"] == 1
        assert payload["todos"] == [
            {"content": "Pull the Q3 pipeline export", "status": "in_progress"},
            {"content": "Reconcile opportunity ids", "status": "pending"},
        ]

    async def test_streamed_arguments_emit_the_same_checklist(self) -> None:
        producer = RecordingEventProducer()
        processor = self.processor(producer)
        await self.write_todos(
            processor,
            self.run_record(),
            call_id="call_1",
            todos=self.TODOS,
            streamed=True,
        )

        [payload] = self.todo_events(producer)
        assert payload["todos"] == [
            {"content": "Pull the Q3 pipeline export", "status": "in_progress"},
            {"content": "Reconcile opportunity ids", "status": "pending"},
        ]

    async def test_the_tool_frames_themselves_stay_internal(self) -> None:
        # The checklist event is the public rendering of ``write_todos``. If the
        # raw frames were also visible the run would show both the panel and the
        # card it replaces.
        producer = RecordingEventProducer()
        processor = self.processor(producer)
        await self.write_todos(
            processor, self.run_record(), call_id="call_1", todos=self.TODOS
        )

        for event in producer.events:
            payload = event["payload"]
            assert isinstance(payload, dict)
            if payload.get("tool_name") == "write_todos":
                assert payload["visibility"] == "internal"
        assert len(self.todo_events(producer)) == 1

    async def test_finishing_a_list_then_writing_again_opens_generation_two(
        self,
    ) -> None:
        producer = RecordingEventProducer()
        processor = self.processor(producer)
        run = self.run_record()
        await self.write_todos(
            processor,
            run,
            call_id="call_1",
            todos=[{"content": "only step", "status": "in_progress"}],
        )
        await self.write_todos(
            processor,
            run,
            call_id="call_2",
            todos=[{"content": "only step", "status": "completed"}],
        )
        await self.write_todos(
            processor,
            run,
            call_id="call_3",
            todos=[{"content": "follow-up work", "status": "in_progress"}],
        )

        generations = [payload["generation"] for payload in self.todo_events(producer)]
        assert generations == [1, 1, 2]

    async def test_a_malformed_list_emits_no_event_and_does_not_fail_the_run(
        self,
    ) -> None:
        producer = RecordingEventProducer()
        processor = self.processor(producer)
        await self.write_todos(
            processor,
            self.run_record(),
            call_id="call_1",
            todos=[{"content": "step", "status": "blocked"}],
        )

        assert self.todo_events(producer) == []
        # The tool's own frames still flow; only the projection is suppressed.
        assert any(
            event["event_type"] is RuntimeApiEventType.TOOL_RESULT
            for event in producer.events
        )

    async def test_the_recorded_four_call_sequence_emits_four_checklists(self) -> None:
        # The sequence a live benchmark reported as a ``write_todos`` crash on
        # its fourth call, replayed through the seam on the provider path it
        # actually took (arguments as JSON text deltas). Four calls in, four
        # checklists out, and nothing raised — the tool was not the fault.
        producer = RecordingEventProducer()
        processor = self.processor(producer)
        run = self.run_record()
        for index, statuses in enumerate(_RECORDED_SEQUENCE):
            await self.write_todos(
                processor,
                run,
                call_id=f"call_{index}",
                todos=_recorded_todos(statuses),
                streamed=True,
            )

        payloads = self.todo_events(producer)
        assert [payload["generation"] for payload in payloads] == [1, 1, 1, 1]
        assert payloads[-1]["todos"] == _recorded_todos(_RECORDED_SEQUENCE[-1])

    async def test_a_streamed_list_arrives_as_rows_not_as_one_run_on_string(
        self,
    ) -> None:
        # The trap that would have hidden the list: read a tool's arguments off
        # the DISPLAY payload and every ``{"content": ..., "status": ...}`` row
        # collapses into its concatenated ``content`` values, statuses gone. The
        # benchmark's ledger stored exactly that string. The projector must not
        # be reading from there — and the proof is that the statuses survive.
        producer = RecordingEventProducer()
        processor = self.processor(producer)
        todos = _recorded_todos(_RECORDED_SEQUENCE[-1])
        await self.write_todos(
            processor,
            self.run_record(),
            call_id="call_last",
            todos=todos,
            streamed=True,
        )

        [payload] = self.todo_events(producer)
        assert payload["todos"] == todos
        assert [row["status"] for row in payload["todos"]] == list(
            _RECORDED_SEQUENCE[-1]
        )
        # The same argument, flattened the way the display/ledger path does it:
        # byte-for-byte the string that live run's ledger row recorded.
        assert (
            StreamMessageParser.payload_mapping({"todos": todos})["todos"]
            == _LEDGER_FLATTENED_TODOS
        )

    async def test_other_tools_never_emit_a_checklist(self) -> None:
        producer = RecordingEventProducer()
        processor = self.processor(producer)
        run = self.run_record()
        namespace = StreamNamespace.from_value(())
        await processor.append_tool_call_chunk_event(
            run=run,
            namespace=namespace,
            tool_call={
                "name": "read_file",
                "id": "call_read",
                "args": {"file_path": "src/app.ts"},
            },
            metadata={},
            parent_task_id=None,
        )
        await processor.process(
            run=run,
            namespace=namespace,
            message={
                "type": "tool",
                "tool_call_id": "call_read",
                "name": "read_file",
                "status": "success",
                "output": "contents",
            },
            delta=None,
        )

        assert self.todo_events(producer) == []
