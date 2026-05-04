"""Conversation and message API schemas."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import Field, NonNegativeInt, PositiveInt, ValidationInfo, field_validator

from agent_runtime.execution.contracts import JsonObject, RuntimeContract
from agent_runtime.api.constants import Keys, Values
from agent_runtime.observability.redaction import ObservabilityRedactor
from agent_runtime.validation import ValueNormalizer
from runtime_api.schemas.common import (
    ConversationStatus,
    MessageRole,
    MessageStatus,
)


class _Fields:
    CONTENT_TEXT = "content_text"
    CONTENT_FORMAT = "content_format"
    PARENT_MESSAGE_ID = "parent_message_id"
    SOURCE_MESSAGE_ID = "source_message_id"
    BRANCH_ID = "branch_id"


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
        return ValueNormalizer.normalize_id(value, info.field_name)

    @field_validator(Keys.Field.TITLE, mode="before")
    @classmethod
    def _normalize_title(cls, value: object) -> str | None:
        return ValueNormalizer.normalize_optional_text(value, Keys.Field.TITLE)

    @field_validator(Keys.Field.IDEMPOTENCY_KEY, mode="before")
    @classmethod
    def _normalize_idempotency_key(cls, value: object) -> str | None:
        return ValueNormalizer.normalize_optional_id(value, Keys.Field.IDEMPOTENCY_KEY)

    @field_validator(Keys.Field.METADATA, mode="before")
    @classmethod
    def _redact_metadata(cls, value: object) -> JsonObject:
        return ObservabilityRedactor.redact_json_object(value)


class ConversationRecord(RuntimeContract):
    """Persisted conversation metadata."""

    conversation_id: str = Field(default_factory=lambda: uuid4().hex)
    org_id: str
    user_id: str
    assistant_id: str
    title: str | None = None
    status: ConversationStatus = ConversationStatus.ACTIVE
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
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
        return ValueNormalizer.normalize_id(value, info.field_name)

    @field_validator(Keys.Field.IDEMPOTENCY_KEY, mode="before")
    @classmethod
    def _normalize_idempotency_key(cls, value: object) -> str | None:
        return ValueNormalizer.normalize_optional_id(value, Keys.Field.IDEMPOTENCY_KEY)

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


class ConversationListResponse(RuntimeContract):
    """Paginated conversation metadata for a caller scope."""

    conversations: tuple[ConversationResponse, ...]
    next_cursor: str | None = None
    has_more: bool = False


class MessageRecord(RuntimeContract):
    """Persisted conversation message."""

    message_id: str = Field(default_factory=lambda: uuid4().hex)
    conversation_id: str
    org_id: str
    run_id: str | None = None
    role: MessageRole
    content_text: str
    content_format: str = Values.DEFAULT_CONTENT_FORMAT
    content: tuple[JsonObject, ...] = ()
    attachments: tuple[JsonObject, ...] = ()
    quote: JsonObject | None = None
    metadata: JsonObject = Field(default_factory=dict)
    parent_message_id: str | None = None
    source_message_id: str | None = None
    branch_id: str | None = None
    token_count: NonNegativeInt | None = None
    trace_id: str | None = None
    status: MessageStatus = MessageStatus.CREATED
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
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
        return ValueNormalizer.normalize_id(value, info.field_name)

    @field_validator(
        Keys.Field.RUN_ID,
        _Fields.PARENT_MESSAGE_ID,
        _Fields.SOURCE_MESSAGE_ID,
        _Fields.BRANCH_ID,
        Keys.Field.TRACE_ID,
        mode="before",
    )
    @classmethod
    def _normalize_optional_ids(cls, value: object, info: ValidationInfo) -> str | None:
        return ValueNormalizer.normalize_optional_id(value, info.field_name)

    @field_validator(Keys.Field.METADATA, mode="before")
    @classmethod
    def _redact_metadata(cls, value: object) -> JsonObject:
        return ObservabilityRedactor.redact_json_object(value)

    @field_validator(_Fields.CONTENT_TEXT, _Fields.CONTENT_FORMAT)
    @classmethod
    def _normalize_text(cls, value: object, info: ValidationInfo) -> str:
        return ValueNormalizer.normalize_nonempty_string(value, info.field_name)

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
            content=self.content,
            attachments=self.attachments,
            quote=self.quote,
            metadata=self.metadata,
            parent_message_id=self.parent_message_id,
            source_message_id=self.source_message_id,
            branch_id=self.branch_id,
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
    content: tuple[JsonObject, ...] = ()
    attachments: tuple[JsonObject, ...] = ()
    quote: JsonObject | None = None
    metadata: JsonObject = Field(default_factory=dict)
    parent_message_id: str | None = None
    source_message_id: str | None = None
    branch_id: str | None = None
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


class HistoryDeletionResponse(RuntimeContract):
    """Audit-safe result for deleting a user's visible runtime history."""

    org_id: str
    user_id: str
    conversations_archived: NonNegativeInt = 0
    messages_tombstoned: NonNegativeInt = 0
    runs_cancelled: NonNegativeInt = 0
    events_retained: NonNegativeInt = 0
    audit_event_id: str | None = None


# ---------------------------------------------------------------------------
# Conversation context (B5 — `/context` slash command).
#
# Joins the latest run-level usage row (B1) with the per-call rows (B2),
# the compression event log, and the model's pricing context window (B3).
# Server returns integer ``headroom_pct`` so the UI never re-derives it.
# ---------------------------------------------------------------------------


class ContextWindowSummary(RuntimeContract):
    """Model + context-window descriptor for the latest run."""

    provider: str
    name: str
    context_window_tokens: NonNegativeInt | None = None  # None = model not in pricing


class ContextCurrentSlice(RuntimeContract):
    """Token state for the latest completed run in the conversation."""

    last_run_id: str | None = None
    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0
    cached_input_tokens: NonNegativeInt = 0
    available_tokens: NonNegativeInt | None = None
    headroom_pct: int | None = Field(default=None, ge=0, le=100)


class ContextCallRow(RuntimeContract):
    """One LLM call inside ``ContextBreakdown.by_call``."""

    event_id: str
    model_name: str
    input: NonNegativeInt = 0
    output: NonNegativeInt = 0
    cached_input: NonNegativeInt = 0
    task_id: str | None = None


class ContextSubagentRow(RuntimeContract):
    """One subagent inside ``ContextBreakdown.by_subagent``."""

    subagent_id: str
    name: str
    total: NonNegativeInt = 0
    call_count: NonNegativeInt = 0


class ContextCompressionRow(RuntimeContract):
    """One context compression event for the run."""

    before: NonNegativeInt
    after: NonNegativeInt
    strategy: str
    at: datetime


class ContextBreakdown(RuntimeContract):
    """Per-call, per-subagent, and compression-event breakdown."""

    by_call: tuple[ContextCallRow, ...] = ()
    by_subagent: tuple[ContextSubagentRow, ...] = ()
    compression_events: tuple[ContextCompressionRow, ...] = ()


class ConversationContextResponse(RuntimeContract):
    """Response shape for ``GET /v1/agent/conversations/{id}/context``."""

    model: ContextWindowSummary
    current: ContextCurrentSlice
    breakdown: ContextBreakdown
