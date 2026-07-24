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


def test_reasoning_delta_extracts_anthropic_thinking_block() -> None:
    chunk = {"content": [{"type": "thinking", "thinking": "weighing options"}]}
    assert StreamMessageParser.reasoning_delta(chunk) == "weighing options"


def test_reasoning_delta_extracts_openai_responses_summary_delta() -> None:
    chunk = {
        "content": [
            {"type": "reasoning_summary_text_delta", "text": "summary "},
            {"type": "reasoning_summary_text_delta", "text": "tail"},
        ]
    }
    assert StreamMessageParser.reasoning_delta(chunk) == "summary tail"


def test_reasoning_delta_returns_none_for_plain_text_chunks() -> None:
    assert StreamMessageParser.reasoning_delta({"content": "visible reply"}) is None
    assert (
        StreamMessageParser.reasoning_delta(
            {"content": [{"type": "text", "text": "visible"}]}
        )
        is None
    )


def test_reasoning_finalised_detects_explicit_close_markers() -> None:
    assert StreamMessageParser.reasoning_finalised(
        {"content": [{"type": "thinking", "thinking_signature": "sig"}]}
    )
    assert StreamMessageParser.reasoning_finalised(
        {"content": [{"type": "reasoning_summary_text_done"}]}
    )
    assert not StreamMessageParser.reasoning_finalised(
        {"content": [{"type": "thinking", "thinking": "still going"}]}
    )


async def _drive_emit_reasoning(
    processor: StreamMessageProcessor,
    *,
    namespace: StreamNamespace,
    message: object,
) -> None:
    await processor.emit_reasoning_events(
        run=TestFixtures.run_record(),
        namespace=namespace,
        message=message,
        metadata={},
        parent_task_id=None,
        subagent_id=None,
    )


def test_emit_reasoning_streams_delta_and_caps_on_signature() -> None:
    import asyncio

    producer = RecordingEventProducer()
    update_processor = StreamUpdateProcessor(event_producer=producer)  # type: ignore[arg-type]
    processor = StreamMessageProcessor(
        event_producer=producer, update_processor=update_processor
    )
    main = StreamNamespace.from_value(())

    asyncio.run(
        _drive_emit_reasoning(
            processor,
            namespace=main,
            message={"content": [{"type": "thinking", "thinking": "first "}]},
        )
    )
    asyncio.run(
        _drive_emit_reasoning(
            processor,
            namespace=main,
            message={"content": [{"type": "thinking", "thinking": "second"}]},
        )
    )
    asyncio.run(
        _drive_emit_reasoning(
            processor,
            namespace=main,
            message={"content": [{"type": "thinking", "thinking_signature": "sig"}]},
        )
    )

    event_types = [event["event_type"] for event in producer.events]
    assert event_types == [
        RuntimeApiEventType.REASONING_SUMMARY_DELTA,
        RuntimeApiEventType.REASONING_SUMMARY_DELTA,
        RuntimeApiEventType.REASONING_SUMMARY,
    ]
    assert producer.events[-1]["payload"] == {"summary": "first second"}
    assert "run_123" not in processor._reasoning_buffers


def test_emit_reasoning_drops_subagent_chunks() -> None:
    import asyncio

    producer = RecordingEventProducer()
    update_processor = StreamUpdateProcessor(event_producer=producer)  # type: ignore[arg-type]
    processor = StreamMessageProcessor(
        event_producer=producer, update_processor=update_processor
    )
    sub = StreamNamespace.from_value(("tools:task_42",))

    asyncio.run(
        _drive_emit_reasoning(
            processor,
            namespace=sub,
            message={"content": [{"type": "thinking", "thinking": "subagent"}]},
        )
    )

    assert producer.events == []


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
            # PR #43 — N=1 and N=N follow the same code path. The old
            # bare-interrupt_id special case for single-action interrupts is
            # gone; every item id now follows ``<batch_id>:<index>``.
            "approval_id": "interrupt_123:0",
            "action_id": "interrupt_123:0",
            "approval_kind": "mcp_tool",
            "native_interrupt_id": "interrupt_123",
            "action_index": 0,
            "action_count": 1,
            "batch_id": "interrupt_123",
            "batch_index": 0,
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
            # PR 4.4.6.2 — structured consent-card payload spread alongside
            # the flat fields. Read-only `list_tasks` → READ + first-use.
            "vendor": "CLICKUP",
            "category": "read",
            "reason_code": "read_only_first_use",
            "reversible": "n/a",
            "params": [{"label": "Assignee", "value": "me", "hint": None}],
        },
    )


