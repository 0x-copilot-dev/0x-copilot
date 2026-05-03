from __future__ import annotations

import json
from types import SimpleNamespace

from agent_runtime.execution.contracts import AgentRuntimeContext
from runtime_api.schemas import RunRecord
from runtime_api.schemas import RuntimeApiEventType
from runtime_worker.stream_events import StreamOrchestrator
from runtime_worker.stream_messages import StreamMessageParser
from runtime_worker.stream_parts import StreamNamespace, StreamPartParser
from runtime_worker.stream_subagents import StreamUpdateProcessor
from runtime_worker.stream_tools import StreamMessageProcessor


class RecordingEventProducer:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def append_api_event(self, **kwargs: object) -> None:
        self.events.append(kwargs)


class TestFixtures:
    @staticmethod
    def run_record() -> RunRecord:
        return RunRecord(
            run_id="run_123",
            conversation_id="conversation_123",
            org_id="org_123",
            user_id="user_123",
            user_message_id="message_123",
            trace_id="trace_123",
            model_provider="openai",
            model_name="gpt-5.4-mini",
            runtime_context=AgentRuntimeContext(
                user_id="user_123",
                org_id="org_123",
                roles=["employee"],
                model_profile={
                    "provider": "openai",
                    "model_name": "gpt-5.4-mini",
                    "max_input_tokens": 128000,
                    "timeout_seconds": 30,
                    "temperature": 0,
                    "supports_streaming": True,
                },
                run_id="run_123",
                trace_id="trace_123",
            ),
        )


def test_stream_part_parser_normalizes_namespace_metadata() -> None:
    part = StreamPartParser.stream_part(
        {
            "type": "updates",
            "ns": ("tools:task_123", "model_request:req_456"),
            "data": {"status": "running"},
        }
    )

    assert part is not None
    assert StreamPartParser.stream_type(part) == "updates"
    namespace = StreamPartParser.namespace_for(part)
    assert namespace == StreamNamespace(("tools:task_123", "model_request:req_456"))
    assert namespace.subagent_task_id == "task_123"
    assert namespace.metadata("updates") == {
        "stream_type": "updates",
        "namespace": ["tools:task_123", "model_request:req_456"],
    }


def test_explicit_api_payloads_are_collected_from_nested_payloads() -> None:
    payloads = StreamMessageParser.explicit_api_payloads(
        {
            "model": {
                "events": [
                    {
                        "api_event_type": "reasoning_summary_delta",
                        "summary": "Checking source coverage",
                    }
                ]
            }
        }
    )

    assert len(payloads) == 1
    assert StreamMessageParser.api_event_type(payloads[0]) is (
        RuntimeApiEventType.REASONING_SUMMARY_DELTA
    )
    assert payloads[0]["summary"] == "Checking source coverage"


def test_explicit_api_payloads_are_collected_from_json_string_content() -> None:
    payloads = StreamMessageParser.explicit_api_payloads(
        {
            "type": "tool",
            "content": json.dumps(
                {
                    "api_event_type": "reasoning_summary_delta",
                    "summary": "Checking source coverage",
                }
            ),
        }
    )

    assert len(payloads) == 1
    assert StreamMessageParser.api_event_type(payloads[0]) is (
        RuntimeApiEventType.REASONING_SUMMARY_DELTA
    )
    assert payloads[0]["summary"] == "Checking source coverage"


def test_explicit_api_payloads_are_collected_from_tool_message_objects() -> None:
    payloads = StreamMessageParser.explicit_api_payloads(
        (
            SimpleNamespace(
                type="tool",
                name="progress_tool",
                tool_call_id="call_progress_123",
                content=json.dumps(
                    {
                        "api_event_type": "progress",
                        "message": "Still working.",
                    }
                ),
            ),
            {},
        )
    )

    assert len(payloads) == 1
    assert StreamMessageParser.api_event_type(payloads[0]) is (
        RuntimeApiEventType.PROGRESS
    )
    assert payloads[0]["message"] == "Still working."


