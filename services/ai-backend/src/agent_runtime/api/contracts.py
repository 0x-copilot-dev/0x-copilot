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


class RuntimeEventPresentationProjector:
    """Project normalized runtime events into stable UI timeline semantics."""

    SUBAGENT_STARTED_STATUSES = frozenset(
        {
            Values.Status.QUEUED,
            Values.Status.STARTED,
        }
    )
    SUBAGENT_COMPLETED_STATUSES = frozenset(
        {
            Values.Status.CANCELLED,
            Values.Status.COMPLETED,
            Values.Status.FAILED,
            "succeeded",
            "success",
        }
    )

    @classmethod
    def event_type_for_stream_event(cls, stream_event: StreamEvent) -> RuntimeApiEventType:
        """Return the most specific API event type for a normalized runtime event."""

        override = cls._event_type_override(stream_event.payload, stream_event.metadata)
        if override is not None:
            return override
        if stream_event.event_type is StreamEventType.TOOL_CALL:
            return RuntimeApiEventType.TOOL_CALL_STARTED
        if stream_event.event_type is StreamEventType.TOOL_RESULT:
            return RuntimeApiEventType.TOOL_CALL_COMPLETED
        if stream_event.event_type in {
            StreamEventType.LIFECYCLE,
            StreamEventType.SUBAGENT_UPDATE,
        }:
            return cls._subagent_event_type(stream_event.payload)
        if stream_event.source is StreamEventSource.SUBAGENT and stream_event.event_type in {
            StreamEventType.CUSTOM,
            StreamEventType.PROGRESS,
        }:
            return RuntimeApiEventType.SUBAGENT_PROGRESS
        return RuntimeApiEventType.from_stream_event_type(stream_event.event_type)

    @classmethod
    def payload_for_event(
        cls,
        *,
        event_type: RuntimeApiEventType,
        payload: JsonObject,
    ) -> JsonObject:
        """Return the client-visible payload for an API event type."""

        if event_type in {
            RuntimeApiEventType.REASONING_SUMMARY,
            RuntimeApiEventType.REASONING_SUMMARY_DELTA,
        }:
            return cls._reasoning_summary_payload(event_type=event_type, payload=payload)
        return payload

    @classmethod
    def presentation_fields(
        cls,
        *,
        event_type: RuntimeApiEventType,
        source: StreamEventSource,
        parent_task_id: str | None,
        payload: JsonObject,
        metadata: JsonObject,
    ) -> dict[str, object]:
        """Return additive UI timeline fields for an event envelope or draft."""

        task_id = cls._text(payload.get(Keys.Field.TASK_ID)) or parent_task_id
        subagent_id = cls._text(payload.get(Keys.Field.SUBAGENT_NAME)) or cls._text(
            payload.get(Keys.Field.SUBAGENT_ID)
        )
        span_id = cls._span_id_for(event_type=event_type, task_id=task_id, payload=payload)
        return {
            Keys.Field.PARENT_EVENT_ID: cls._text(
                payload.get(Keys.Field.PARENT_EVENT_ID),
            )
            or cls._text(metadata.get(Keys.Field.PARENT_EVENT_ID)),
            Keys.Field.SPAN_ID: span_id,
            Keys.Field.PARENT_SPAN_ID: cls._text(
                payload.get(Keys.Field.PARENT_SPAN_ID),
            )
            or cls._text(metadata.get(Keys.Field.PARENT_SPAN_ID))
            or parent_task_id,
            Keys.Field.TASK_ID: task_id,
            Keys.Field.SUBAGENT_ID: subagent_id,
            Keys.Field.DISPLAY_TITLE: cls._display_title_for(
                event_type=event_type,
                payload=payload,
            ),
            Keys.Field.SUMMARY: cls._summary_for(payload=payload, metadata=metadata),
            Keys.Field.STATUS: cls._status_for(event_type=event_type, payload=payload),
            Keys.Field.VISIBILITY: cls._visibility_for(source=source, payload=payload),
            Keys.Field.REDACTION_STATE: cls._redaction_state_for(
                payload=payload,
                metadata=metadata,
            ),
        }

    @classmethod
    def _event_type_override(
        cls,
        payload: JsonObject,
        metadata: JsonObject,
    ) -> RuntimeApiEventType | None:
        value = cls._text(payload.get(Keys.Field.API_EVENT_TYPE)) or cls._text(
            metadata.get(Keys.Field.API_EVENT_TYPE)
        )
        if value is None:
            return None
        try:
            return RuntimeApiEventType(value)
        except ValueError:
            return None

    @classmethod
    def _subagent_event_type(cls, payload: JsonObject) -> RuntimeApiEventType:
        status = cls._status_text(payload)
        if status in cls.SUBAGENT_STARTED_STATUSES:
            return RuntimeApiEventType.SUBAGENT_STARTED
        if status in cls.SUBAGENT_COMPLETED_STATUSES:
            return RuntimeApiEventType.SUBAGENT_COMPLETED
        return RuntimeApiEventType.SUBAGENT_PROGRESS

    @classmethod
    def _reasoning_summary_payload(
        cls,
        *,
        event_type: RuntimeApiEventType,
        payload: JsonObject,
    ) -> JsonObject:
        summary = cls._text(payload.get(Keys.Field.SUMMARY)) or cls._text(
            payload.get(Keys.Payload.MESSAGE)
        )
        safe_payload: JsonObject = {}
        if summary is not None:
            safe_payload[Keys.Field.SUMMARY] = summary
        if event_type is RuntimeApiEventType.REASONING_SUMMARY_DELTA:
            delta = cls._text(payload.get(Keys.Payload.DELTA))
            if delta is not None:
                safe_payload[Keys.Payload.DELTA] = delta
        return safe_payload

    @classmethod
    def _span_id_for(
        cls,
        *,
        event_type: RuntimeApiEventType,
        task_id: str | None,
        payload: JsonObject,
    ) -> str | None:
        configured_span_id = cls._text(payload.get(Keys.Field.SPAN_ID))
        if configured_span_id is not None:
            return configured_span_id
        if event_type in {
            RuntimeApiEventType.TOOL_CALL,
            RuntimeApiEventType.TOOL_CALL_STARTED,
            RuntimeApiEventType.TOOL_CALL_DELTA,
            RuntimeApiEventType.TOOL_RESULT,
            RuntimeApiEventType.TOOL_CALL_COMPLETED,
        }:
            return cls._text(payload.get(Keys.Field.CALL_ID))
        if event_type in {
            RuntimeApiEventType.SUBAGENT_UPDATE,
            RuntimeApiEventType.SUBAGENT_STARTED,
            RuntimeApiEventType.SUBAGENT_PROGRESS,
            RuntimeApiEventType.SUBAGENT_COMPLETED,
        }:
            return task_id
        return None

    @classmethod
    def _display_title_for(
        cls,
        *,
        event_type: RuntimeApiEventType,
        payload: JsonObject,
    ) -> str | None:
        configured = cls._text(payload.get(Keys.Field.DISPLAY_TITLE)) or cls._text(
            payload.get(Keys.Payload.DISPLAY_TITLE)
        )
        if configured is not None:
            return configured
        tool_name = cls._text(payload.get(Keys.Field.TOOL_NAME))
        if event_type is RuntimeApiEventType.TOOL_CALL_STARTED:
            if tool_name is None:
                return Messages.Event.TOOL_CALL
            return Messages.Event.tool_started_title(tool_name)
        if event_type is RuntimeApiEventType.TOOL_CALL_COMPLETED:
            if tool_name is None:
                return Messages.Event.TOOL_CALL
            return Messages.Event.tool_completed_title(tool_name)
        subagent_name = cls._text(payload.get(Keys.Field.SUBAGENT_NAME))
        if event_type in {
            RuntimeApiEventType.SUBAGENT_STARTED,
            RuntimeApiEventType.SUBAGENT_PROGRESS,
            RuntimeApiEventType.SUBAGENT_COMPLETED,
            RuntimeApiEventType.SUBAGENT_UPDATE,
        }:
            if subagent_name is None:
                return Messages.Event.SUBAGENT
            return Messages.Event.subagent_title(subagent_name)
        if event_type in {
            RuntimeApiEventType.REASONING_SUMMARY,
            RuntimeApiEventType.REASONING_SUMMARY_DELTA,
        }:
            return Messages.Event.REASONING
        if event_type is RuntimeApiEventType.FINAL_RESPONSE:
            return Messages.Event.FINAL_RESPONSE
        return None

    @classmethod
    def _summary_for(cls, *, payload: JsonObject, metadata: JsonObject) -> str | None:
        return (
            cls._text(payload.get(Keys.Field.SUMMARY))
            or cls._text(payload.get(Keys.Payload.MESSAGE))
            or cls._text(metadata.get(Keys.Field.SUMMARY))
        )

    @classmethod
    def _status_for(
        cls,
        *,
        event_type: RuntimeApiEventType,
        payload: JsonObject,
    ) -> str | None:
        configured = cls._status_text(payload)
        if configured is not None:
            return configured
        if event_type in {RuntimeApiEventType.RUN_QUEUED}:
            return Values.Status.QUEUED
        if event_type in {
            RuntimeApiEventType.RUN_STARTED,
            RuntimeApiEventType.TOOL_CALL_STARTED,
            RuntimeApiEventType.SUBAGENT_STARTED,
        }:
            return Values.Status.STARTED
        if event_type in {
            RuntimeApiEventType.PROGRESS,
            RuntimeApiEventType.REASONING_SUMMARY,
            RuntimeApiEventType.REASONING_SUMMARY_DELTA,
            RuntimeApiEventType.SUBAGENT_PROGRESS,
        }:
            return Values.Status.RUNNING
        if event_type in {
            RuntimeApiEventType.RUN_COMPLETED,
            RuntimeApiEventType.TOOL_CALL_COMPLETED,
            RuntimeApiEventType.SUBAGENT_COMPLETED,
            RuntimeApiEventType.FINAL_RESPONSE,
        }:
            return Values.Status.COMPLETED
        if event_type in {RuntimeApiEventType.RUN_FAILED, RuntimeApiEventType.ERROR}:
            return Values.Status.FAILED
        if event_type is RuntimeApiEventType.RUN_CANCELLED:
            return Values.Status.CANCELLED
        return None

    @classmethod
    def _visibility_for(
        cls,
        *,
        source: StreamEventSource,
        payload: JsonObject,
    ) -> RuntimeEventVisibility:
        configured = cls._text(payload.get(Keys.Field.VISIBILITY))
        if configured is not None:
            try:
                return RuntimeEventVisibility(configured)
            except ValueError:
                return RuntimeEventVisibility.USER
        if source is StreamEventSource.SUMMARIZATION:
            return RuntimeEventVisibility.INTERNAL
        return RuntimeEventVisibility.USER

    @classmethod
    def _redaction_state_for(
        cls,
        *,
        payload: JsonObject,
        metadata: JsonObject,
    ) -> RuntimeEventRedactionState:
        configured = cls._text(payload.get(Keys.Field.REDACTION_STATE)) or cls._text(
            metadata.get(Keys.Field.REDACTION_STATE)
        )
        if configured is not None:
            try:
                return RuntimeEventRedactionState(configured)
            except ValueError:
                return RuntimeEventRedactionState.REDACTED
        if cls._contains_payload_ref(payload):
            return RuntimeEventRedactionState.OFFLOADED
        if "[truncated]" in str(payload):
            return RuntimeEventRedactionState.TRUNCATED
        return RuntimeEventRedactionState.REDACTED

    @classmethod
    def _contains_payload_ref(cls, payload: JsonObject) -> bool:
        return any("ref" in key.lower() for key in payload)

    @classmethod
    def _status_text(cls, payload: JsonObject) -> str | None:
        value = cls._text(payload.get(Keys.Field.STATUS))
        if value is None:
            return None
        return value.lower()

    @classmethod
    def _text(cls, value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        if not normalized:
            return None
        return normalized


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
    parent_event_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    parent_task_id: str | None = None
    task_id: str | None = None
    subagent_id: str | None = None
    display_title: str | None = None
    summary: str | None = None
    status: str | None = None
    visibility: RuntimeEventVisibility = RuntimeEventVisibility.USER
    redaction_state: RuntimeEventRedactionState = RuntimeEventRedactionState.REDACTED
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

    @field_validator(
        Keys.Field.PARENT_EVENT_ID,
        Keys.Field.SPAN_ID,
        Keys.Field.PARENT_SPAN_ID,
        Keys.Field.PARENT_TASK_ID,
        Keys.Field.TASK_ID,
        Keys.Field.SUBAGENT_ID,
        mode="before",
    )
    @classmethod
    def _normalize_optional_ids(cls, value: object, info: ValidationInfo) -> str | None:
        return RuntimeApiValueNormalizer.normalize_optional_id(value, info.field_name)

    @field_validator(
        Keys.Field.DISPLAY_TITLE,
        Keys.Field.SUMMARY,
        Keys.Field.STATUS,
        mode="before",
    )
    @classmethod
    def _normalize_optional_text(cls, value: object, info: ValidationInfo) -> str | None:
        return RuntimeApiValueNormalizer.normalize_optional_text(value, info.field_name)

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

        event_type = RuntimeEventPresentationProjector.event_type_for_stream_event(stream_event)
        payload = RuntimeEventPresentationProjector.payload_for_event(
            event_type=event_type,
            payload=stream_event.payload,
        )
        presentation = RuntimeEventPresentationProjector.presentation_fields(
            event_type=event_type,
            source=stream_event.source,
            parent_task_id=stream_event.parent_task_id,
            payload=payload,
            metadata=stream_event.metadata,
        )
        return cls(
            event_id=stream_event.event_id,
            run_id=run_id,
            conversation_id=conversation_id,
            sequence_no=sequence_no,
            source=stream_event.source,
            event_type=event_type,
            trace_id=stream_event.trace_id,
            parent_task_id=stream_event.parent_task_id,
            payload=payload,
            metadata=stream_event.metadata,
            created_at=stream_event.timestamp,
            **presentation,
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
    parent_event_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    parent_task_id: str | None = None
    task_id: str | None = None
    subagent_id: str | None = None
    display_title: str | None = None
    summary: str | None = None
    status: str | None = None
    visibility: RuntimeEventVisibility = RuntimeEventVisibility.USER
    redaction_state: RuntimeEventRedactionState = RuntimeEventRedactionState.REDACTED
    payload: JsonObject = Field(default_factory=dict)
    metadata: JsonObject = Field(default_factory=dict)

    @field_validator(Keys.Field.RUN_ID, Keys.Field.CONVERSATION_ID, Keys.Field.TRACE_ID)
    @classmethod
    def _normalize_ids(cls, value: object, info: ValidationInfo) -> str:
        return RuntimeApiValueNormalizer.normalize_id(value, info.field_name)

    @field_validator(
        Keys.Field.PARENT_EVENT_ID,
        Keys.Field.SPAN_ID,
        Keys.Field.PARENT_SPAN_ID,
        Keys.Field.PARENT_TASK_ID,
        Keys.Field.TASK_ID,
        Keys.Field.SUBAGENT_ID,
        mode="before",
    )
    @classmethod
    def _normalize_optional_ids(cls, value: object, info: ValidationInfo) -> str | None:
        return RuntimeApiValueNormalizer.normalize_optional_id(value, info.field_name)

    @field_validator(
        Keys.Field.DISPLAY_TITLE,
        Keys.Field.SUMMARY,
        Keys.Field.STATUS,
        mode="before",
    )
    @classmethod
    def _normalize_optional_text(cls, value: object, info: ValidationInfo) -> str | None:
        return RuntimeApiValueNormalizer.normalize_optional_text(value, info.field_name)

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
        stream_event: StreamEvent,
    ) -> "RuntimeEventDraft":
        """Create an appendable API event draft from a normalized runtime event."""

        event_type = RuntimeEventPresentationProjector.event_type_for_stream_event(stream_event)
        payload = RuntimeEventPresentationProjector.payload_for_event(
            event_type=event_type,
            payload=stream_event.payload,
        )
        presentation = RuntimeEventPresentationProjector.presentation_fields(
            event_type=event_type,
            source=stream_event.source,
            parent_task_id=stream_event.parent_task_id,
            payload=payload,
            metadata=stream_event.metadata,
        )
        return cls(
            run_id=run_id,
            conversation_id=conversation_id,
            source=stream_event.source,
            event_type=event_type,
            trace_id=stream_event.trace_id,
            parent_task_id=stream_event.parent_task_id,
            payload=payload,
            metadata=stream_event.metadata,
            **presentation,
        )