def test_native_mcp_interrupt_payloads_write_emits_writes_out_of_workspace() -> None:
    payloads = StreamOrchestrator.native_tool_approval_payloads(
        interrupt_id="interrupt_w",
        interrupt_value={
            "action_requests": [
                {
                    "name": "call_mcp_tool",
                    "args": {
                        "server_name": "mcp_slack_com",
                        "tool_name": "post_message",
                        "arguments": {
                            "channel": "#launch-aurora",
                            "text": "Atlas wrote this — should not appear in params",
                            "api_key": "sk-secret-123",
                        },
                    },
                }
            ],
            "review_configs": [],
        },
    )

    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["vendor"] == "SLACK"
    assert payload["category"] == "write"
    assert payload["reason_code"] == "writes_out_of_workspace"
    # PR 4.4.6.4 — Slack post_message opts into the 60s undo window.
    assert payload["reversible"] == "yes"
    # PR 4.4.6.3 — Slack recogniser owns this vendor; only `channel`
    # projects (no thread_ts in args). `text` and `api_key` never appear.
    assert payload["params"] == [
        {"label": "Channel", "value": "#launch-aurora", "hint": None}
    ]


def test_native_mcp_interrupt_payloads_github_recogniser_composes_repo_and_pr() -> None:
    # PR 4.4.6.3 — owner + repo + pull_number compose into a single
    # ``Repo: acme/api · #42`` row instead of three split rows.
    payloads = StreamOrchestrator.native_tool_approval_payloads(
        interrupt_id="interrupt_gh",
        interrupt_value={
            "action_requests": [
                {
                    "name": "call_mcp_tool",
                    "args": {
                        "server_name": "mcp_github_com",
                        "tool_name": "create_pull_review_comment",
                        "arguments": {
                            "owner": "acme",
                            "repo": "api",
                            "pull_number": 42,
                        },
                    },
                }
            ],
            "review_configs": [],
        },
    )

    payload = payloads[0]
    assert payload["params"] == [
        {"label": "Repo", "value": "acme/api · #42", "hint": None}
    ]


def test_native_mcp_interrupt_payloads_unknown_vendor_falls_back_to_generic() -> None:
    # PR 4.4.6.3 — a custom-URL server has no recogniser; the worker
    # falls through to the Phase-2 allow-list projector.
    payloads = StreamOrchestrator.native_tool_approval_payloads(
        interrupt_id="interrupt_acme",
        interrupt_value={
            "action_requests": [
                {
                    "name": "call_mcp_tool",
                    "args": {
                        "server_name": "mcp_acme_internal_com",
                        "tool_name": "list_widgets",
                        "arguments": {"team": "Core"},
                    },
                }
            ],
            "review_configs": [],
        },
    )

    payload = payloads[0]
    # Generic projector capitalises the key as-is.
    assert payload["params"] == [{"label": "Team", "value": "Core", "hint": None}]


def test_native_mcp_interrupt_payloads_param_count_capped_at_six() -> None:
    # PR 4.4.6.3 — Linear has a recogniser now; use a no-recogniser
    # vendor (clickup) to exercise the generic allow-list cap.
    payloads = StreamOrchestrator.native_tool_approval_payloads(
        interrupt_id="interrupt_cap",
        interrupt_value={
            "action_requests": [
                {
                    "name": "call_mcp_tool",
                    "args": {
                        "server_name": "mcp_clickup_com",
                        "tool_name": "list_tasks",
                        "arguments": {
                            "team": "Core",
                            "project": "Atlas",
                            "assignee": "me",
                            "label": "bug",
                            "filter": "open",
                            "query": "p0",
                            "title": "ignored - over the cap",
                        },
                    },
                }
            ],
            "review_configs": [],
        },
    )

    payload = payloads[0]
    assert len(payload["params"]) == 6
    labels = [row["label"] for row in payload["params"]]
    # Allow-list iteration is in key-tuple order; ``label`` is last so
    # it falls past the 6-row cap and is dropped.
    assert "Label" not in labels


