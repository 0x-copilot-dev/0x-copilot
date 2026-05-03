"""Durable runtime command schemas produced by the API."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import Field

from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeContract
from runtime_api.schemas.common import ApprovalDecision


class RuntimeRunCommand(RuntimeContract):
    """Durable command enqueued after run creation."""

    command_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    conversation_id: str
    org_id: str
    user_id: str
    trace_id: str
    runtime_context: AgentRuntimeContext
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RuntimeCancelCommand(RuntimeContract):
    """Durable command requesting best-effort run cancellation."""

    command_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    org_id: str
    requested_by_user_id: str
    reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RuntimeApprovalResolvedCommand(RuntimeContract):
    """Durable command notifying workers that an approval was resolved."""

    command_id: str = Field(default_factory=lambda: uuid4().hex)
    approval_id: str
    run_id: str
    org_id: str
    decision: ApprovalDecision
    answer: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
