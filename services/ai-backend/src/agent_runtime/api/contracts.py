"""Pydantic contracts for the FastAPI runtime API."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import Field, NonNegativeInt, PositiveInt, ValidationInfo, field_validator

from agent_runtime.agent.contracts import (
    AgentRuntimeContext,
    JsonObject,
    RuntimeContract,
    RuntimeErrorCode,
    RuntimeErrorEnvelope,
    StreamEvent,
    StreamEventSource,
    StreamEventType,
)
from agent_runtime.api.constants import Keys, Messages, Patterns, Values
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
    """Runtime run states required by the producer/consumer PRD."""

    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class RuntimeApiEventType(StrEnum):
    """Versioned event types emitted through the API transport envelope."""

    RUN_QUEUED = "run_queued"
    RUN_STARTED = "run_started"
    RUN_CANCELLING = "run_cancelling"
    RUN_CANCELLED = "run_cancelled"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    PROGRESS = "progress"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    SUBAGENT_UPDATE = "subagent_update"
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


class CreateConversationRequest(RuntimeContract):
    """Request to create or idempotently resume a conversation shell."""

    org_id: str
    user_id: str
    assistant_id: str = Values.DEFAULT_ASSISTANT_ID
    title: str | None = None
    metadata: JsonObject = Field(default_factory=dict)
    idempotency_key: str | None = None

    @field_validator(Keys.Field.ORG_ID, Keys.Field.USER_ID, Keys.Field.ASSISTANT_ID)
    @classmethod
    def _normalize_ids(cls, value: object, info: ValidationInfo) -> str:
        return RuntimeApiValueNormalizer.normalize_id(value, info.field_name)

    @field_validator(Keys.Field.TITLE, mode="before")
    @classmethod
    def _normalize_title(cls, value: object) -> str | None:
        return RuntimeApiValueNormalizer.normalize_optional_text(value, Keys.Field.TITLE)

    @field_validator(Keys.Field.IDEMPOTENCY_KEY, mode="before")
    @classmethod
    def _normalize_idempotency_key(cls, value: object) -> str | None:
        return RuntimeApiValueNormalizer.normalize_optional_id(value, Keys.Field.IDEMPOTENCY_KEY)

    @field_validator(Keys.Field.METADATA, mode="before")
    @classmethod
    def _redact_metadata(cls, value: object) -> JsonObject:
        return RuntimeApiValueNormalizer.redact_json_object(value)


class ConversationRecord(RuntimeContract):
    """Persisted conversation metadata."""

    conversation_id: str = Field(default_factory=lambda: uuid4().hex)
    org_id: str
    user_id: str
    assistant_id: str
    title: str | None = None
    status: ConversationStatus = ConversationStatus.ACTIVE
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    archived_at: datetime | None = None
    metadata: JsonObject = Field(default_factory=dict)
    schema_version: PositiveInt = Values.SCHEMA_VERSION
    idempotency_key: str | None = None

    @field_validator(
        Keys.Field.CONVERSATION_ID,
        Keys.Field.ORG_ID,
        Keys.Field.USER_ID,
        Keys.Field.ASSISTANT_ID,
    )
    @classmethod
    def _normalize_ids(cls, value: object, info: ValidationInfo) -> str:
        return RuntimeApiValueNormalizer.normalize_id(value, info.field_name)

    @field_validator(Keys.Field.IDEMPOTENCY_KEY, mode="before")
    @classmethod
    def _normalize_idempotency_key(cls, value: object) -> str | None:
        return RuntimeApiValueNormalizer.normalize_optional_id(value, Keys.Field.IDEMPOTENCY_KEY)

    def to_response(self) -> "ConversationResponse":
        """Return the stable public conversation shape."""

        return ConversationResponse(
            conversation_id=self.conversation_id,
            org_id=self.org_id,
            user_id=self.user_id,
            assistant_id=self.assistant_id,
            title=self.title,
            status=self.status,
            created_at=self.created_at,
            updated_at=self.updated_at,
            archived_at=self.archived_at,
            metadata=self.metadata,
            schema_version=self.schema_version,
        )


class ConversationResponse(RuntimeContract):
    """Conversation metadata returned by the API."""

    conversation_id: str
    org_id: str
    user_id: str
    assistant_id: str
    title: str | None = None
    status: ConversationStatus
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None
    metadata: JsonObject = Field(default_factory=dict)
    schema_version: PositiveInt


class MessageRecord(RuntimeContract):
    """Persisted conversation message."""

    message_id: str = Field(default_factory=lambda: uuid4().hex)
    conversation_id: str
    org_id: str
    run_id: str | None = None
    role: MessageRole
    content_text: str
    content_format: str = Values.DEFAULT_CONTENT_FORMAT
    parent_message_id: str | None = None
    token_count: NonNegativeInt | None = None
    trace_id: str | None = None
    status: MessageStatus = MessageStatus.CREATED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    edited_at: datetime | None = None
    deleted_at: datetime | None = None

    @field_validator(
        Keys.Field.MESSAGE_ID,
        Keys.Field.CONVERSATION_ID,
        Keys.Field.ORG_ID,
        mode="before",
    )
    @classmethod
    def _normalize_ids(cls, value: object, info: ValidationInfo) -> str:
        return RuntimeApiValueNormalizer.normalize_id(value, info.field_name)

    @field_validator(Keys.Field.RUN_ID, "parent_message_id", Keys.Field.TRACE_ID, mode="before")
    @classmethod
    def _normalize_optional_ids(cls, value: object, info: ValidationInfo) -> str | None:
        return RuntimeApiValueNormalizer.normalize_optional_id(value, info.field_name)

    @field_validator("content_text", "content_format")
    @classmethod
    def _normalize_text(cls, value: object, info: ValidationInfo) -> str:
        return RuntimeApiValueNormalizer.normalize_nonempty_string(value, info.field_name)

    def to_response(self) -> "MessageResponse":
        """Return the stable public message shape."""

        return MessageResponse(
            message_id=self.message_id,
            conversation_id=self.conversation_id,
            org_id=self.org_id,
            run_id=self.run_id,
            role=self.role,
            content_text=self.content_text,
            content_format=self.content_format,
            parent_message_id=self.parent_message_id,
            token_count=self.token_count,
            trace_id=self.trace_id,
            status=self.status,
            created_at=self.created_at,
            edited_at=self.edited_at,
            deleted_at=self.deleted_at,
        )


class MessageResponse(RuntimeContract):
    """Conversation message returned to clients."""

    message_id: str
    conversation_id: str
    org_id: str
    run_id: str | None = None
    role: MessageRole
    content_text: str
    content_format: str
    parent_message_id: str | None = None
    token_count: NonNegativeInt | None = None
    trace_id: str | None = None
    status: MessageStatus
    created_at: datetime
    edited_at: datetime | None = None
    deleted_at: datetime | None = None


class MessageListResponse(RuntimeContract):
    """Paginated conversation messages."""

    conversation_id: str
    messages: tuple[MessageResponse, ...]
    next_cursor: str | None = None
    has_more: bool = False


class CreateRunRequest(RuntimeContract):
    """Request to create a queued runtime run for one user message."""

    conversation_id: str
    user_input: str
    content_format: str = Values.DEFAULT_CONTENT_FORMAT
    idempotency_key: str | None = None
    runtime_context: AgentRuntimeContext
    request_options: JsonObject = Field(default_factory=dict)

    @field_validator(Keys.Field.CONVERSATION_ID)
    @classmethod
    def _normalize_conversation_id(cls, value: object) -> str:
        return RuntimeApiValueNormalizer.normalize_id(value, Keys.Field.CONVERSATION_ID)

    @field_validator(Keys.Field.USER_INPUT, "content_format")
    @classmethod
    def _normalize_text(cls, value: object, info: ValidationInfo) -> str:
        return RuntimeApiValueNormalizer.normalize_nonempty_string(value, info.field_name)

    @field_validator(Keys.Field.IDEMPOTENCY_KEY, mode="before")
    @classmethod
    def _normalize_idempotency_key(cls, value: object) -> str | None:
        return RuntimeApiValueNormalizer.normalize_optional_id(value, Keys.Field.IDEMPOTENCY_KEY)

    @field_validator("request_options", mode="before")
    @classmethod
    def _redact_request_options(cls, value: object) -> JsonObject:
        return RuntimeApiValueNormalizer.redact_json_object(value)


class RunRecord(RuntimeContract):
    """Persisted runtime run state."""

    run_id: str
    conversation_id: str
    org_id: str
    user_id: str
    user_message_id: str
    idempotency_key: str | None = None
    trace_id: str
    status: AgentRunStatus = AgentRunStatus.QUEUED
    model_provider: str
    model_name: str
    runtime_context: AgentRuntimeContext
    request_options: JsonObject = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    safe_error: RuntimeErrorEnvelope | None = None
    latest_sequence_no: NonNegativeInt = 0

    @field_validator(
        Keys.Field.RUN_ID,
        Keys.Field.CONVERSATION_ID,
        Keys.Field.ORG_ID,
        Keys.Field.USER_ID,
        "user_message_id",
        Keys.Field.TRACE_ID,
        mode="before",
    )
    @classmethod
    def _normalize_ids(cls, value: object, info: ValidationInfo) -> str:
        return RuntimeApiValueNormalizer.normalize_id(value, info.field_name)

    @field_validator(Keys.Field.IDEMPOTENCY_KEY, mode="before")
    @classmethod
    def _normalize_idempotency_key(cls, value: object) -> str | None:
        return RuntimeApiValueNormalizer.normalize_optional_id(value, Keys.Field.IDEMPOTENCY_KEY)

    def to_response(self) -> "RunStatusResponse":
        """Return the public run status shape."""

        return RunStatusResponse(
            run_id=self.run_id,
            conversation_id=self.conversation_id,
            org_id=self.org_id,
            user_id=self.user_id,
            status=self.status,
            trace_id=self.trace_id,
            started_at=self.started_at,
            completed_at=self.completed_at,
            cancelled_at=self.cancelled_at,
            safe_error=self.safe_error,
            latest_sequence_no=self.latest_sequence_no,
        )


class CreateRunResponse(RuntimeContract):
    """Run handle returned after producer transaction commits."""

    run_id: str
    conversation_id: str
    user_message_id: str
    trace_id: str
    status: AgentRunStatus
    stream_url: str
    events_url: str
    created_at: datetime


class RunStatusResponse(RuntimeContract):
    """Current run status returned by run inspection and cancellation."""

    run_id: str
    conversation_id: str
    org_id: str
    user_id: str
    status: AgentRunStatus
    trace_id: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    safe_error: RuntimeErrorEnvelope | None = None
    latest_sequence_no: NonNegativeInt = 0


class RuntimeEventEnvelope(RuntimeContract):
    """Ordered transport event envelope shared by replay and streaming."""

    event_protocol_version: PositiveInt = Values.EVENT_PROTOCOL_VERSION
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    conversation_id: str
    sequence_no: PositiveInt
    source: StreamEventSource
    event_type: RuntimeApiEventType
    trace_id: str
    parent_task_id: str | None = None
    payload: JsonObject = Field(default_factory=dict)
    metadata: JsonObject = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator(
        Keys.Field.EVENT_ID,
        Keys.Field.RUN_ID,
        Keys.Field.CONVERSATION_ID,
        Keys.Field.TRACE_ID,
        mode="before",
    )
    @classmethod
    def _normalize_ids(cls, value: object, info: ValidationInfo) -> str:
        return RuntimeApiValueNormalizer.normalize_id(value, info.field_name)

    @field_validator(Keys.Field.PARENT_TASK_ID, mode="before")
    @classmethod
    def _normalize_parent_task_id(cls, value: object) -> str | None:
        return RuntimeApiValueNormalizer.normalize_optional_id(value, Keys.Field.PARENT_TASK_ID)

    @field_validator(Keys.Field.PAYLOAD, Keys.Field.METADATA, mode="before")
    @classmethod
    def _redact_json_fields(cls, value: object) -> JsonObject:
        return RuntimeApiValueNormalizer.redact_json_object(value)

    @classmethod
    def from_stream_event(
        cls,
        *,
        run_id: str,
        conversation_id: str,
        sequence_no: int,
        stream_event: StreamEvent,
    ) -> "RuntimeEventEnvelope":
        """Wrap an existing normalized runtime event in the API envelope."""

        return cls(
            event_id=stream_event.event_id,
            run_id=run_id,
            conversation_id=conversation_id,
            sequence_no=sequence_no,
            source=stream_event.source,
            event_type=RuntimeApiEventType.from_stream_event_type(stream_event.event_type),
            trace_id=stream_event.trace_id,
            parent_task_id=stream_event.parent_task_id,
            payload=stream_event.payload,
            metadata=stream_event.metadata,
            created_at=stream_event.timestamp,
        )


class RuntimeEventReplayResponse(RuntimeContract):
    """Replay response for persisted ordered events."""

    run_id: str
    events: tuple[RuntimeEventEnvelope, ...]
    latest_sequence_no: NonNegativeInt
    run_status: AgentRunStatus
    has_more: bool = False


class CancelRunRequest(RuntimeContract):
    """Request to cancel long-running work."""

    reason: str | None = None
    requested_by_user_id: str

    @field_validator(Keys.Field.REQUESTED_BY_USER_ID)
    @classmethod
    def _normalize_requested_by_user_id(cls, value: object) -> str:
        return RuntimeApiValueNormalizer.normalize_id(value, Keys.Field.REQUESTED_BY_USER_ID)

    @field_validator(Keys.Field.REASON, mode="before")
    @classmethod
    def _normalize_reason(cls, value: object) -> str | None:
        return RuntimeApiValueNormalizer.normalize_optional_text(value, Keys.Field.REASON)


class CancelRunResponse(RuntimeContract):
    """Cancellation request result."""

    run_id: str
    status: AgentRunStatus
    cancel_requested_at: datetime | None = None
    latest_sequence_no: NonNegativeInt


class ApprovalDecisionRequest(RuntimeContract):
    """Request to resolve a pending side-effect approval."""

    decision: ApprovalDecision
    decided_by_user_id: str
    reason: str | None = None

    @field_validator("decided_by_user_id")
    @classmethod
    def _normalize_decided_by_user_id(cls, value: object) -> str:
        return RuntimeApiValueNormalizer.normalize_id(value, "decided_by_user_id")

    @field_validator(Keys.Field.REASON, mode="before")
    @classmethod
    def _normalize_reason(cls, value: object) -> str | None:
        return RuntimeApiValueNormalizer.normalize_optional_text(value, Keys.Field.REASON)


class ApprovalDecisionRecord(RuntimeContract):
    """Persisted approval decision."""

    approval_id: str
    run_id: str
    conversation_id: str
    org_id: str
    user_id: str
    status: ApprovalStatus
    decided_by_user_id: str
    reason: str | None = None
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ApprovalRequestRecord(RuntimeContract):
    """Persisted pending approval request created by a runtime worker."""

    approval_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    conversation_id: str
    org_id: str
    user_id: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    metadata: JsonObject = Field(default_factory=dict)


class ApprovalDecisionResponse(RuntimeContract):
    """Approval decision result returned to clients."""

    approval_id: str
    run_id: str
    status: ApprovalStatus
    decided_at: datetime


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


class RuntimeRunCommand(RuntimeContract):
    """Durable command enqueued after run creation."""

    command_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    conversation_id: str
    org_id: str
    user_id: str
    trace_id: str
    runtime_context: AgentRuntimeContext
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RuntimeCancelCommand(RuntimeContract):
    """Durable command requesting best-effort run cancellation."""

    command_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    org_id: str
    requested_by_user_id: str
    reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RuntimeApprovalResolvedCommand(RuntimeContract):
    """Durable command notifying workers that an approval was resolved."""

    command_id: str = Field(default_factory=lambda: uuid4().hex)
    approval_id: str
    run_id: str
    org_id: str
    decision: ApprovalDecision
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RuntimeEventDraft(RuntimeContract):
    """Event data before the event store assigns per-run sequence number."""

    run_id: str
    conversation_id: str
    source: StreamEventSource
    event_type: RuntimeApiEventType
    trace_id: str
    parent_task_id: str | None = None
    payload: JsonObject = Field(default_factory=dict)
    metadata: JsonObject = Field(default_factory=dict)
