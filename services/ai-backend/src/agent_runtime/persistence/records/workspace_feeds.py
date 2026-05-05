"""Projection records for the Workspace-pane data feeds (PR 1.5).

Both records are *read-only projections*: they are computed at read time from
``runtime_events`` (subagents) and ``runtime_citations`` (sources). The
:class:`SubagentStorePort` and :class:`SourceStorePort` return tuples of these
records, which the
:class:`agent_runtime.api.workspace_feed_service.WorkspaceFeedService` then
shapes into the public HTTP DTOs.

Keeping the projection records separate from the wire DTOs lets adapters stay
ignorant of HTTP concerns and lets the service own truncation, redaction, and
field-level encryption decisions.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field, NonNegativeInt, PositiveInt

from agent_runtime.execution.contracts import RuntimeContract


class SubagentLifecycleStatus(str, Enum):
    """Coarse lifecycle status projected from SUBAGENT_* events.

    The values mirror :class:`agent_runtime.persistence.records.common.AsyncTaskStatus`
    so that a future writer can populate the dormant ``runtime_async_tasks``
    table without renaming anything.
    """

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class SubagentTokenUsage(RuntimeContract):
    """Per-subagent token rollup over ``runtime_model_call_usage`` (PR 1.5 AC-2).

    Computed by SUM-GROUP-BY on ``task_id``; absent when a subagent has not
    yet logged any model call (rare but possible for sub-second cancellations).
    """

    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0
    cached_input_tokens: NonNegativeInt = 0
    total_tokens: NonNegativeInt = 0


class SubagentSnapshot(RuntimeContract):
    """One subagent task projected from its SUBAGENT_* event timeline."""

    task_id: str = Field(min_length=1, max_length=128)
    parent_run_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=128)
    org_id: str = Field(min_length=1, max_length=128)
    subagent_name: str = Field(min_length=1, max_length=128)
    status: SubagentLifecycleStatus
    display_title: str | None = Field(default=None, max_length=240)
    objective_summary: str | None = Field(default=None, max_length=4096)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: NonNegativeInt | None = None
    result_summary: str | None = Field(default=None, max_length=2048)
    safe_error_code: str | None = Field(default=None, max_length=128)
    safe_error_message: str | None = Field(default=None, max_length=2048)
    token_usage: SubagentTokenUsage | None = None


class SourceAggregate(RuntimeContract):
    """One unique source aggregated across every citation in a conversation."""

    citation_id: str = Field(min_length=2, max_length=16)
    conversation_id: str = Field(min_length=1, max_length=128)
    org_id: str = Field(min_length=1, max_length=128)
    source_connector: str = Field(min_length=1, max_length=64)
    source_doc_id: str = Field(min_length=1, max_length=512)
    source_url: str | None = Field(default=None, max_length=2048)
    title: str | None = Field(default=None, max_length=512)
    snippet: str | None = Field(default=None, max_length=1024)
    freshness_at: datetime | None = None
    citation_count: PositiveInt
    last_cited_at: datetime