def test_native_mcp_interrupt_payloads_project_to_approval() -> None:
    payloads = StreamOrchestrator.native_tool_approval_payloads(
        interrupt_id="interrupt_123",
        interrupt_value={
            "action_requests": [
                {
                    "name": "call_mcp_tool",
                    "args": {
                        "server_name": "mcp_clickup_com",
                        "tool_name": "list_tasks",
                        "arguments": {"assignee": "me"},
                    },
                }
            ],
            "review_configs": [
                {
                    "action_name": "call_mcp_tool",
                    "allowed_decisions": ["approve", "reject"],
                }
            ],
        },
    )

    assert payloads == (
        {
            "api_event_type": "approval_requested",
            "event_type": "approval_requested",
            "approval_id": "interrupt_123",
            "action_id": "interrupt_123",
            "approval_kind": "mcp_tool",
            "native_interrupt_id": "interrupt_123",
            "action_index": 0,
            "action_count": 1,
            "server_name": "mcp_clickup_com",
            "display_name": "ClickUp",
            "tool_name": "list_tasks",
            "arguments": {"assignee": "me"},
            "message": "Allow ClickUp search?",
            "read_only": True,
            "risk_level": "low",
            "status": "pending",
            "allowed_decisions": ["approve", "reject"],
            "grant_options": ["allow_once"],
        },
    )


def test_native_ask_a_question_interrupt_projects_to_approval_requested() -> None:
    run = TestFixtures.run_record()
    payloads = StreamOrchestrator.native_interrupt_payloads(
        run,
        {
            "__interrupt__": [
                {
                    "id": "ask_interrupt_42",
                    "value": {
                        "api_event_type": "approval_requested",
                        "event_type": "approval_requested",
                        "approval_kind": "ask_a_question",
                        "approval_id": "ask_a_question:run_123:trace_123",
                        "question": "Where would you like to travel?",
                        "options": ["Tokyo", "Paris"],
                        "status": "pending",
                    },
                }
            ]
        },
    )

    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["api_event_type"] == "approval_requested"
    assert payload["approval_kind"] == "ask_a_question"
    assert payload["question"] == "Where would you like to travel?"
    assert payload["native_interrupt_id"] == "ask_interrupt_42"
    assert payload["approval_id"] == "ask_a_question:run_123:trace_123"


def test_tool_call_state_merges_incremental_chunks_with_stable_identity() -> None:
    producer = object()
    update_processor = StreamUpdateProcessor(event_producer=producer)  # type: ignore[arg-type]
    processor = StreamMessageProcessor(
        event_producer=producer, update_processor=update_processor
    )  # type: ignore[arg-type]
    namespace = StreamNamespace.from_value(())

    first = processor.tool_call_state(
        "run_123",
        namespace,
        {
            "name": "write_todos",
            "id": "call_123",
            "index": 0,
            "args": {"delta": ""},
        },
    )
    second = processor.tool_call_state(
        "run_123",
        namespace,
        {
            "index": 0,
            "args": {"delta": '{"todos":[{"content":"check prime"}]}'},
        },
    )

    assert second is first
    payload = StreamMessageProcessor.tool_call_payload_from_state(second)
    assert payload["tool_name"] == "write_todos"
    assert payload["call_id"] == "call_123"
    assert payload["args"] == {"todos": "check prime"}


def test_large_result_file_tools_are_internal_only_for_virtual_paths() -> None:
    large_payload = StreamMessageProcessor.tool_call_payload(
        {
            "name": "read_file",
            "id": "call_large",
            "args": {"file_path": "/large_tool_results/call_123"},
        }
    )
    normal_payload = StreamMessageProcessor.tool_call_payload(
        {
            "name": "read_file",
            "id": "call_project",
            "args": {"file_path": "src/app.ts"},
        }
    )

    assert large_payload["visibility"] == "internal"
    assert "visibility" not in normal_payload