def test_native_mcp_interrupt_payloads_arguments_missing_keeps_empty_params() -> None:
    payloads = StreamOrchestrator.native_tool_approval_payloads(
        interrupt_id="interrupt_empty",
        interrupt_value={
            "action_requests": [
                {
                    "name": "call_mcp_tool",
                    "args": {
                        "server_name": "linear",
                        "tool_name": "list_issues",
                        "arguments": {"body": "ignored — not on allow-list"},
                    },
                }
            ],
            "review_configs": [],
        },
    )

    payload = payloads[0]
    assert payload["params"] == []
    assert payload["category"] == "read"
    assert payload["reason_code"] == "read_only_first_use"


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


def test_ask_a_question_tool_calls_are_marked_internal_visibility() -> None:
    """The ask_a_question approval surface is owned by the native interrupt
    projector, so the chunked tool_call_started/result events for the same call
    must be marked internal — otherwise the UI renders a duplicate
    'ask_a_question running' tile next to the actual question card."""

    started = StreamMessageProcessor.tool_call_payload(
        {
            "name": "ask_a_question",
            "id": "call_aq_1",
            "args": {
                "question": "Petrol or Diesel?",
                "options": ["Petrol", "Diesel"],
            },
        }
    )
    result = StreamMessageProcessor.tool_result_payload(
        {
            "type": "tool",
            "name": "ask_a_question",
            "tool_call_id": "call_aq_1",
            "content": '{"ok": true, "decision": "approved", "answer": "Petrol"}',
        }
    )

    assert started["visibility"] == "internal"
    assert result["visibility"] == "internal"


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


async def test_tool_result_carries_duration_ms_back_to_started_event() -> None:
    """`tool_result` and `tool_call_completed` payloads should carry the wall-clock
    elapsed time since `tool_call_started`. Lets perf consumers compute
    "stream time excluding tool windows" without joining started/result events."""

    producer = RecordingEventProducer()
    orchestrator = StreamOrchestrator(event_producer=producer)  # type: ignore[arg-type]
    namespace = StreamNamespace.from_value(())
    run = TestFixtures.run_record()

    await orchestrator.message_processor.append_tool_call_chunk_event(
        run=run,
        namespace=namespace,
        tool_call={
            "name": "web_search",
            "id": "call_w1",
            "index": 0,
            "args": {"query": "langchain version"},
        },
        metadata={},
        parent_task_id=None,
    )
    await orchestrator.message_processor.process(
        run=run,
        namespace=namespace,
        message={
            "type": "tool",
            "name": "web_search",
            "tool_call_id": "call_w1",
            "content": "1.3.0",
        },
        delta=None,
    )

    started = next(
        e
        for e in producer.events
        if e["event_type"] is RuntimeApiEventType.TOOL_CALL_STARTED
    )
    result = next(
        e for e in producer.events if e["event_type"] is RuntimeApiEventType.TOOL_RESULT
    )
    completed = next(
        e
        for e in producer.events
        if e["event_type"] is RuntimeApiEventType.TOOL_CALL_COMPLETED
    )
    assert "duration_ms" not in started["payload"]
    assert isinstance(result["payload"]["duration_ms"], int)
    assert result["payload"]["duration_ms"] >= 0
    assert completed["payload"]["duration_ms"] == result["payload"]["duration_ms"]


async def test_subagent_completed_carries_duration_ms_back_to_started_event() -> None:
    """`subagent_completed` payload should carry elapsed time since
    `subagent_started` so the UI / perf tooling can show "subagent X gated the
    run" without recomputing from event timestamps."""

    producer = RecordingEventProducer()
    update_processor = StreamUpdateProcessor(event_producer=producer)  # type: ignore[arg-type]
    run = TestFixtures.run_record()

    await update_processor.append_task_lifecycle_event(
        run=run,
        event_type=RuntimeApiEventType.SUBAGENT_STARTED,
        payload={
            "task_id": "call_research",
            "subagent_name": "general-purpose",
            "status": "queued",
        },
        metadata={},
    )
    await update_processor.append_task_lifecycle_event(
        run=run,
        event_type=RuntimeApiEventType.SUBAGENT_COMPLETED,
        payload={
            "task_id": "call_research",
            "subagent_name": "general-purpose",
            "status": "completed",
            "summary": "Research complete.",
        },
        metadata={},
    )

    started = next(
        e
        for e in producer.events
        if e["event_type"] is RuntimeApiEventType.SUBAGENT_STARTED
    )
    completed = next(
        e
        for e in producer.events
        if e["event_type"] is RuntimeApiEventType.SUBAGENT_COMPLETED
    )
    assert "duration_ms" not in started["payload"]
    assert isinstance(completed["payload"]["duration_ms"], int)
    assert completed["payload"]["duration_ms"] >= 0


