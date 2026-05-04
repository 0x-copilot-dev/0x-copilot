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
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    OBSERVATION = "observation"
    ERROR = "error"
    MODEL_CALL_STARTED = "model_call_started"
    MODEL_CALL_COMPLETED = "model_call_completed"
    MODEL_DELTA = "model_delta"
    FINAL_RESPONSE = "final_response"
    HEARTBEAT = "heartbeat"
    PRESENTATION_UPDATED = "presentation_updated"

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
    """Allowed user decisions for side-effecting approval requests."""

    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalStatus(StrEnum):
    """Approval request state after a decision is accepted."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
