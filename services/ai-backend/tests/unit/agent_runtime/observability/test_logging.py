from __future__ import annotations

from agent_runtime.observability.logging import RuntimeLogEvent, RuntimeLogLevel


def test_runtime_log_event_blocks_sensitive_and_complex_metadata() -> None:
    event = RuntimeLogEvent(
        event="runtime.invoke.failed",
        level=RuntimeLogLevel.ERROR,
        request_id="request_123",
        run_id="run_123",
        trace_id="trace_123",
        parent_trace_id="trace_parent",
        subsystem="runtime",
        operation="runtime.invoke",
        status="failed",
        error_code="external_service_error",
        retryable=True,
        safe_message="Runtime invocation failed safely.",
        metadata={
            "llm_response": "The user should follow up.",
            "output": "Raw model output.",
            "query": "What did someone promise?",
            "authorization": "bearer super-secret",
            "nested": {
                "response": "Nested LLM response text.",
                "content": "raw Slack message",
                "count": 2,
            },
            "safe_count": 3,
            "duration_ms": 150,
            "api_key": "sk-secret-value",
        },
    )

    payload = event.to_log_dict()

    assert payload["metadata"]["safe_count"] == 3  # type: ignore[index]
    assert payload["metadata"]["duration_ms"] == 150  # type: ignore[index]
    assert payload["metadata"]["llm_response"] == "The user should follow up."  # type: ignore[index]
    assert payload["metadata"]["query"] == "What did someone promise?"  # type: ignore[index]

    assert "authorization" not in payload["metadata"]  # type: ignore[operator]
    assert "api_key" not in payload["metadata"]  # type: ignore[operator]
    assert "nested" not in payload["metadata"]  # type: ignore[operator]
    assert "super-secret" not in str(payload)
    assert "sk-secret-value" not in str(payload)


def test_runtime_log_event_accepts_empty_metadata() -> None:
    event = RuntimeLogEvent(
        event="runtime.health",
        request_id="req_1",
        run_id="run_1",
        trace_id="trace_1",
        subsystem="runtime",
        operation="health",
        status="ok",
    )
    payload = event.to_log_dict()
    assert payload["metadata"] == {}


def test_runtime_log_event_strips_whitespace_from_labels() -> None:
    event = RuntimeLogEvent(
        event="  runtime.invoke  ",
        request_id="req_1",
        run_id="run_1",
        trace_id="trace_1",
        subsystem="  runtime  ",
        operation="  invoke  ",
        status="  ok  ",
    )
    assert event.event == "runtime.invoke"
    assert event.subsystem == "runtime"
    assert event.operation == "invoke"
    assert event.status == "ok"
