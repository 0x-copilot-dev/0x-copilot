"""Shared runtime API schema enums and value normalization."""

from __future__ import annotations

import logging
from enum import StrEnum

from agent_runtime.execution.contracts import StreamEventType


class ConversationStatus(StrEnum):
    """Conversation lifecycle states visible to API clients."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class MessageRole(StrEnum):
    """Conversation message roles persisted by the API producer."""

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    SYSTEM = "system"


class MessageStatus(StrEnum):
    """Message lifecycle states."""

    CREATED = "created"
    DELETED = "deleted"


class AgentRunStatus(StrEnum):
    """Runtime run states used by the producer/consumer event contract."""

    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class RuntimeEventVisibility(StrEnum):
    """Client visibility class for timeline and audit events."""

    USER = "user"
    INTERNAL = "internal"
    AUDIT = "audit"


class RuntimeEventRedactionState(StrEnum):
    """How event payload details were prepared before persistence."""

    REDACTED = "redacted"
    TRUNCATED = "truncated"
    OFFLOADED = "offloaded"


class RuntimeActivityKind(StrEnum):
    """Server-projected UI activity bucket for runtime events."""

    RUN = "run"
    MESSAGE = "message"
    TOOL = "tool"
    SUBAGENT = "subagent"
    REASONING = "reasoning"
    MCP_AUTH = "mcp_auth"
    APPROVAL = "approval"
    HEARTBEAT = "heartbeat"
    EVENT = "event"
    DRAFT = "draft"
    # PR A1 — context-compression note ("Atlas summarised N older
    # messages…"). Renders as a single dim line in-thread, not a card.
    NOTE = "note"


class RuntimeApiEventType(StrEnum):
    """Versioned event types emitted through the API transport envelope."""

    RUN_QUEUED = "run_queued"
    RUN_STARTED = "run_started"
    RUN_CANCELLING = "run_cancelling"
    RUN_CANCELLED = "run_cancelled"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    PROGRESS = "progress"
    REASONING_SUMMARY = "reasoning_summary"
    REASONING_SUMMARY_DELTA = "reasoning_summary_delta"
    TOOL_CALL = "tool_call"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_DELTA = "tool_call_delta"
    TOOL_RESULT = "tool_result"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    MCP_AUTH_REQUIRED = "mcp_auth_required"
    SUBAGENT_UPDATE = "subagent_update"
    SUBAGENT_STARTED = "subagent_started"
    SUBAGENT_PROGRESS = "subagent_progress"
    SUBAGENT_COMPLETED = "subagent_completed"
    # PR A2 — parallel-batch grouping. When the orchestrator dispatches
    # > 1 subagent in a single tick, it wraps them in a fleet so the FE
    # can render a single `<SubagentFleetCard>` instead of N siblings.
    # Each child subagent event carries `parent_fleet_id` in
    # `payload.parent_fleet_id` for binding.
    SUBAGENT_FLEET_STARTED = "subagent_fleet_started"
    SUBAGENT_FLEET_FINISHED = "subagent_fleet_finished"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    # PR 1.4 — two-stage approval forwarding. Emitted between
    # APPROVAL_RESOLVED (status=forwarded) on the parent and
    # APPROVAL_REQUESTED on the child so the FE can transform the original
    # in-thread card into a "Waiting on @marcus" pill in one reducer step.
    APPROVAL_FORWARDED = "approval_forwarded"
    OBSERVATION = "observation"
    ERROR = "error"
    MODEL_CALL_STARTED = "model_call_started"
    MODEL_CALL_COMPLETED = "model_call_completed"
    MODEL_DELTA = "model_delta"
    FINAL_RESPONSE = "final_response"
    HEARTBEAT = "heartbeat"
    PRESENTATION_UPDATED = "presentation_updated"
    # Budget enforcement (B7). RUN_REJECTED is distinct from RUN_FAILED so
    # the UI can show "budget exceeded" rather than a generic error.
    BUDGET_WARNING = "budget_warning"
    RUN_REJECTED = "run_rejected"
    # PR 1.3 — Workspace pane Draft tab. Emitted by DraftBackend on every
    # successful awrite/aedit and by RuntimeApiService on user PATCH /
    # POST send / POST discard.
    DRAFT_UPDATED = "draft_updated"
    # PR 1.1 — citations live registry. One event per (run, source) the
    # CitationLedger registers; payload carries `CitationSourceRef` under
    # `payload.citation` and projects to RuntimeActivityKind.TOOL.
    SOURCE_INGESTED = "source_ingested"
    # PR A1 — context-compression note. Emitted by the compression hook
    # when the context window manager redacts older messages to keep
    # the run efficient. Payload carries
    # ``CompressionNotePayload`` (before_tokens, after_tokens, strategy,
    # summary_text, payload_refs). Projects to
    # ``RuntimeActivityKind.NOTE``; the FE renders an inline
    # ``<NoteCard>`` ("Atlas summarised N older messages to keep this
    # conversation efficient.").
    COMPRESSION_NOTE = "compression_note"

    @classmethod
    def from_stream_event_type(
        cls, event_type: StreamEventType
    ) -> "RuntimeApiEventType":
        """Map normalized runtime stream events into API transport events."""

        mapping = {
            StreamEventType.PROGRESS: cls.PROGRESS,
            StreamEventType.TOOL_CALL: cls.TOOL_CALL,
            StreamEventType.TOOL_RESULT: cls.TOOL_RESULT,
            StreamEventType.CUSTOM: cls.PROGRESS,
            StreamEventType.LIFECYCLE: cls.SUBAGENT_UPDATE,
            StreamEventType.SUBAGENT_UPDATE: cls.SUBAGENT_UPDATE,
            StreamEventType.OBSERVATION: cls.OBSERVATION,
            StreamEventType.ERROR: cls.ERROR,
            StreamEventType.FINAL: cls.FINAL_RESPONSE,
            StreamEventType.FINAL_RESPONSE: cls.FINAL_RESPONSE,
        }
        result = mapping.get(event_type)
        if result is None:
            logging.getLogger(__name__).warning(
                "Unmapped stream event type: %s", event_type
            )
            return cls.PROGRESS
        return result


class ApprovalDecision(StrEnum):
    """Allowed user decisions for side-effecting approval requests.

    PR 1.4 — ``FORWARDED`` is an API-edge variant: it routes the pending
    approval to a second workspace user and never reaches the LangGraph
    harness. The worker discriminates on this enum and skips
    ``Command(resume=...)`` for the forwarded case.
    """

    APPROVED = "approved"
    REJECTED = "rejected"
    FORWARDED = "forwarded"


class ApprovalStatus(StrEnum):
    """Approval request state after a decision is accepted.

    PR 1.4 — ``FORWARDED`` is a terminal state for the parent row in a
    chain. Resume of the underlying run hangs off the child row's
    eventual ``APPROVED`` / ``REJECTED`` instead.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    FORWARDED = "forwarded"
