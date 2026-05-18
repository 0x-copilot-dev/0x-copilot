"""Stable string constants, display messages, and compiled validators for the runtime API.

Centralises every key, status value, route name, and client-visible message so callers
never inline bare string literals. Changes to wire-level names are made here once.
"""

from __future__ import annotations

import re


class Keys:
    """Namespaced string keys for API contracts, stores, events, and transport adapters."""

    class Field:
        """Payload and record field names used across persistence, events, and HTTP responses."""

        AFTER_SEQUENCE = "after_sequence"
        API_EVENT_TYPE = "api_event_type"
        APPROVAL_ID = "approval_id"
        APPROVAL_KIND = "approval_kind"
        # PR #43 — ApprovalBatch fields projected onto every approval_requested
        # and approval_resolved event so the FE can group cards by batch and
        # a future PR can add an "approve all" affordance.
        BATCH_ID = "batch_id"
        BATCH_INDEX = "batch_index"
        # Two-stage approval forwarding bookkeeping fields.
        ACTION_SUMMARY = "action_summary"
        CHAIN_PARENT_APPROVAL_ID = "chain_parent_approval_id"
        FORWARD_TO = "forward_to"
        FORWARDED_AT = "forwarded_at"
        FORWARDED_BY_USER_ID = "forwarded_by_user_id"
        FORWARDED_TO_USER_ID = "forwarded_to_user_id"
        # Non-blocking MCP discovery payload fields. Presence of ``DISCOVERY_REASON``
        # flips the event from a blocking auth gate to a Connect/Skip suggestion.
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
        """Top-level keys used specifically within event payload dicts."""

        DELTA = "delta"
        DISPLAY_TITLE = "display_title"
        MESSAGE = "message"
        REASON = "reason"
        SUMMARY = "summary"

    class Query:
        """Query-string parameter names for list and stream endpoints."""

        AFTER_SEQUENCE = "after_sequence"
        LIMIT = "limit"
        ORG_ID = "org_id"
        USER_ID = "user_id"

    class RouteName:
        """FastAPI route ``name=`` values used for URL reverse-lookup."""

        APPROVAL_DECISION = "approval_decision"
        # Undo within the 60 s reversibility window.
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
        # Recipient inbox endpoint + per-user SSE channel.
        LIST_APPROVALS = "list_approvals"
        STREAM_INBOX = "stream_inbox"
        UPDATE_CONVERSATION_CONNECTORS = "update_conversation_connectors"
        # Usage endpoints.
        USAGE_ME = "usage_me"
        USAGE_ME_CONVERSATIONS = "usage_me_conversations"
        USAGE_RUN = "usage_run"
        USAGE_CONVERSATION = "usage_conversation"
        USAGE_ORG = "usage_org"
        USAGE_ORG_SUBAGENTS = "usage_org_subagents"
        USAGE_ORG_PURPOSE = "usage_org_purpose"
        USAGE_ORG_AGENT = "usage_org_agent"
        # Budget endpoints.
        BUDGETS_LIST = "budgets_list"
        BUDGETS_CREATE = "budgets_create"
        BUDGETS_UPDATE = "budgets_update"
        BUDGETS_DELETE = "budgets_delete"
        BUDGETS_ME = "budgets_me"
        # Draft endpoints.
        LIST_DRAFTS = "list_drafts"
        GET_DRAFT = "get_draft"
        PATCH_DRAFT = "patch_draft"
        SEND_DRAFT = "send_draft"
        DISCARD_DRAFT = "discard_draft"
        # Workspace pane feed endpoints.
        LIST_SUBAGENTS = "list_subagents"
        LIST_SOURCES = "list_sources"
        # Retention admin endpoints.
        RETENTION_LIST = "retention_list"
        RETENTION_UPSERT = "retention_upsert"
        RETENTION_DELETE = "retention_delete"
        # Read-only effective TTL view exposed to tenant members (Privacy & data settings).
        RETENTION_EFFECTIVE = "retention_effective"
        # Workspace defaults + conversation lifecycle endpoints.
        GET_WORKSPACE_DEFAULTS = "get_workspace_defaults"
        UPDATE_WORKSPACE_DEFAULTS = "update_workspace_defaults"
        UPDATE_CONVERSATION = "update_conversation"
        DELETE_CONVERSATION = "delete_conversation"
        RESTORE_CONVERSATION = "restore_conversation"
        # Workspace data lifecycle endpoints (export queue + audited delete-all).
        REQUEST_WORKSPACE_EXPORT = "request_workspace_export"
        DELETE_WORKSPACE_DATA = "delete_workspace_data"
        # Recipient forks a shared chat into their own workspace.
        FORK_SHARE = "fork_share"
        # Owner forks their own conversation from a message ("Retry from here").
        # Same target row shape as the share-fork path; difference is in
        # source-side validation (own-org only, no share token).
        FORK_CONVERSATION = "fork_conversation"