async def test_tool_message_with_error_status_emits_failed_tool_result() -> None:
    """LangGraph's tool executor sets ToolMessage.status='error' when a tool
    raises. The runtime stream parser must surface that as `tool_result.status='failed'`
    (and propagate the failure into the follow-up `tool_call_completed` event)
    rather than silently defaulting to 'completed'."""

    producer = RecordingEventProducer()
    orchestrator = StreamOrchestrator(event_producer=producer)  # type: ignore[arg-type]
    namespace = StreamNamespace.from_value(())
    run = TestFixtures.run_record()

    await orchestrator.message_processor.append_tool_call_chunk_event(
        run=run,
        namespace=namespace,
        tool_call={
            "name": "web_search",
            "id": "call_err",
            "index": 0,
            "args": {"query": "broken tool"},
        },
        metadata={},
        parent_task_id=None,
    )
    await orchestrator.message_processor.process(
        run=run,
        namespace=namespace,
        message={
            "type": "tool",
            "name": "web_search",
            "tool_call_id": "call_err",
            "content": "ConnectionError: connection refused",
            "status": "error",
        },
        delta=None,
    )

    result = next(
        e for e in producer.events if e["event_type"] is RuntimeApiEventType.TOOL_RESULT
    )
    completed = next(
        e
        for e in producer.events
        if e["event_type"] is RuntimeApiEventType.TOOL_CALL_COMPLETED
    )
    assert result["payload"]["status"] == "failed"
    assert completed["payload"]["status"] == "failed"


async def test_tool_message_with_success_status_emits_completed_tool_result() -> None:
    """LangChain ToolMessage.status='success' maps to our 'completed' status."""

    producer = RecordingEventProducer()
    orchestrator = StreamOrchestrator(event_producer=producer)  # type: ignore[arg-type]
    namespace = StreamNamespace.from_value(())
    run = TestFixtures.run_record()

    await orchestrator.message_processor.append_tool_call_chunk_event(
        run=run,
        namespace=namespace,
        tool_call={
            "name": "web_search",
            "id": "call_ok",
            "index": 0,
            "args": {"query": "ok"},
        },
        metadata={},
        parent_task_id=None,
    )
    await orchestrator.message_processor.process(
        run=run,
        namespace=namespace,
        message={
            "type": "tool",
            "name": "web_search",
            "tool_call_id": "call_ok",
            "content": "results...",
            "status": "success",
        },
        delta=None,
    )

    result = next(
        e for e in producer.events if e["event_type"] is RuntimeApiEventType.TOOL_RESULT
    )
    assert result["payload"]["status"] == "completed"


async def test_tool_call_started_records_ledger_entry() -> None:
    """The ledger must observe every tool_call_started event so the handler
    can reconcile in-flight calls if the run hits a terminal failure path."""

    producer = RecordingEventProducer()
    orchestrator = StreamOrchestrator(event_producer=producer)  # type: ignore[arg-type]
    namespace = StreamNamespace.from_value(())
    run = TestFixtures.run_record()

    await orchestrator.message_processor.append_tool_call_chunk_event(
        run=run,
        namespace=namespace,
        tool_call={
            "name": "web_search",
            "id": "call_x",
            "index": 0,
            "args": {"query": "hello"},
        },
        metadata={},
        parent_task_id=None,
    )
    ledger = orchestrator.message_processor.ledger_for_run(run.run_id)
    unsettled = ledger.unsettled()
    assert len(unsettled) == 1
    assert unsettled[0].call_id == "call_x"
    assert unsettled[0].tool_name == "web_search"


