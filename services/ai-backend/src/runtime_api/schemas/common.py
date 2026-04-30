"""Shared runtime API schema enums and value normalization."""

from __future__ import annotations

from enum import StrEnum

from agent_runtime.agent.contracts import JsonObject, StreamEventType
from agent_runtime.api.constants import Messages, Patterns
from agent_runtime.observability.redaction import ObservabilityRedactor


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
    SUBAGENT_UPDATE = "subagent_update"
    SUBAGENT_STARTED = "subagent_started"
    SUBAGENT_PROGRESS = "subagent_progress"
    SUBAGENT_COMPLETED = "subagent_completed"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    OBSERVATION = "observation"
    ERROR = "error"
    FINAL_RESPONSE = "final_response"
    HEARTBEAT = "heartbeat"

    @classmethod
    def from_stream_event_type(cls, event_type: StreamEventType) -> "RuntimeApiEventType":
        """Map normalized runtime stream events into API transport events."""

        return {
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
        }[event_type]



class ApprovalDecision(StrEnum):
    """Allowed user decisions for side-effecting approval requests."""

    APPROVED = "approved"
    REJECTED = "rejected"



class ApprovalStatus(StrEnum):
    """Approval request state after a decision is accepted."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"



class RuntimeApiValueNormalizer:
    """Normalize and redact values entering API/domain contracts."""

    @classmethod
    def normalize_id(cls, value: object, field_name: str) -> str:
        normalized = cls.normalize_nonempty_string(value, field_name)
        if not Patterns.ID.fullmatch(normalized):
            msg = Messages.Validation.id_contains_unsupported_characters(field_name)
            raise ValueError(msg)
        return normalized

    @classmethod
    def normalize_optional_id(cls, value: object, field_name: str) -> str | None:
        if value is None:
            return None
        return cls.normalize_id(value, field_name)

    @classmethod
    def normalize_slug(cls, value: object, field_name: str) -> str:
        normalized = cls.normalize_nonempty_string(value, field_name).lower()
        if not Patterns.SLUG.fullmatch(normalized):
            msg = Messages.Validation.stable_slug(field_name)
            raise ValueError(msg)
        return normalized

    @classmethod
    def normalize_nonempty_string(cls, value: object, field_name: str) -> str:
        if not isinstance(value, str):
            msg = Messages.Validation.string_required(field_name)
            raise ValueError(msg)
        normalized = value.strip()
        if not normalized:
            msg = Messages.Validation.nonempty_string(field_name)
            raise ValueError(msg)
        return normalized

    @classmethod
    def normalize_optional_text(cls, value: object, field_name: str) -> str | None:
        if value is None:
            return None
        return cls.normalize_nonempty_string(value, field_name)

    @classmethod
    def redact_json_object(cls, value: object) -> JsonObject:
        return ObservabilityRedactor.redact_json_object(value)  # type: ignore[return-value]