def test_large_result_file_tool_results_inherit_internal_visibility() -> None:
    producer = object()
    update_processor = StreamUpdateProcessor(event_producer=producer)  # type: ignore[arg-type]
    processor = StreamMessageProcessor(
        event_producer=producer, update_processor=update_processor
    )  # type: ignore[arg-type]
    namespace = StreamNamespace.from_value(())

    processor.tool_call_state(
        "run_123",
        namespace,
        {
            "name": "read_file",
            "id": "call_large",
            "args": {"file_path": "/large_tool_results/call_123"},
        },
    )
    payload = processor.tool_result_payload_with_state(
        "run_123",
        {
            "tool_name": "unknown_tool",
            "call_id": "call_large",
            "output": {"content": "large payload"},
        },
    )

    assert payload["visibility"] == "internal"


async def test_streamed_large_result_file_tool_does_not_emit_visible_start() -> None:
    producer = RecordingEventProducer()
    orchestrator = StreamOrchestrator(event_producer=producer)  # type: ignore[arg-type]
    namespace = StreamNamespace.from_value(())
    run = TestFixtures.run_record()

    await orchestrator.message_processor.append_tool_call_chunk_event(
        run=run,
        namespace=namespace,
        tool_call={
            "name": "read_file",
            "id": "call_large",
            "index": 0,
            "args": {"delta": ""},
        },
        metadata={},
        parent_task_id=None,
    )
    await orchestrator.message_processor.append_tool_call_chunk_event(
        run=run,
        namespace=namespace,
        tool_call={
            "index": 0,
            "args": {
                "delta": json.dumps(
                    {
                        "file_path": "/large_tool_results/call_mcp_result",
                        "offset": 0,
                        "limit": 120,
                    }
                )
            },
        },
        metadata={},
        parent_task_id=None,
    )

    assert len(producer.events) == 1
    event = producer.events[0]
    assert event["event_type"] is RuntimeApiEventType.TOOL_CALL_STARTED
    assert event["payload"]["visibility"] == "internal"
    assert event["payload"]["call_id"] == "call_large"
    assert event["payload"]["args"]["file_path"] == (
        "/large_tool_results/call_mcp_result"
    )


async def test_streamed_normal_file_tool_emits_visible_start_after_path_is_known() -> (
    None
):
    producer = RecordingEventProducer()
    orchestrator = StreamOrchestrator(event_producer=producer)  # type: ignore[arg-type]
    namespace = StreamNamespace.from_value(())
    run = TestFixtures.run_record()

    await orchestrator.message_processor.append_tool_call_chunk_event(
        run=run,
        namespace=namespace,
        tool_call={
            "name": "read_file",
            "id": "call_project",
            "index": 0,
            "args": {"delta": ""},
        },
        metadata={},
        parent_task_id=None,
    )
    await orchestrator.message_processor.append_tool_call_chunk_event(
        run=run,
        namespace=namespace,
        tool_call={
            "index": 0,
            "args": {"delta": json.dumps({"file_path": "src/app.ts"})},
        },
        metadata={},
        parent_task_id=None,
    )

    assert len(producer.events) == 1
    event = producer.events[0]
    assert event["event_type"] is RuntimeApiEventType.TOOL_CALL_STARTED
    assert "visibility" not in event["payload"]
    assert event["payload"]["call_id"] == "call_project"
    assert event["payload"]["args"]["file_path"] == "src/app.ts"


def test_task_tool_updates_project_to_subagent_lifecycle_payloads() -> None:
    started = StreamUpdateProcessor.task_tool_call_payloads(
        {
            "model_request": {
                "messages": [
                    {
                        "tool_calls": [
                            {
                                "name": "task",
                                "id": "task_abc",
                                "args": {
                                    "subagent_type": "researcher",
                                    "description": "Research launch risks.",
                                },
                            }
                        ]
                    }
                ]
            }
        }
    )
    completed = StreamUpdateProcessor.task_tool_result_payloads(
        {
            "tools": {
                "messages": [
                    {
                        "type": "tool",
                        "name": "task",
                        "tool_call_id": "task_abc",
                        "content": "Research complete.",
                    }
                ]
            }
        }
    )

    assert started == (
        {
            "task_id": "task_abc",
            "subagent_name": "researcher",
            "status": "queued",
            "summary": "Research launch risks.",
            "short_summary": "Researching launch risks.",
            "display_title": "Researching launch risks.",
        },
    )
    assert completed == (
        {
            "task_id": "task_abc",
            "subagent_name": "subagent",
            "status": "completed",
            "summary": "Research complete.",
        },
    )