async def test_tool_result_marks_ledger_entry_observed_settled() -> None:
    """Natural settlement (LangGraph emits a tool_result) must clear the
    in-flight entry so the handler doesn't synthesize a duplicate
    terminal event during reconciliation."""

    producer = RecordingEventProducer()
    orchestrator = StreamOrchestrator(event_producer=producer)  # type: ignore[arg-type]
    namespace = StreamNamespace.from_value(())
    run = TestFixtures.run_record()

    await orchestrator.message_processor.append_tool_call_chunk_event(
        run=run,
        namespace=namespace,
        tool_call={
            "name": "web_search",
            "id": "call_settled",
            "index": 0,
            "args": {"query": "x"},
        },
        metadata={},
        parent_task_id=None,
    )
    await orchestrator.message_processor.process(
        run=run,
        namespace=namespace,
        message={
            "type": "tool",
            "name": "web_search",
            "tool_call_id": "call_settled",
            "content": "results",
        },
        delta=None,
    )
    ledger = orchestrator.message_processor.ledger_for_run(run.run_id)
    assert ledger.unsettled() == []


def test_discard_ledger_frees_per_run_state() -> None:
    """The handler calls discard_ledger after RUN_COMPLETED / RUN_FAILED so
    we don't leak per-run state on a long-running worker."""

    producer = RecordingEventProducer()
    orchestrator = StreamOrchestrator(event_producer=producer)  # type: ignore[arg-type]

    ledger = orchestrator.message_processor.ledger_for_run("run_to_discard")
    ledger.started("call_a", tool_name="web_search")
    assert ledger.has_entries()
    orchestrator.message_processor.discard_ledger("run_to_discard")
    # A fresh ledger_for_run after discard returns an empty new instance.
    fresh = orchestrator.message_processor.ledger_for_run("run_to_discard")
    assert fresh is not ledger
    assert fresh.has_entries() is False


async def test_parallel_dispatch_wraps_subagents_in_a_fleet() -> None:
    """When the supervisor dispatches >1 task tool call in a single update
    tick, the processor emits SUBAGENT_FLEET_STARTED first, stamps
    `parent_fleet_id` on each child SUBAGENT_STARTED, and emits
    SUBAGENT_FLEET_FINISHED only once the last child completes."""

    producer = RecordingEventProducer()
    update_processor = StreamUpdateProcessor(event_producer=producer)  # type: ignore[arg-type]
    run = TestFixtures.run_record()
    namespace = StreamNamespace.from_value(())

    parallel_dispatch = {
        "model_request": {
            "messages": [
                {
                    "tool_calls": [
                        {
                            "name": "task",
                            "id": "task_alpha",
                            "args": {
                                "subagent_type": "doc_reader",
                                "description": "Read launch brief.",
                            },
                        },
                        {
                            "name": "task",
                            "id": "task_beta",
                            "args": {
                                "subagent_type": "press_scout",
                                "description": "Scan press coverage.",
                            },
                        },
                    ]
                }
            ]
        }
    }
    await update_processor.append_subagent_lifecycle_events(
        run=run,
        namespace=namespace,
        data=parallel_dispatch,
        metadata={},
    )

    fleet_started = [
        event
        for event in producer.events
        if event["event_type"] is RuntimeApiEventType.SUBAGENT_FLEET_STARTED
    ]
    assert len(fleet_started) == 1
    fleet_payload = fleet_started[0]["payload"]
    assert tuple(fleet_payload["agent_ids"]) == ("doc_reader", "press_scout")
    fleet_id = fleet_payload["fleet_id"]
    assert isinstance(fleet_id, str) and fleet_id

    started_payloads = [
        event["payload"]
        for event in producer.events
        if event["event_type"] is RuntimeApiEventType.SUBAGENT_STARTED
    ]
    assert len(started_payloads) == 2
    assert {payload["parent_fleet_id"] for payload in started_payloads} == {fleet_id}

    # First child completes — fleet stays open, no FINISHED yet.
    completion_alpha = {
        "tools": {
            "messages": [
                {
                    "type": "tool",
                    "name": "task",
                    "tool_call_id": "task_alpha",
                    "content": "Brief read.",
                }
            ]
        }
    }
    await update_processor.append_subagent_lifecycle_events(
        run=run,
        namespace=namespace,
        data=completion_alpha,
        metadata={},
    )
    assert not [
        event
        for event in producer.events
        if event["event_type"] is RuntimeApiEventType.SUBAGENT_FLEET_FINISHED
    ]
    completed_alpha = next(
        event["payload"]
        for event in producer.events
        if event["event_type"] is RuntimeApiEventType.SUBAGENT_COMPLETED
        and event["payload"]["task_id"] == "task_alpha"
    )
    assert completed_alpha["parent_fleet_id"] == fleet_id

    # Second child completes — fleet closes.
    completion_beta = {
        "tools": {
            "messages": [
                {
                    "type": "tool",
                    "name": "task",
                    "tool_call_id": "task_beta",
                    "content": "Press coverage scanned.",
                }
            ]
        }
    }
    await update_processor.append_subagent_lifecycle_events(
        run=run,
        namespace=namespace,
        data=completion_beta,
        metadata={},
    )
    fleet_finished = [
        event
        for event in producer.events
        if event["event_type"] is RuntimeApiEventType.SUBAGENT_FLEET_FINISHED
    ]
    assert len(fleet_finished) == 1
    assert fleet_finished[0]["payload"]["fleet_id"] == fleet_id


