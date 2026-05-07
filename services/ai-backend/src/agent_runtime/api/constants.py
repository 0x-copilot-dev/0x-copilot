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
        # PR 3.3 — non-blocking MCP discovery payload fields. The card
        # variant is keyed off ``DISCOVERY_REASON`` (presence flips it
        # from a blocking auth gate to a Connect/Skip suggestion).
        DISCOVERY_REASON = "discovery_reason"
        EXPECTED_VALUE = "expected_value"
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
        # PR 4.4.6.4 — undo within the 60s reversibility window.
        APPROVAL_UNDO = "approval_undo"
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
        # PR 1.4.1 — recipient inbox endpoint + per-user SSE channel.
        LIST_APPROVALS = "list_approvals"
        STREAM_INBOX = "stream_inbox"
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
        # PR 4.3 — read-only effective TTL view exposed to any tenant
        # member (the Privacy & data Settings panel renders against it).
        RETENTION_EFFECTIVE = "retention_effective"
        # PR 1.6 — workspace defaults + conversation lifecycle.
        GET_WORKSPACE_DEFAULTS = "get_workspace_defaults"
        UPDATE_WORKSPACE_DEFAULTS = "update_workspace_defaults"
        UPDATE_CONVERSATION = "update_conversation"
        DELETE_CONVERSATION = "delete_conversation"
        RESTORE_CONVERSATION = "restore_conversation"
        # PR 4.3 — workspace data lifecycle stubs (export queues + audited
        # delete-all attempt). The actual export pipeline + cascade-delete
        # job land in dedicated follow-ups.
        REQUEST_WORKSPACE_EXPORT = "request_workspace_export"
        DELETE_WORKSPACE_DATA = "delete_workspace_data"
        # PR 6.2 — recipient forks a shared chat into their own workspace.
        FORK_SHARE = "fork_share"
        # PR A3 / 8.0.3c — owner forks their own conversation from a
        # message ("Retry from here"). Same target conversation row shape
        # as the share-fork path; the difference is in the source-side
        # validation (own-org only, no share token).
        FORK_CONVERSATION = "fork_conversation"


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
    # PR 1.4.1 — sentinel actor for system-driven approval rejections
    # (auto-expiry sweeper, membership-revocation cascade). The audit
    # emitter sees this and records ``actor_type=system`` instead of
    # ``user`` so SIEM exports distinguish operator-driven from
    # background-driven rejections.
    SYSTEM_USER_ID = "system:runtime"
    DEFAULT_ASSIGNED_APPROVAL_LIMIT = 50
    MAX_ASSIGNED_APPROVAL_LIMIT = 200

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
        # PR 3.3 — non-blocking MCP discovery tool. Agent calls this when
        # an *unauthenticated* MCP server would improve the answer; the
        # tool emits an ``mcp_auth_required`` event with ``discovery_reason``
        # set so the FE renders a Connect/Skip card without pausing the run.
        SUGGEST_MCP_CONNECTOR = "suggest_mcp_connector"
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
        # PR 1.6 — workspace defaults model validation. Messages stay
        # generic (no leaking the catalog membership rules) and pair
        # with a 422 so the FE can render a field-level error.
        UNKNOWN_MODEL_PROVIDER = "Default model provider is not in the catalog."
        UNKNOWN_MODEL_NAME = "Default model name is not in the catalog."

    class Audit:
        # PR 1.4 — append-only audit action for the forward link. The
        # parent's final outcome is still ``approval_decision_recorded``
        # (the existing action). ``approval.forward`` records the act of
        # forwarding with chain_parent_approval_id metadata so SIEM
        # exports can reconstruct chains end-to-end.
        APPROVAL_FORWARD = "approval.forward"
        # PR 3.3 — non-blocking MCP discovery suggestion. Recorded when
        # the agent surfaces a Connect/Skip card via
        # ``suggest_mcp_connector``. Keeps the audit chain consistent
        # with PR 1.4 forwarded events; SIEM exports can correlate
        # discovery suggestions with subsequent ``mcp.auth.granted`` /
        # ``approval_decision_recorded`` rows when the user resolves.
        MCP_DISCOVERY_SUGGESTED = "mcp.discovery.suggested"
        # PR 1.4.1 — reasons recorded in audit metadata when a system
        # actor (the expiry sweeper) auto-rejects a pending approval.
        # Distinct values feed SIEM dashboards and operational queries.
        APPROVAL_REASON_EXPIRED = "expired"
        APPROVAL_REASON_RECIPIENT_REVOKED = "recipient_membership_revoked"
        # PR 1.2 — per-chat connector scope mutation; metadata captures
        # ``before`` / ``after`` / ``diff_keys`` for forensic replay.
        CONVERSATION_CONNECTORS_UPDATE = "conversation.connectors.update"
        # PR 1.6 — workspace defaults + conversation lifecycle audit.
        # ``WORKSPACE_DEFAULTS_UPDATE`` metadata cross-references the
        # ``retention_policies`` rows it inserted/updated via
        # ``retention_policy_ids`` so SIEM can chase one event back to
        # all the storage rows it affected.
        WORKSPACE_DEFAULTS_UPDATE = "workspace.defaults.update"
        CONVERSATION_UPDATE = "conversation.update"
        CONVERSATION_DELETE = "conversation.delete"
        CONVERSATION_RESTORE = "conversation.restore"
        # PR 4.3 — workspace behavior overrides + privacy / export.
        # ``WORKSPACE_BEHAVIOR_OVERRIDES_UPDATE`` carries the full
        # before/after blob (system_prompt_override, temperature, citation
        # density, refusal behavior, default_reasoning_effort,
        # training_data_opt_out). The dedicated ``training_opt_out`` row
        # is split out because compliance auditors search for the boolean
        # transition by action name without parsing JSONB diffs.
        WORKSPACE_BEHAVIOR_OVERRIDES_UPDATE = "workspace.behavior_overrides.update"
        WORKSPACE_TRAINING_OPT_OUT_UPDATE = "workspace.training_opt_out.update"
        # ``WORKSPACE_EXPORT_REQUEST`` audits a queued export — v1 ships
        # the audit row + 202; the actual export pipeline lands later.
        WORKSPACE_EXPORT_REQUEST = "workspace.export.request"
        # ``WORKSPACE_DELETE_ATTEMPT`` audits a delete-all-data attempt
        # even though v1 returns 501; we record the typed-confirmation
        # correctness so a forensic reader sees who is asking and how
        # they answered the confirm gate.
        WORKSPACE_DELETE_ATTEMPT = "workspace.delete_attempt"

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