async def test_subagent_id_for_subgraph_links_first_child_event_to_started_call() -> (
    None
):
    """First tool event in a subagent's subgraph claims the oldest unlinked call_id."""

    producer = RecordingEventProducer()
    update_processor = StreamUpdateProcessor(event_producer=producer)  # type: ignore[arg-type]
    run = TestFixtures.run_record()

    await update_processor.append_task_lifecycle_event(
        run=run,
        event_type=RuntimeApiEventType.SUBAGENT_STARTED,
        payload={
            "task_id": "call_supervisor_abc",
            "subagent_name": "general-purpose",
            "status": "queued",
        },
        metadata={},
    )

    # First child tool event in this subgraph (LangGraph generates a UUID-style id)
    resolved = update_processor.subagent_id_for_subgraph(
        run_id=run.run_id,
        subgraph_task_id="3af7da77-f445-1753-7487-5b703bd06945",
    )
    assert resolved == "general-purpose"

    # Subsequent child events for the same subgraph reuse the link.
    again = update_processor.subagent_id_for_subgraph(
        run_id=run.run_id,
        subgraph_task_id="3af7da77-f445-1753-7487-5b703bd06945",
    )
    assert again == "general-purpose"

    # SUBAGENT_STARTED itself was emitted with the explicit subagent_id kwarg.
    started_events = [
        event
        for event in producer.events
        if event["event_type"] is RuntimeApiEventType.SUBAGENT_STARTED
    ]
    assert len(started_events) == 1
    assert started_events[0]["subagent_id"] == "general-purpose"


async def test_subagent_id_for_subgraph_returns_none_without_active_subagent() -> None:
    producer = object()
    update_processor = StreamUpdateProcessor(event_producer=producer)  # type: ignore[arg-type]

    assert (
        update_processor.subagent_id_for_subgraph(
            run_id="run_123", subgraph_task_id="some-uuid"
        )
        is None
    )
    assert (
        update_processor.subagent_id_for_subgraph(
            run_id="run_123", subgraph_task_id=None
        )
        is None
    )


async def test_subagent_id_for_subgraph_defers_ambiguous_links() -> None:
    """When two subagents are dispatched in parallel and one finishes before
    the other's first tool fires (e.g. an `is_prime` writer that emits no
    tool calls plus a long research subagent), naive FIFO popping mis-
    attributes the long subagent's tools to the writer. The defer-while-
    ambiguous strategy returns None until exactly one subagent remains
    unlinked, then locks onto it for the rest of that subgraph's events."""

    producer = RecordingEventProducer()
    update_processor = StreamUpdateProcessor(event_producer=producer)  # type: ignore[arg-type]
    run = TestFixtures.run_record()

    for call_id, name in [
        ("call_writer", "general-purpose"),
        ("call_researcher", "general-purpose"),
    ]:
        await update_processor.append_task_lifecycle_event(
            run=run,
            event_type=RuntimeApiEventType.SUBAGENT_STARTED,
            payload={
                "task_id": call_id,
                "subagent_name": name,
                "status": "queued",
            },
            metadata={},
        )

    # First tool event lands while BOTH subagents are still unlinked.
    # We refuse to guess; orphan rather than mis-attribute.
    deferred = update_processor.subagent_call_id_for_subgraph(
        run_id=run.run_id, subgraph_task_id="research-subgraph-uuid"
    )
    assert deferred is None

    # The fast subagent (`call_writer`, no tool calls) completes first.
    await update_processor.append_task_lifecycle_event(
        run=run,
        event_type=RuntimeApiEventType.SUBAGENT_COMPLETED,
        payload={
            "task_id": "call_writer",
            "subagent_name": "general-purpose",
            "status": "completed",
        },
        metadata={},
    )

    # Now only `call_researcher` is unlinked — the next subgraph event resolves.
    later = update_processor.subagent_call_id_for_subgraph(
        run_id=run.run_id, subgraph_task_id="research-subgraph-uuid"
    )
    assert later == "call_researcher"

    # Subsequent events on the same subgraph keep the link cached.
    again = update_processor.subagent_call_id_for_subgraph(
        run_id=run.run_id, subgraph_task_id="research-subgraph-uuid"
    )
    assert again == "call_researcher"


