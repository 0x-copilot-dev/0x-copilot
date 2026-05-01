from __future__ import annotations

import json
from types import SimpleNamespace

from runtime_api.schemas import RuntimeApiEventType
from runtime_worker.stream_events import RuntimeStreamPartAdapter
from runtime_worker.stream_parts import StreamNamespace, StreamPartParser


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
    payloads = RuntimeStreamPartAdapter.explicit_api_payloads(
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
    assert RuntimeStreamPartAdapter.api_event_type(payloads[0]) is (
        RuntimeApiEventType.REASONING_SUMMARY_DELTA
    )
    assert payloads[0]["summary"] == "Checking source coverage"


def test_explicit_api_payloads_are_collected_from_json_string_content() -> None:
    payloads = RuntimeStreamPartAdapter.explicit_api_payloads(
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
    assert RuntimeStreamPartAdapter.api_event_type(payloads[0]) is (
        RuntimeApiEventType.REASONING_SUMMARY_DELTA
    )
    assert payloads[0]["summary"] == "Checking source coverage"


def test_explicit_api_payloads_are_collected_from_tool_message_objects() -> None:
    payloads = RuntimeStreamPartAdapter.explicit_api_payloads(
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
    assert RuntimeStreamPartAdapter.api_event_type(payloads[0]) is (
        RuntimeApiEventType.PROGRESS
    )
    assert payloads[0]["message"] == "Still working."


def test_native_mcp_interrupt_payloads_project_to_approval() -> None:
    payloads = RuntimeStreamPartAdapter.native_tool_approval_payloads(
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
            "display_name": "mcp_clickup_com",
            "tool_name": "list_tasks",
            "arguments": {"assignee": "me"},
            "message": "Approve mcp_clickup_com to run list_tasks.",
            "status": "pending",
            "allowed_decisions": ["approve", "reject"],
            "grant_options": ["allow_once"],
        },
    )


def test_tool_call_state_merges_incremental_chunks_with_stable_identity() -> None:
    adapter = RuntimeStreamPartAdapter(event_producer=object())  # type: ignore[arg-type]
    namespace = StreamNamespace.from_value(())

    first = adapter.tool_call_state(
        "run_123",
        namespace,
        {
            "name": "write_todos",
            "id": "call_123",
            "index": 0,
            "args": {"delta": ""},
        },
    )
    second = adapter.tool_call_state(
        "run_123",
        namespace,
        {
            "index": 0,
            "args": {"delta": '{"todos":[{"content":"check prime"}]}'},
        },
    )

    assert second is first
    payload = adapter.tool_call_payload_from_state(second)
    assert payload["tool_name"] == "write_todos"
    assert payload["call_id"] == "call_123"
    assert payload["args"] == {"todos": "check prime"}


def test_large_result_file_tools_are_internal_only_for_virtual_paths() -> None:
    large_payload = RuntimeStreamPartAdapter.tool_call_payload(
        {
            "name": "read_file",
            "id": "call_large",
            "args": {"file_path": "/large_tool_results/call_123"},
        }
    )
    normal_payload = RuntimeStreamPartAdapter.tool_call_payload(
        {
            "name": "read_file",
            "id": "call_project",
            "args": {"file_path": "src/app.ts"},
        }
    )

    assert large_payload["visibility"] == "internal"
    assert "visibility" not in normal_payload


def test_large_result_file_tool_results_inherit_internal_visibility() -> None:
    adapter = RuntimeStreamPartAdapter(event_producer=object())  # type: ignore[arg-type]
    namespace = StreamNamespace.from_value(())

    adapter.tool_call_state(
        "run_123",
        namespace,
        {
            "name": "read_file",
            "id": "call_large",
            "args": {"file_path": "/large_tool_results/call_123"},
        },
    )
    payload = adapter.tool_result_payload_with_state(
        "run_123",
        {
            "tool_name": "unknown_tool",
            "call_id": "call_large",
            "output": {"content": "large payload"},
        },
    )

    assert payload["visibility"] == "internal"


def test_task_tool_updates_project_to_subagent_lifecycle_payloads() -> None:
    started = RuntimeStreamPartAdapter.task_tool_call_payloads(
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
    completed = RuntimeStreamPartAdapter.task_tool_result_payloads(
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


def test_task_tool_payload_includes_concise_user_facing_summary() -> None:
    payload = RuntimeStreamPartAdapter.task_tool_call_payload(
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
