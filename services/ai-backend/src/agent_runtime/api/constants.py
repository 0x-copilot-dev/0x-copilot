"""Constants and public messages for the FastAPI runtime API."""

from __future__ import annotations

import re


class Keys:
    """Stable keys used by API contracts, stores, and transport adapters."""

    class Field:
        AFTER_SEQUENCE = "after_sequence"
        API_EVENT_TYPE = "api_event_type"
        APPROVAL_ID = "approval_id"
        APPROVAL_KIND = "approval_kind"
        # PR 1.4 — two-stage approval forwarding bookkeeping.
        ACTION_SUMMARY = "action_summary"
        CHAIN_PARENT_APPROVAL_ID = "chain_parent_approval_id"
        FORWARD_TO = "forward_to"
        FORWARDED_AT = "forwarded_at"
        FORWARDED_BY_USER_ID = "forwarded_by_user_id"
        FORWARDED_TO_USER_ID = "forwarded_to_user_id"
        ARGS = "args"
        ASSISTANT_ID = "assistant_id"
        AUTH_URL = "auth_url"
        CALL_ID = "call_id"
        CONTENT = "content"
        CONVERSATION_ID = "conversation_id"
        CORRELATION_ID = "correlation_id"
        CREATED_AT = "created_at"
        DECISION = "decision"
        DISPLAY_TITLE = "display_title"
        ERROR_COUNT = "error_count"
        EVENT_ID = "event_id"
        EVENT_TYPE = "event_type"
        EXPIRES_AT = "expires_at"
        FILE_PATH = "file_path"
        ID = "id"
        IDEMPOTENCY_KEY = "idempotency_key"
        MESSAGE_ID = "message_id"
        METADATA = "metadata"
        NAME = "name"
        ORG_ID = "org_id"
        OUTPUT = "output"
        PARENT_EVENT_ID = "parent_event_id"
        PARENT_SPAN_ID = "parent_span_id"
        PARENT_TASK_ID = "parent_task_id"
        PATH = "path"
        PAYLOAD = "payload"
        REASON = "reason"
        REDACTION_STATE = "redaction_state"
        REQUESTED_BY_USER_ID = "requested_by_user_id"
        RUN_ID = "run_id"
        SEQUENCE_NO = "sequence_no"
        SERVER_ID = "server_id"
        SERVER_NAME = "server_name"
        SHORT_SUMMARY = "short_summary"
        SOURCE = "source"
        SOURCE_TOOL_CALL_ID = "source_tool_call_id"
        SPAN_ID = "span_id"
        STATUS = "status"
        SUBAGENT_ID = "subagent_id"
        SUBAGENT_NAME = "subagent_name"
        SUMMARY = "summary"
        TASK_ID = "task_id"
        TITLE = "title"
        TOOL_CALL_ID = "tool_call_id"
        TOOL_NAME = "tool_name"
        TRACE_ID = "trace_id"
        TYPE = "type"
        USER_ID = "user_id"
        USER_INPUT = "user_input"
        VISIBILITY = "visibility"

    class Payload:
        DELTA = "delta"
        DISPLAY_TITLE = "display_title"
        MESSAGE = "message"
        REASON = "reason"
        SUMMARY = "summary"

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
        DELETE_USER_HISTORY = "delete_user_history"
        GET_CONVERSATION = "get_conversation"
        GET_CONVERSATION_CONTEXT = "get_conversation_context"
        GET_EVENTS = "get_events"
        GET_MESSAGES = "get_messages"
        GET_RUN = "get_run"
        LIST_CONVERSATIONS = "list_conversations"
        LIST_MODELS = "list_models"
        STREAM_RUN = "stream_run"
        UPDATE_CONVERSATION_CONNECTORS = "update_conversation_connectors"
        # Usage endpoints (B4)
        USAGE_ME = "usage_me"
        USAGE_ME_CONVERSATIONS = "usage_me_conversations"
        USAGE_RUN = "usage_run"
        USAGE_CONVERSATION = "usage_conversation"
        USAGE_ORG = "usage_org"
        # Budget endpoints (B7)
        BUDGETS_LIST = "budgets_list"
        BUDGETS_CREATE = "budgets_create"
        BUDGETS_UPDATE = "budgets_update"
        BUDGETS_DELETE = "budgets_delete"
        BUDGETS_ME = "budgets_me"
        # Drafts (PR 1.3)
        LIST_DRAFTS = "list_drafts"
        GET_DRAFT = "get_draft"
        PATCH_DRAFT = "patch_draft"
        SEND_DRAFT = "send_draft"
        DISCARD_DRAFT = "discard_draft"
        # Workspace pane feeds (PR 1.5)
        LIST_SUBAGENTS = "list_subagents"
        LIST_SOURCES = "list_sources"
        # Retention admin (C8)
        RETENTION_LIST = "retention_list"
        RETENTION_UPSERT = "retention_upsert"
        RETENTION_DELETE = "retention_delete"