async def test_single_dispatch_does_not_wrap_in_a_fleet() -> None:
    """A single subagent dispatch keeps the singleton path with no fleet
    bookend events — fleet wrapping is only for parallel batches."""

    producer = RecordingEventProducer()
    update_processor = StreamUpdateProcessor(event_producer=producer)  # type: ignore[arg-type]
    run = TestFixtures.run_record()
    namespace = StreamNamespace.from_value(())

    single_dispatch = {
        "model_request": {
            "messages": [
                {
                    "tool_calls": [
                        {
                            "name": "task",
                            "id": "task_solo",
                            "args": {
                                "subagent_type": "researcher",
                                "description": "Investigate the launch.",
                            },
                        }
                    ]
                }
            ]
        }
    }
    await update_processor.append_subagent_lifecycle_events(
        run=run,
        namespace=namespace,
        data=single_dispatch,
        metadata={},
    )

    event_types = [event["event_type"] for event in producer.events]
    assert RuntimeApiEventType.SUBAGENT_FLEET_STARTED not in event_types
    started_payloads = [
        event["payload"]
        for event in producer.events
        if event["event_type"] is RuntimeApiEventType.SUBAGENT_STARTED
    ]
    assert len(started_payloads) == 1
    assert "parent_fleet_id" not in started_payloads[0]


async def test_subagent_completed_without_started_omits_duration_ms() -> None:
    """If a SUBAGENT_COMPLETED arrives without a matching SUBAGENT_STARTED on
    this processor (e.g. event-store replay scenarios), we omit the field
    rather than fabricate a misleading zero."""

    producer = RecordingEventProducer()
    update_processor = StreamUpdateProcessor(event_producer=producer)  # type: ignore[arg-type]
    run = TestFixtures.run_record()

    await update_processor.append_task_lifecycle_event(
        run=run,
        event_type=RuntimeApiEventType.SUBAGENT_COMPLETED,
        payload={
            "task_id": "call_orphan",
            "subagent_name": "general-purpose",
            "status": "completed",
            "summary": "Orphan completion.",
        },
        metadata={},
    )

    completed = producer.events[0]
    assert "duration_ms" not in completed["payload"]


