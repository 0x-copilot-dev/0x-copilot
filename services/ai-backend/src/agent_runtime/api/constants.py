"""Constants and public messages for the FastAPI runtime API."""

from __future__ import annotations

import re


class Keys:
    """Stable keys used by API contracts, stores, and transport adapters."""

    class Field:
        AFTER_SEQUENCE = "after_sequence"
        APPROVAL_ID = "approval_id"
        ASSISTANT_ID = "assistant_id"
        CONVERSATION_ID = "conversation_id"
        CORRELATION_ID = "correlation_id"
        CREATED_AT = "created_at"
        DECISION = "decision"
        EVENT_ID = "event_id"
        EVENT_TYPE = "event_type"
        IDEMPOTENCY_KEY = "idempotency_key"
        MESSAGE_ID = "message_id"
        METADATA = "metadata"
        ORG_ID = "org_id"
        PARENT_TASK_ID = "parent_task_id"
        PAYLOAD = "payload"
        REASON = "reason"
        REQUESTED_BY_USER_ID = "requested_by_user_id"
        RUN_ID = "run_id"
        SEQUENCE_NO = "sequence_no"
        SOURCE = "source"
        STATUS = "status"
        TITLE = "title"
        TRACE_ID = "trace_id"
        USER_ID = "user_id"
        USER_INPUT = "user_input"

    class Payload:
        MESSAGE = "message"
        REASON = "reason"

    class Query:
        AFTER_SEQUENCE = "after_sequence"
        LIMIT = "limit"
        ORG_ID = "org_id"
        USER_ID = "user_id"

    class RouteName:
        APPROVAL_DECISION = "approval_decision"
        CANCEL_RUN = "cancel_run"
        CREATE_CONVERSATION = "create_conversation"
        CREATE_RUN = "create_run"
        GET_CONVERSATION = "get_conversation"
        GET_EVENTS = "get_events"
        GET_MESSAGES = "get_messages"
        GET_RUN = "get_run"
        STREAM_RUN = "stream_run"


class Values:
    """Stable public values for the API layer."""

    EVENT_PROTOCOL_VERSION = 1
    SCHEMA_VERSION = 1
    DEFAULT_ASSISTANT_ID = "default"
    DEFAULT_CONTENT_FORMAT = "text"
    DEFAULT_MESSAGE_LIMIT = 50
    MAX_MESSAGE_LIMIT = 200
    SSE_EVENT_NAME = "runtime_event"


class Patterns:
    """Compiled validators for API IDs and slugs."""

    ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class Messages:
    """Centralized safe messages returned to API clients."""

    class Error:
        APPROVAL_NOT_FOUND = "Approval request was not found for this scope."
        CONVERSATION_NOT_FOUND = "Conversation was not found for this scope."
        IDEMPOTENCY_CONFLICT = "Idempotency key conflicts with a different request."
        INVALID_REQUEST = "Request payload is invalid."
        RUN_NOT_FOUND = "Run was not found for this scope."
        SAFE_FALLBACK = "The runtime API could not complete the request safely."

    class Event:
        APPROVAL_RESOLVED = "Approval decision was recorded."
        HEARTBEAT = "Runtime stream heartbeat."
        RUN_CANCELLING = "Run cancellation was requested."
        RUN_QUEUED = "Run was queued for runtime execution."

    class Validation:
        @classmethod
        def id_contains_unsupported_characters(cls, field_name: str) -> str:
            return f"{field_name} contains unsupported characters"

        @classmethod
        def nonempty_string(cls, field_name: str) -> str:
            return f"{field_name} must not be empty"

        @classmethod
        def stable_slug(cls, field_name: str) -> str:
            return f"{field_name} must be a stable slug"

        @classmethod
        def string_required(cls, field_name: str) -> str:
            return f"{field_name} must be a string"