class Values:
    """Stable scalar values and nested enumerations for the API layer."""

    EVENT_PROTOCOL_VERSION = 1
    SCHEMA_VERSION = 1
    DEFAULT_ASSISTANT_ID = "default"
    DEFAULT_CONTENT_FORMAT = "text"
    DEFAULT_CONVERSATION_LIMIT = 30
    DEFAULT_MESSAGE_LIMIT = 50
    MAX_MESSAGE_LIMIT = 200
    SSE_EVENT_NAME = "runtime_event"
    # Sentinel actor for system-driven approval rejections (expiry sweeper,
    # membership-revocation cascade). The audit emitter records ``actor_type=system``
    # so SIEM exports distinguish background-driven from operator-driven rejections.
    SYSTEM_USER_ID = "system:runtime"
    DEFAULT_ASSIGNED_APPROVAL_LIMIT = 50
    MAX_ASSIGNED_APPROVAL_LIMIT = 200

    class Status:
        """Wire-level status label strings emitted in event payloads."""

        ANSWERED = "answered"
        CANCELLED = "cancelled"
        COMPLETED = "completed"
        FAILED = "failed"
        # Wire-level status emitted on APPROVAL_RESOLVED for the parent of a
        # forwarded chain. Distinguishes "forwarded on" from approve/reject so
        # the frontend renders a "Waiting on @..." pill instead of a resolved record.
        FORWARDED = "forwarded"
        QUEUED = "queued"
        RUNNING = "running"
        SKIPPED = "skipped"
        STARTED = "started"
        WAITING = "waiting"

    class Tool:
        """Tool name strings as seen in event payloads and capability registrations."""

        ASK_A_QUESTION = "ask_a_question"
        GREP = "grep"
        READ_FILE = "read_file"
        RG = "rg"
        SEARCH_FILES = "search_files"
        # Non-blocking MCP discovery tool. Agent calls this when an unauthenticated
        # MCP server would improve the answer; emits ``mcp_auth_required`` with
        # ``discovery_reason`` so the frontend renders a Connect/Skip card without
        # pausing the run.
        SUGGEST_MCP_CONNECTOR = "suggest_mcp_connector"
        TASK = "task"
        UNKNOWN_TOOL = "unknown_tool"
        WRITE_TODOS = "write_todos"

    class ApprovalKind:
        """Approval kind discriminators stored in ``ApprovalRequestRecord.metadata``."""

        ACTION = "action"
        ASK_A_QUESTION = "ask_a_question"
        MCP_AUTH = "mcp_auth"
        MCP_TOOL = "mcp_tool"

    class VirtualPath:
        """Virtual filesystem path prefixes for oversized payloads stored by-reference."""

        LARGE_TOOL_RESULTS_PREFIX = "/large_tool_results/"