async def test_subagent_id_for_subgraph_links_immediately_when_only_one_subagent() -> (
    None
):
    """Single-subagent dispatch is unambiguous: link on the first event."""

    producer = RecordingEventProducer()
    update_processor = StreamUpdateProcessor(event_producer=producer)  # type: ignore[arg-type]
    run = TestFixtures.run_record()

    await update_processor.append_task_lifecycle_event(
        run=run,
        event_type=RuntimeApiEventType.SUBAGENT_STARTED,
        payload={
            "task_id": "call_only",
            "subagent_name": "general-purpose",
            "status": "queued",
        },
        metadata={},
    )

    resolved = update_processor.subagent_call_id_for_subgraph(
        run_id=run.run_id, subgraph_task_id="some-subgraph-uuid"
    )
    assert resolved == "call_only"


async def test_tool_event_inside_subagent_carries_subagent_id() -> None:
    """Tool events emitted while a subagent is active include `subagent_id`."""

    producer = RecordingEventProducer()
    orchestrator = StreamOrchestrator(event_producer=producer)  # type: ignore[arg-type]
    run = TestFixtures.run_record()

    # Supervisor dispatches a subagent; SUBAGENT_STARTED is recorded.
    await orchestrator.update_processor.append_task_lifecycle_event(
        run=run,
        event_type=RuntimeApiEventType.SUBAGENT_STARTED,
        payload={
            "task_id": "call_supervisor_abc",
            "subagent_name": "general-purpose",
            "status": "queued",
        },
        metadata={},
    )

    # A tool fires inside the subagent's subgraph. Namespace carries the
    # LangGraph subgraph task id under the `tools:` prefix.
    namespace = StreamNamespace.from_value(("tools:3af7da77-f445",))
    await orchestrator.message_processor.process(
        run=run,
        namespace=namespace,
        message={
            "type": "ai",
            "content": "",
            "tool_calls": [
                {
                    "name": "web_search",
                    "id": "call_websearch_1",
                    "index": 0,
                    "args": {"query": "AI agents"},
                }
            ],
        },
        delta=None,
    )

    tool_events = [
        event
        for event in producer.events
        if event["event_type"] is RuntimeApiEventType.TOOL_CALL_STARTED
    ]
    assert len(tool_events) == 1
    event = tool_events[0]
    assert event["payload"]["tool_name"] == "web_search"
    # parent_task_id is the supervisor's `task` call_id (not the raw subgraph
    # UUID), so the frontend can group child tool events under the
    # `subagent_started` card via a shared identifier.
    assert event["parent_task_id"] == "call_supervisor_abc"
    assert event["subagent_id"] == "general-purpose"


def test_task_tool_payload_includes_concise_user_facing_summary() -> None:
    payload = StreamUpdateProcessor.task_tool_call_payload(
        call_id="task_report",
        args_payload={
            "subagent_type": "general-purpose",
            "description": (
                "Create a formal research report on the phrase/concept "
                "'health is wealth'. Investigate and synthesize evidence for "
                "how health affects economic outcomes. Provide: executive "
                "summary, evidence, policy implications, and references."
            ),
        },
    )

    assert payload["summary"].startswith("Create a formal research report")
    assert payload["short_summary"] == (
        "Preparing a formal research report on the phrase/concept 'health is wealth'."
    )
    assert payload["display_title"] == payload["short_summary"]
    assert len(str(payload["short_summary"])) <= 120
