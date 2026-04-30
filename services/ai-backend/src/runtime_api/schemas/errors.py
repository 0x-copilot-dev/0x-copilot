"""Safe runtime API error schemas."""

from __future__ import annotations

from pydantic import Field

from agent_runtime.execution.contracts import JsonObject, RuntimeContract, RuntimeErrorCode, RuntimeErrorEnvelope


class ApiErrorResponse(RuntimeContract):
    """Safe error body returned by HTTP exception handlers."""

    code: RuntimeErrorCode
    safe_message: str
    retryable: bool
    correlation_id: str
    details: JsonObject = Field(default_factory=dict)

    @classmethod
    def from_envelope(
        cls,
        envelope: RuntimeErrorEnvelope,
        *,
        details: JsonObject | None = None,
    ) -> "ApiErrorResponse":
        """Return an API error body from a runtime error envelope."""

        return cls(
            code=envelope.code,
            safe_message=envelope.safe_message,
            retryable=envelope.retryable,
            correlation_id=envelope.correlation_id,
            details=details or {},
        )
