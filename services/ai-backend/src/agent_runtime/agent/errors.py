"""Typed runtime exceptions with safe serialization."""

from __future__ import annotations

from uuid import uuid4

from agent_runtime.agent.contracts import RuntimeErrorCode, RuntimeErrorEnvelope


class AgentRuntimeError(Exception):
    """Runtime exception that carries a safe public message."""

    def __init__(
        self,
        code: RuntimeErrorCode,
        safe_message: str,
        *,
        retryable: bool = False,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable
        self.correlation_id = correlation_id or uuid4().hex

    def to_envelope(self, *, correlation_id: str | None = None) -> RuntimeErrorEnvelope:
        """Serialize the exception without exposing raw exception details."""

        return RuntimeErrorEnvelope(
            code=self.code,
            safe_message=self.safe_message,
            retryable=self.retryable,
            correlation_id=correlation_id or self.correlation_id,
        )