async def test_chunk_metadata_links_parallel_subagents_to_supervisor_call_ids() -> None:
    """Parallel subagents: chunk metadata pins (subgraph_uuid → supervisor call_id).

    Regression for the FIFO-pop race that returned None when ≥2 subagents were
    unlinked concurrently. The fix ships our `atlas_task_tool` which writes
    `supervisor_task_call_id` into each subagent's RunnableConfig metadata,
    then `StreamPartParser.supervisor_task_call_id_for(part)` reads it on the
    first chunk from a subgraph and pins the link via
    `register_supervisor_call_id_for_subgraph`. From that point onward, every
    event from that subgraph resolves deterministically — no ambiguity even
    when multiple subagents are mid-flight.
    """

    producer = RecordingEventProducer()
    orchestrator = StreamOrchestrator(event_producer=producer)  # type: ignore[arg-type]
    run = TestFixtures.run_record()

    # Supervisor dispatches two subagents in the same tick.
    await orchestrator.update_processor.append_task_lifecycle_event(
        run=run,
        event_type=RuntimeApiEventType.SUBAGENT_STARTED,
        payload={
            "task_id": "call_supervisor_A",
            "subagent_name": "general-purpose",
            "status": "queued",
        },
        metadata={},
    )
    await orchestrator.update_processor.append_task_lifecycle_event(
        run=run,
        event_type=RuntimeApiEventType.SUBAGENT_STARTED,
        payload={
            "task_id": "call_supervisor_B",
            "subagent_name": "general-purpose",
            "status": "queued",
        },
        metadata={},
    )

    # Sub A's first chunk arrives. Carries `supervisor_task_call_id` in
    # the messages-mode metadata tuple position 1. Pins the link.
    await orchestrator.append_activity_events(
        run=run,
        chunk={
            "type": "messages",
            "ns": ("tools:subgraph_A_uuid",),
            "data": (
                {
                    "tool_call_chunks": (
                        {
                            "name": "web_search",
                            "id": "call_search_a",
                            "args": {"query": "topic A"},
                        },
                    ),
                },
                {"supervisor_task_call_id": "call_supervisor_A"},
            ),
        },
        delta=None,
    )
    # Sub B's first chunk — different subgraph UUID, different supervisor
    # call_id. Even with sub A still un-completed (so the FIFO would have
    # been racy), this resolves correctly via metadata.
    await orchestrator.append_activity_events(
        run=run,
        chunk={
            "type": "messages",
            "ns": ("tools:subgraph_B_uuid",),
            "data": (
                {
                    "tool_call_chunks": (
                        {
                            "name": "web_search",
                            "id": "call_search_b",
                            "args": {"query": "topic B"},
                        },
                    ),
                },
                {"supervisor_task_call_id": "call_supervisor_B"},
            ),
        },
        delta=None,
    )

    tool_starts = [
        e
        for e in producer.events
        if e["event_type"] is RuntimeApiEventType.TOOL_CALL_STARTED
    ]
    assert len(tool_starts) == 2, [
        (e["payload"].get("tool_name"), e.get("parent_task_id")) for e in tool_starts
    ]
    by_call = {e["payload"]["call_id"]: e for e in tool_starts}
    # Sub A's tool stays attributed to A; sub B's to B. No mis-attribution.
    assert by_call["call_search_a"]["parent_task_id"] == "call_supervisor_A"
    assert by_call["call_search_b"]["parent_task_id"] == "call_supervisor_B"


async def test_chunk_without_supervisor_metadata_falls_back_to_raw_subgraph_id() -> (
    None
):
    """Legacy / synthetic chunks without our injected metadata still
    resolve via the raw subgraph UUID for the chunk-level emit path
    (custom + explicit api_event payloads). Preserves the historical
    contract for replay flows and synthetic test fixtures that bypass
    `atlas_task_tool`. The FIFO-pop fallback for messages-mode chunks
    stays inside `stream_tools.StreamMessageProcessor.process` — see
    `test_tool_event_inside_subagent_carries_subagent_id`."""

    producer = RecordingEventProducer()
    orchestrator = StreamOrchestrator(event_producer=producer)  # type: ignore[arg-type]
    run = TestFixtures.run_record()

    await orchestrator.append_activity_events(
        run=run,
        chunk={
            "type": "custom",
            "ns": ("tools:legacy_subgraph_uuid",),
            "data": {
                "api_event_type": "reasoning_summary_delta",
                "summary": "Reasoning inside the legacy subgraph.",
                "delta": "Reasoning inside",
            },
        },
        delta=None,
    )

    reasoning = [
        e
        for e in producer.events
        if e["event_type"] is RuntimeApiEventType.REASONING_SUMMARY_DELTA
    ]
    assert len(reasoning) == 1
    assert reasoning[0]["parent_task_id"] == "legacy_subgraph_uuid"


# --- v1 surface lift RETIRED (PRD-E3 D4) -------------------------------------
#
# PRD-02 used to attach ``surface_uri`` + ``surface`` to the MCP tool return dict,
# and ``_lift_surface_fields`` hoisted them onto the event-payload top level. E3
# retired both: the v1 appendage no longer exists, so ``tool_result_payload``
# performs NO surface lift — any ``surface`` / ``surface_uri`` key is treated as
# ordinary tool output (bucketed into ``output`` like every other non-reserved
# field), never special-cased onto the payload top level. Surface data now reaches
# clients via the Work Ledger (``surface.created`` / ``view.derived``) instead.

