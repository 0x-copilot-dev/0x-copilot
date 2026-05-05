"""HTTP IO schemas for the Workspace pane data feeds (PR 1.5).

The wire surface is intentionally narrow: two GET responses + one query enum.
Both responses are conversation-scoped projections; live updates over SSE
reuse existing event types (``SUBAGENT_*`` and PR 1.1's ``source_ingested``).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, NonNegativeInt, PositiveInt

from agent_runtime.persistence.records import SubagentLifecycleStatus


class SubagentStatusFilter(str, Enum):
    """Coarse filter exposed to the FE on ``GET …/subagents``."""

    ALL = "all"
    RUNNING = "running"
    RECENT = "recent"


class SubagentEntry(BaseModel):
    """Single subagent card the Agents tab renders."""

    task_id: str = Field(min_length=1, max_length=128)
    parent_run_id: str = Field(min_length=1, max_length=128)
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


class SubagentListResponse(BaseModel):
    """Response envelope for ``GET /v1/agent/conversations/{cid}/subagents``."""

    conversation_id: str = Field(min_length=1, max_length=128)
    subagents: tuple[SubagentEntry, ...] = ()
    truncated: bool = False


class SourceEntry(BaseModel):
    """One unique source aggregated across every citation in the conversation."""

    citation_id: str = Field(min_length=2, max_length=16)
    source_connector: str = Field(min_length=1, max_length=64)
    source_doc_id: str = Field(min_length=1, max_length=512)
    source_url: str | None = Field(default=None, max_length=2048)
    title: str | None = Field(default=None, max_length=512)
    snippet: str | None = Field(default=None, max_length=1024)
    freshness_at: datetime | None = None
    citation_count: PositiveInt
    last_cited_at: datetime


class SourceListResponse(BaseModel):
    """Response envelope for ``GET /v1/agent/conversations/{cid}/sources``."""

    conversation_id: str = Field(min_length=1, max_length=128)
    run_id: str | None = Field(default=None, max_length=128)
    sources: tuple[SourceEntry, ...] = ()
    truncated: bool = False


__all__ = (
    "SourceEntry",
    "SourceListResponse",
    "SubagentEntry",
    "SubagentListResponse",
    "SubagentStatusFilter",
)
