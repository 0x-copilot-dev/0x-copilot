"""Conversation and message API schemas."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import Field, NonNegativeInt, PositiveInt, ValidationInfo, field_validator

from agent_runtime.execution.contracts import JsonObject, RuntimeContract
from agent_runtime.api.constants import Keys, Values
from runtime_api.schemas.common import (
    ConversationStatus,
    MessageRole,
    MessageStatus,
    RuntimeApiValueNormalizer,
)


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
        return RuntimeApiValueNormalizer.normalize_optional_text(
            value, Keys.Field.TITLE
        )

    @field_validator(Keys.Field.IDEMPOTENCY_KEY, mode="before")
    @classmethod
    def _normalize_idempotency_key(cls, value: object) -> str | None:
        return RuntimeApiValueNormalizer.normalize_optional_id(
            value, Keys.Field.IDEMPOTENCY_KEY
        )

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
        return RuntimeApiValueNormalizer.normalize_optional_id(
            value, Keys.Field.IDEMPOTENCY_KEY
        )

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

    @field_validator(
        Keys.Field.RUN_ID, "parent_message_id", Keys.Field.TRACE_ID, mode="before"
    )
    @classmethod
    def _normalize_optional_ids(cls, value: object, info: ValidationInfo) -> str | None:
        return RuntimeApiValueNormalizer.normalize_optional_id(value, info.field_name)

    @field_validator("content_text", "content_format")
    @classmethod
    def _normalize_text(cls, value: object, info: ValidationInfo) -> str:
        return RuntimeApiValueNormalizer.normalize_nonempty_string(
            value, info.field_name
        )

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


class HistoryDeletionResponse(RuntimeContract):
    """Audit-safe result for deleting a user's visible runtime history."""

    org_id: str
    user_id: str
    conversations_archived: NonNegativeInt = 0
    messages_tombstoned: NonNegativeInt = 0
    runs_cancelled: NonNegativeInt = 0
    events_retained: NonNegativeInt = 0
    audit_event_id: str | None = None
