from __future__ import annotations

from agent_runtime.observability.logging import RuntimeLogEvent, RuntimeLogLevel


def test_runtime_log_event_only_keeps_allowlisted_metadata() -> None:
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
            "llm_response": "The user should follow up with alice@example.com.",
            "output": "Raw model output must never be logged.",
            "query": "What did alice@example.com promise?",
            "authorization": "bearer super-secret",
            "nested": {
                "response": "Nested LLM response text.",
                "content": "raw Slack message",
                "count": 2,
            },
            "safe_count": 3,
        },
    )

    payload = event.to_log_dict()

    assert "query" not in payload["metadata"]  # type: ignore[operator]
    assert "llm_response" not in payload["metadata"]  # type: ignore[operator]
    assert "output" not in payload["metadata"]  # type: ignore[operator]
    assert "authorization" not in payload["metadata"]  # type: ignore[operator]
    assert "nested" not in payload["metadata"]  # type: ignore[operator]
    assert payload["metadata"]["safe_count"] == 3  # type: ignore[index]
    assert "alice@example.com" not in str(payload)
    assert "super-secret" not in str(payload)
    assert "Raw model output" not in str(payload)
    assert "Nested LLM response" not in str(payload)
