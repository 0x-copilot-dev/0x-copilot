from __future__ import annotations

from agent_runtime.execution.contracts import RuntimeErrorCode, RuntimeErrorEnvelope
from agent_runtime.execution.errors import AgentRuntimeError


def test_runtime_error_envelope_uses_safe_agent_message() -> None:
    error = AgentRuntimeError(
        RuntimeErrorCode.PERMISSION_DENIED,
        "You do not have access to this capability.",
        retryable=False,
        correlation_id="trace_123",
    )

    envelope = RuntimeErrorEnvelope.from_exception(error)

    assert envelope.code == RuntimeErrorCode.PERMISSION_DENIED
    assert envelope.safe_message == "You do not have access to this capability."
    assert envelope.correlation_id == "trace_123"


def test_runtime_error_envelope_does_not_leak_raw_exception() -> None:
    envelope = RuntimeErrorEnvelope.from_exception(
        RuntimeError("api_key=super-secret"),
        correlation_id="trace_123",
    )

    assert envelope.code == RuntimeErrorCode.RUNTIME_FACTORY_ERROR
    assert envelope.safe_message == "The runtime could not complete the request safely."
    assert "super-secret" not in envelope.safe_message
    assert envelope.correlation_id == "trace_123"
