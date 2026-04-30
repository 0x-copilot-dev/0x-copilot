"""Persisted async subagent task and result records."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import Field, PositiveInt

from agent_runtime.agent.contracts import JsonObject, RuntimeContract
from agent_runtime.persistence.records.common import AsyncTaskStatus


class AsyncTaskRecord(RuntimeContract):
    """Persisted async subagent task metadata."""

    task_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    conversation_id: str
    org_id: str
    parent_task_id: str | None = None
    subagent_name: str
    thread_id: str | None = None
    langgraph_run_id: str | None = None
    status: AsyncTaskStatus = AsyncTaskStatus.QUEUED
    objective_summary: str
    constraints: JsonObject = Field(default_factory=dict)
    output_contract: JsonObject = Field(default_factory=dict)
    timeout_seconds: PositiveInt | None = None
    started_at: datetime | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    safe_error_code: str | None = None
    safe_error_message: str | None = None



class SubagentResultRecord(RuntimeContract):
    """Persisted subagent result and compact summaries."""

    result_id: str = Field(default_factory=lambda: uuid4().hex)
    task_id: str
    run_id: str
    response_text: str | None = None
    execution_summary: str | None = None
    plan_summary: str | None = None
    artifacts: JsonObject = Field(default_factory=dict)
    recent_messages_ref: str | None = None
    error: JsonObject | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
