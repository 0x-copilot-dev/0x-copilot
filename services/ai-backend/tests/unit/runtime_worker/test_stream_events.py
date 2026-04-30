from __future__ import annotations

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