class Patterns:
    """Pre-compiled regular expressions for validating API IDs and slugs."""

    ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class Messages:
    """Centralised safe messages returned to API clients, audit rows, and event payloads.

    All strings here are considered public — they may appear in HTTP response bodies
    or structured logs visible to operators. Never embed internal detail.
    """

    class Error:
        """User-facing error strings for HTTP 4xx responses."""

        APPROVAL_NOT_FOUND = "Approval request was not found for this scope."
        # Forwarding-target validation messages are deliberately generic and do not
        # reveal whether the target user exists in another tenant.
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
        # Workspace defaults model validation. Messages stay generic (no leaking
        # catalog membership rules) and pair with 422 for field-level FE rendering.
        UNKNOWN_MODEL_PROVIDER = "Default model provider is not in the catalog."
        UNKNOWN_MODEL_NAME = "Default model name is not in the catalog."

    class Audit:
        """Action-name strings written to audit log rows."""

        # Canonical approval verbs (cross-audit §2.2). Wire nouns stay past-
        # tense ("approved"/"rejected") on ``ApprovalDecision``; audit verbs
        # are imperative ``approval.<verb>`` for SIEM compatibility.
        APPROVAL_ACCEPT = "approval.accept"
        APPROVAL_REJECT = "approval.reject"
        # Append-only audit action for the forward link. Records the act of forwarding
        # with ``chain_parent_approval_id`` metadata so SIEM exports can reconstruct
        # chains end-to-end.
        APPROVAL_FORWARD = "approval.forward"
        # P1-A re-scoped — suggest-edit verb. Pairs with the new
        # ``ApprovalDecision.SUGGEST_EDIT`` flow; metadata carries the
        # parent approval id, child approval id, and the edited payload
        # keys (values are persisted under metadata.edited_payload so SIEM
        # can audit the diff that was suggested).
        APPROVAL_SUGGEST_EDIT = "approval.suggest_edit"
        APPROVAL_UNDO = "approval.undo"
        # Non-blocking MCP discovery suggestion audit action. Recorded when the agent
        # surfaces a Connect/Skip card. Correlatable with subsequent
        # ``mcp.auth.granted`` / ``approval.accept`` / ``approval.reject`` rows when the user resolves.
        MCP_DISCOVERY_SUGGESTED = "mcp.discovery.suggested"
        # Reasons recorded in audit metadata when a system actor auto-rejects a
        # pending approval. Distinct values feed SIEM dashboards and operational queries.
        APPROVAL_REASON_EXPIRED = "expired"
        APPROVAL_REASON_RECIPIENT_REVOKED = "recipient_membership_revoked"
        # Per-chat connector scope mutation; metadata captures ``before`` / ``after``
        # / ``diff_keys`` for forensic replay.
        CONVERSATION_CONNECTORS_UPDATE = "conversation.connectors.update"
        # Workspace defaults + conversation lifecycle audit. ``WORKSPACE_DEFAULTS_UPDATE``
        # metadata cross-references the ``retention_policies`` rows it inserted/updated
        # via ``retention_policy_ids`` so SIEM can chase one event back to all affected rows.
        WORKSPACE_DEFAULTS_UPDATE = "workspace.defaults.update"
        CONVERSATION_UPDATE = "conversation.update"
        CONVERSATION_DELETE = "conversation.delete"
        CONVERSATION_RESTORE = "conversation.restore"
        # Workspace behavior overrides audit. The ``WORKSPACE_BEHAVIOR_OVERRIDES_UPDATE``
        # action carries the full before/after blob. ``WORKSPACE_TRAINING_OPT_OUT_UPDATE``
        # is split out so compliance auditors can search by action name without
        # parsing JSONB diffs.
        WORKSPACE_BEHAVIOR_OVERRIDES_UPDATE = "workspace.behavior_overrides.update"
        WORKSPACE_TRAINING_OPT_OUT_UPDATE = "workspace.training_opt_out.update"
        # Queued export audit row — ships the audit record; the actual export pipeline
        # is a follow-up.
        WORKSPACE_EXPORT_REQUEST = "workspace.export.request"
        # Delete-all-data attempt audit — recorded even when the endpoint returns 501
        # so a forensic reader sees who requested deletion and how they answered the
        # confirmation gate.
        WORKSPACE_DELETE_ATTEMPT = "workspace.delete_attempt"

    class Event:
        """Human-readable messages and title factories for runtime stream events."""

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
            """Return the display title for a subagent lifecycle event."""
            return f"{subagent_name} subagent"

        @classmethod
        def tool_completed_title(cls, tool_name: str) -> str:
            """Return the display title for a completed tool call."""
            return f"{tool_name} completed"

        @classmethod
        def tool_result_title(cls, tool_name: str) -> str:
            """Return the display title for a tool result event."""
            return f"{tool_name} result"

        @classmethod
        def tool_running_title(cls, tool_name: str) -> str:
            """Return the display title while a tool call is in flight."""
            return f"{tool_name} running"

        @classmethod
        def tool_started_title(cls, tool_name: str) -> str:
            """Return the display title at the moment a tool call begins."""
            return f"Calling {tool_name}"

        @classmethod
        def source_cited_title(cls, title: str) -> str:
            """Return the display title for a single cited source."""
            return f"Cited {title}"

        SOURCE_INGESTED = "Cited a source"

        @classmethod
        def sources_cited_title(cls, count: int) -> str:
            """Return the pluralised display title for a multi-source citation event."""
            if count == 1:
                return "Cited 1 source"
            return f"Cited {count} sources"

        SOURCES_INGESTED = "Cited sources"

        @classmethod
        def citation_made_title(cls, ordinal: int) -> str:
            """Return the display title for a model-declared citation pointer.

            The ordinal maps to a specific tool-call invocation; the frontend
            resolves the chip to that tool call in its event registry.
            """
            return f"Cited tool call #{ordinal}"

        CITATION_MADE = "Cited a tool call"

    class Validation:
        """Field-level validation error message factories for Pydantic validators."""

        @classmethod
        def id_contains_unsupported_characters(cls, field_name: str) -> str:
            """Return an error for an ID field that fails the ``Patterns.ID`` check."""
            return f"{field_name} contains unsupported characters"

        @classmethod
        def nonempty_string(cls, field_name: str) -> str:
            """Return an error for an empty string where a non-empty value is required."""
            return f"{field_name} must not be empty"

        @classmethod
        def stable_slug(cls, field_name: str) -> str:
            """Return an error for a field that must match ``Patterns.SLUG``."""
            return f"{field_name} must be a stable slug"

        @classmethod
        def string_required(cls, field_name: str) -> str:
            """Return an error when a field's value must be a string but is not."""
            return f"{field_name} must be a string"