class Values:
    """Stable public values for the API layer."""

    EVENT_PROTOCOL_VERSION = 1
    SCHEMA_VERSION = 1
    DEFAULT_ASSISTANT_ID = "default"
    DEFAULT_CONTENT_FORMAT = "text"
    DEFAULT_CONVERSATION_LIMIT = 30
    DEFAULT_MESSAGE_LIMIT = 50
    MAX_MESSAGE_LIMIT = 200
    SSE_EVENT_NAME = "runtime_event"

    class Status:
        ANSWERED = "answered"
        CANCELLED = "cancelled"
        COMPLETED = "completed"
        FAILED = "failed"
        # PR 1.4 — wire-level status emitted on APPROVAL_RESOLVED for the
        # parent row of a forwarded chain. Distinguishes the "the user
        # forwarded it on" outcome from approve / reject so the FE renders
        # a "Waiting on @marcus" pill instead of a resolved record.
        FORWARDED = "forwarded"
        QUEUED = "queued"
        RUNNING = "running"
        SKIPPED = "skipped"
        STARTED = "started"
        WAITING = "waiting"

    class Tool:
        ASK_A_QUESTION = "ask_a_question"
        GREP = "grep"
        READ_FILE = "read_file"
        RG = "rg"
        SEARCH_FILES = "search_files"
        TASK = "task"
        UNKNOWN_TOOL = "unknown_tool"
        WRITE_TODOS = "write_todos"

    class ApprovalKind:
        ACTION = "action"
        ASK_A_QUESTION = "ask_a_question"
        MCP_AUTH = "mcp_auth"
        MCP_TOOL = "mcp_tool"

    class VirtualPath:
        LARGE_TOOL_RESULTS_PREFIX = "/large_tool_results/"


class Patterns:
    """Compiled validators for API IDs and slugs."""

    ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class Messages:
    """Centralized safe messages returned to API clients."""

    class Error:
        APPROVAL_NOT_FOUND = "Approval request was not found for this scope."
        # PR 1.4 — forwarding-target validation. Messages are deliberately
        # generic and do not reveal whether the target user exists in
        # another tenant.
        APPROVAL_FORWARD_INVALID_TARGET = (
            "Forward target user is not an active member of this workspace."
        )
        APPROVAL_FORWARD_KIND_NOT_SUPPORTED = "This approval kind cannot be forwarded."
        APPROVAL_FORWARD_SELF = "Cannot forward an approval to yourself."
        APPROVAL_FORWARD_NOT_PENDING = "Only pending approvals can be forwarded."
        APPROVAL_FORWARD_CHAIN_TOO_DEEP = (
            "Forwarding chain is too deep; resolve the existing chain first."
        )
        CONVERSATION_NOT_FOUND = "Conversation was not found for this scope."
        IDEMPOTENCY_CONFLICT = "Idempotency key conflicts with a different request."
        INVALID_CONNECTOR_SCOPES = "Connector scope payload is invalid."
        INVALID_REQUEST = "Request payload is invalid."
        RUN_NOT_FOUND = "Run was not found for this scope."
        SAFE_FALLBACK = "The runtime API could not complete the request safely."

    class Audit:
        # PR 1.4 — append-only audit action for the forward link. The
        # parent's final outcome is still ``approval_decision_recorded``
        # (the existing action). ``approval.forward`` records the act of
        # forwarding with chain_parent_approval_id metadata so SIEM
        # exports can reconstruct chains end-to-end.
        APPROVAL_FORWARD = "approval.forward"
        # PR 1.2 — per-chat connector scope mutation; metadata captures
        # ``before`` / ``after`` / ``diff_keys`` for forensic replay.
        CONVERSATION_CONNECTORS_UPDATE = "conversation.connectors.update"

    class Event:
        APPROVAL_RESOLVED = "Approval decision was recorded."
        APPROVAL_FORWARDED = "Approval forwarded for sign-off."
        FINAL_RESPONSE = "Final response"
        HEARTBEAT = "Runtime stream heartbeat."
        INTERNAL_TODO_PROGRESS_PREFIX = "Updated todo list"
        MCP_AUTH_REQUIRED = "MCP authentication required"
        MODEL_DELTA = "Model response"
        REASONING = "Thinking"
        RUN_CANCELLING = "Run cancellation was requested."
        RUN_QUEUED = "Run was queued for runtime execution."
        SUBAGENT = "Subagent update"
        TOOL_CALL = "Calling tool"
        TOOL_RESULT = "Tool result"

        @classmethod
        def subagent_title(cls, subagent_name: str) -> str:
            return f"{subagent_name} subagent"

        @classmethod
        def tool_completed_title(cls, tool_name: str) -> str:
            return f"{tool_name} completed"

        @classmethod
        def tool_result_title(cls, tool_name: str) -> str:
            return f"{tool_name} result"

        @classmethod
        def tool_running_title(cls, tool_name: str) -> str:
            return f"{tool_name} running"

        @classmethod
        def tool_started_title(cls, tool_name: str) -> str:
            return f"Calling {tool_name}"

        @classmethod
        def source_cited_title(cls, title: str) -> str:
            return f"Cited {title}"

        SOURCE_INGESTED = "Cited a source"

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
