"""Shared business logic for runtime API adapter stores.

Extracted from the in-memory and Postgres stores to eliminate duplication
and ensure consistent behaviour across backends.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from runtime_api.schemas import (
    AgentRunStatus,
    ConversationRecord,
    CreateRunRequest,
    MessageRecord,
    MessageRole,
)

MessageLookupFn = Callable[[str], MessageRecord | None]
LatestMessageIdFn = Callable[[str, str], str | None]
LatestAssistantForRunFn = Callable[[str, str, str], str | None]


class _Fields:
    """Constants for metadata and dict keys used across adapters."""

    ACTOR_TYPE = "actor_type"
    APPROVAL_ID = "approval_id"
    AUDIT_EVENT_ID = "audit_event_id"
    BRANCH = "branch"
    BRANCH_ID = "branch_id"
    COMMAND_ID = "command_id"
    COMMAND_TYPE = "command_type"
    CONVERSATIONS_ARCHIVED = "conversations_archived"
    DELETED_AT = "deleted_at"
    EVENTS_RETAINED = "events_retained"
    MESSAGE = "message"
    MESSAGES_TOMBSTONED = "messages_tombstoned"
    METADATA = "metadata"
    ORG_ID = "org_id"
    OUTCOME = "outcome"
    QUOTE = "quote"
    REASON = "reason"
    RECORD = "record"
    REGENERATE_FROM_MESSAGE_ID = "regenerate_from_message_id"
    RESOURCE_ID = "resource_id"
    RESOURCE_TYPE = "resource_type"
    RISK_LEVEL = "risk_level"
    RUN_ID = "run_id"
    RUNS_CANCELLED = "runs_cancelled"
    SOURCE_MESSAGE_ID = "source_message_id"
    TRACE_ID = "trace_id"
    USER_ID = "user_id"


class StatusTransition:
    """Shared run status-transition timestamp logic.

    Both the in-memory and Postgres stores apply the same rules for which
    timestamp columns to set when a run status changes.  This class
    centralises that logic so it is defined exactly once.
    """

    TERMINAL_STATUSES = frozenset(
        {
            AgentRunStatus.CANCELLED,
            AgentRunStatus.COMPLETED,
            AgentRunStatus.FAILED,
            AgentRunStatus.TIMED_OUT,
        }
    )

    _COMPLETION_STATUSES = frozenset(
        {
            AgentRunStatus.COMPLETED,
            AgentRunStatus.FAILED,
            AgentRunStatus.TIMED_OUT,
        }
    )

    @classmethod
    def timestamp_updates(
        cls,
        new_status: AgentRunStatus,
        *,
        already_started: bool,
    ) -> dict[str, datetime]:
        """Return timestamp field names and values for a status transition."""

        updates: dict[str, datetime] = {}
        now = datetime.now(timezone.utc)
        if new_status == AgentRunStatus.RUNNING and not already_started:
            updates["started_at"] = now
        if new_status in cls._COMPLETION_STATUSES:
            updates["completed_at"] = now
        if new_status == AgentRunStatus.CANCELLED:
            updates["cancelled_at"] = now
        return updates


class RuntimeAdapterHelpers:
    """Shared helpers for building messages and normalising data.

    All former module-level functions are collected here so that no
    standalone functions exist outside a class.
    """

    SYNTHETIC_ASSISTANT_MESSAGE_PREFIX = "assistant-"

    @staticmethod
    def timestamp_ns(dt: datetime) -> int:
        """Convert a datetime to a nanosecond POSIX timestamp."""

        return int(dt.timestamp() * 1_000_000_000)

    @staticmethod
    def message_metadata(request: CreateRunRequest) -> dict[str, object]:
        """Build the metadata dict for a user message created from a run request."""

        metadata: dict[str, object] = {}
        quote = request.quote_payload()
        if quote is not None:
            metadata[_Fields.QUOTE] = quote
        if request.source_message_id is not None:
            metadata[_Fields.SOURCE_MESSAGE_ID] = request.source_message_id
        if request.branch_id is not None:
            metadata[_Fields.BRANCH_ID] = request.branch_id
        branch = request.branch_payload()
        if branch is not None:
            metadata[_Fields.BRANCH] = branch
        if request.regenerate_from_message_id is not None:
            metadata[_Fields.REGENERATE_FROM_MESSAGE_ID] = (
                request.regenerate_from_message_id
            )
        return metadata

    @staticmethod
    def regenerated_user_message(
        message_id: str,
        *,
        get_message: MessageLookupFn,
    ) -> MessageRecord | None:
        """Walk from *message_id* up to its user-role origin for regeneration."""

        message = get_message(message_id)
        if message is None:
            return None
        if message.role == MessageRole.USER:
            return message
        if message.parent_message_id is None:
            return None
        parent = get_message(message.parent_message_id)
        if parent is None or parent.role != MessageRole.USER:
            return None
        return parent

    @classmethod
    def real_assistant_message_id_for_synthetic_parent(
        cls,
        *,
        parent_message_id: str,
        org_id: str,
        conversation_id: str,
        find_latest_assistant_for_run: LatestAssistantForRunFn,
    ) -> str | None:
        """Resolve a synthetic ``assistant-<run_id>`` parent to a real message ID."""

        if not parent_message_id.startswith(cls.SYNTHETIC_ASSISTANT_MESSAGE_PREFIX):
            return None
        run_id = parent_message_id.removeprefix(cls.SYNTHETIC_ASSISTANT_MESSAGE_PREFIX)
        return find_latest_assistant_for_run(org_id, conversation_id, run_id)

    @classmethod
    def parent_message_id_for_run_request(
        cls,
        *,
        request: CreateRunRequest,
        org_id: str,
        conversation_id: str,
        get_latest_message_id: LatestMessageIdFn,
        find_latest_assistant_for_run: LatestAssistantForRunFn,
    ) -> str | None:
        """Determine the parent message ID for a new run request."""

        parent_message_id = request.parent_message_id
        if parent_message_id is None:
            return get_latest_message_id(org_id, conversation_id)
        return (
            cls.real_assistant_message_id_for_synthetic_parent(
                parent_message_id=parent_message_id,
                org_id=org_id,
                conversation_id=conversation_id,
                find_latest_assistant_for_run=find_latest_assistant_for_run,
            )
            or parent_message_id
        )

    @classmethod
    def message_for_run_request(
        cls,
        *,
        request: CreateRunRequest,
        conversation: ConversationRecord,
        get_message: MessageLookupFn,
        get_latest_message_id: LatestMessageIdFn,
        find_latest_assistant_for_run: LatestAssistantForRunFn,
        run_id_for_message: str | None = None,
    ) -> MessageRecord:
        """Build or retrieve the user message for a run request.

        Storage-specific lookups are injected via callbacks so both in-memory
        and Postgres stores can share this orchestration logic.
        """

        context = request.runtime_context
        if request.regenerate_from_message_id is not None:
            regen = cls.regenerated_user_message(
                request.regenerate_from_message_id,
                get_message=get_message,
            )
            if regen is not None:
                return regen
        parent_id = cls.parent_message_id_for_run_request(
            request=request,
            org_id=conversation.org_id,
            conversation_id=conversation.conversation_id,
            get_latest_message_id=get_latest_message_id,
            find_latest_assistant_for_run=find_latest_assistant_for_run,
        )
        return MessageRecord(
            conversation_id=conversation.conversation_id,
            org_id=conversation.org_id,
            run_id=run_id_for_message,
            role=MessageRole.USER,
            content_text=request.user_input,
            content_format=request.content_format,
            content=tuple(
                part.model_dump(
                    mode="json",
                    exclude_none=True,
                    exclude_defaults=True,
                )
                for part in request.content
            ),
            attachments=tuple(
                attachment.model_dump(
                    mode="json",
                    exclude_none=True,
                    exclude_defaults=True,
                )
                for attachment in request.attachments
            ),
            quote=request.quote_payload(),
            metadata=cls.message_metadata(request),
            parent_message_id=parent_id,
            source_message_id=request.source_message_id,
            branch_id=request.branch_id,
            trace_id=context.trace_id,
        )

    @staticmethod
    def normalize_risk_class(metadata: dict[str, object]) -> str:
        """Normalize ``risk_level`` metadata to a safe risk class value."""

        risk_class = str(metadata.get(_Fields.RISK_LEVEL) or "low").lower()
        if risk_class == "critical":
            risk_class = "high"
        if risk_class not in {"low", "medium", "high"}:
            risk_class = "low"
        return risk_class

    @staticmethod
    def derive_action_summary(metadata: dict[str, object]) -> str:
        """Derive a human-readable action summary from approval metadata."""

        return str(
            metadata.get(_Fields.MESSAGE)
            or metadata.get(_Fields.REASON)
            or "Approve this runtime action."
        )