_SURFACE_ENVELOPE: dict[str, object] = {
    "surface_uri": "record://linear/get_issue/ENG-1421",
    "archetype": "record",
    "state": {"data": {"issue": {"identifier": "ENG-1421"}}},
}


def test_tool_result_does_not_lift_top_level_surface() -> None:
    """A dict-shaped tool message carrying ``surface`` / ``surface_uri`` keys must
    NOT hoist them to the payload top level (v1 lift retired) — they stay bucketed
    into ``output`` as ordinary fields."""

    payload = StreamMessageProcessor.tool_result_payload(
        {
            "type": "tool",
            "name": "call_mcp_tool",
            "tool_call_id": "call_surface_1",
            "status": "success",
            "output": {"issue": {"identifier": "ENG-1421"}},
            "surface_uri": "record://linear/get_issue/ENG-1421",
            "surface": _SURFACE_ENVELOPE,
        }
    )

    # No top-level lift.
    assert "surface_uri" not in payload
    assert "surface" not in payload
    # The keys are just ordinary output fields now (not popped/special-cased).
    assert payload["output"]["surface_uri"] == "record://linear/get_issue/ENG-1421"
    assert payload["output"]["surface"] == _SURFACE_ENVELOPE
    assert payload["output"]["output"] == {"issue": {"identifier": "ENG-1421"}}


def test_tool_result_does_not_lift_surface_from_json_string_content() -> None:
    """Production path: the MCP return dict is JSON-serialised into
    ``ToolMessage.content``. E3 retired the lift, so no surface is extracted from
    the content string — ``content`` is left intact and no top-level surface
    appears."""

    from langchain_core.messages import ToolMessage

    return_dict = {
        "server_name": "linear",
        "tool_name": "get_issue",
        "output": {"issue": {"identifier": "ENG-1421"}},
        "surface_uri": "record://linear/get_issue/ENG-1421",
        "surface": _SURFACE_ENVELOPE,
    }
    message = ToolMessage(
        content=json.dumps(return_dict),
        name="call_mcp_tool",
        tool_call_id="call_surface_2",
        status="success",
    )

    payload = StreamMessageProcessor.tool_result_payload(message)

    assert "surface_uri" not in payload
    assert "surface" not in payload
    # ``content`` is left untouched (no parse-and-lift of the embedded surface).
    assert json.loads(payload["output"]["content"]) == return_dict


def test_tool_result_without_surface_is_unchanged() -> None:
    """A structured tool result with no surface keeps ``output`` untouched and
    gains no surface fields."""

    payload = StreamMessageProcessor.tool_result_payload(
        {
            "type": "tool",
            "name": "web_search",
            "tool_call_id": "call_plain",
            "status": "success",
            "output": {"results": ["a", "b"]},
        }
    )

    assert "surface_uri" not in payload
    assert "surface" not in payload
    assert payload["output"] == {"output": {"results": ["a", "b"]}}


def test_tool_result_plain_string_content_is_unaffected() -> None:
    """A plain-text (non-JSON) tool result never triggers a surface lift."""

    payload = StreamMessageProcessor.tool_result_payload(
        {
            "type": "tool",
            "name": "web_search",
            "tool_call_id": "call_text",
            "content": "connection refused",
        }
    )

    assert "surface_uri" not in payload
    assert "surface" not in payload
    assert payload["output"] == {"content": "connection refused"}


def test_tool_result_with_state_never_lifts_surface() -> None:
    """``tool_result_payload_with_state`` re-wraps the payload; with the v1 lift
    retired, no top-level surface is ever produced (the surface keys stay inside
    ``output``)."""

    producer = object()
    update_processor = StreamUpdateProcessor(event_producer=producer)  # type: ignore[arg-type]
    processor = StreamMessageProcessor(
        event_producer=producer, update_processor=update_processor
    )  # type: ignore[arg-type]

    base = StreamMessageProcessor.tool_result_payload(
        {
            "type": "tool",
            "name": "call_mcp_tool",
            "tool_call_id": "call_state_surface",
            "status": "success",
            "output": {"issue": {"identifier": "ENG-1421"}},
            "surface_uri": "record://linear/get_issue/ENG-1421",
            "surface": _SURFACE_ENVELOPE,
        }
    )
    enriched = processor.tool_result_payload_with_state("run_123", base)

    assert "surface_uri" not in enriched
    assert "surface" not in enriched
